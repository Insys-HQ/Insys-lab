import { http } from '@google-cloud/functions-framework';
import admin from 'firebase-admin';
import Anthropic from '@anthropic-ai/sdk';

if (!admin.apps.length) {
  admin.initializeApp({ storageBucket: 'insys-work.firebasestorage.app' });
}

const ANTHROPIC_API_KEY = process.env.ANTHROPIC_API_KEY;

const STAGES = ['철거', '기초', '골조', '외부마감', '내부마감', '준공'];

const ANALYSIS_PROMPT = `당신은 기업 화재복구 TF를 지원하는 법무 검토 보조자입니다.
첨부된 변호사 의견서를 검토하여 아래 형식으로 한국어로 정리해주세요. 실제 법률 자문을 대체하지 않으며, TF 내부 참고용 요약임을 전제로 작성합니다.

1. 핵심 쟁점 (2~4개, 각 1~2문장)
2. 변호사의 권고 조치사항 (실행 가능한 항목 위주)
3. TF가 놓치면 안 되는 기한/데드라인 (있는 경우만)
4. 리스크 및 유의사항
5. 관련 트랙(화재보상/지자체지원/고객사보상/공장신축 중 해당하는 것)

각 항목은 간결하게, 불필요한 서론 없이 작성하세요.`;

const PROGRESS_PROMPT = `아래는 공장신축 현장의 카톡 대화 또는 사진 메모 텍스트입니다.
텍스트에서 아래 6개 공정단계 중 하나에 대한 진행 상태 언급을 찾아 진도율(%)로 변환해주세요.
공정단계는 반드시 다음 중 하나여야 합니다: 철거, 기초, 골조, 외부마감, 내부마감, 준공.

변환 기준:
- 명시적인 숫자가 있으면 그 숫자를 그대로 사용 (예: "골조 70% 완료" → 70)
- "시작", "착수", "시작함", "들어감" 등 이제 막 시작했다는 표현 → 5
- "완료", "끝", "끝남", "마감", "종료" 등 그 단계가 끝났다는 표현 → 100
- "절반", "반 정도", "중간쯤" → 50
- 그 외 막연한 표현("진행중이다", "하고 있음", "작업 중")만 있고 시작/완료/숫자 근거가 없으면 무시하세요.
- 한 텍스트에 여러 단계가 언급되면 각각 따로 추출하세요.

반드시 아래 JSON 형식으로만 응답하세요. 다른 텍스트, 설명, 마크다운 코드블록 없이 순수 JSON만 출력합니다.
{"extractions": [{"stage": "철거", "progress": 5, "reason": "철거 시작이라는 언급"}]}
언급이 전혀 없으면: {"extractions": []}

텍스트:
`;

function withCors(res) {
  res.set('Access-Control-Allow-Origin', '*');
  res.set('Access-Control-Allow-Methods', 'POST, OPTIONS');
  res.set('Access-Control-Allow-Headers', 'Content-Type');
}

async function handleLegalAnalysis(req, res) {
  const docId = req.body.docId;
  const db = admin.firestore();
  const bucket = admin.storage().bucket();

  const docSnap = await db.collection('legalOpinions').doc(docId).get();
  if (!docSnap.exists) { res.status(404).json({ error: '문서를 찾을 수 없습니다.' }); return; }
  const docData = docSnap.data();

  const [fileBuffer] = await bucket.file(docData.storagePath).download();
  const base64Data = fileBuffer.toString('base64');

  const filename = docData.filename || '';
  const isPdf = filename.toLowerCase().endsWith('.pdf');
  if (!isPdf) {
    res.json({ analysis: '현재 자동분석은 PDF 파일만 지원합니다. doc/docx 파일은 PDF로 변환 후 다시 업로드해주세요.' });
    return;
  }

  const client = new Anthropic({ apiKey: ANTHROPIC_API_KEY });
  const message = await client.messages.create({
    model: 'claude-sonnet-4-6',
    max_tokens: 1500,
    messages: [
      {
        role: 'user',
        content: [
          { type: 'document', source: { type: 'base64', media_type: 'application/pdf', data: base64Data } },
          { type: 'text', text: ANALYSIS_PROMPT }
        ]
      }
    ]
  });

  const analysisText = message.content.filter(b => b.type === 'text').map(b => b.text).join('\n');
  res.json({ analysis: analysisText });
}

async function handleProgressExtraction(req, res) {
  const text = (req.body.text || '').trim();
  if (!text) { res.json({ extractions: [] }); return; }

  const client = new Anthropic({ apiKey: ANTHROPIC_API_KEY });
  const message = await client.messages.create({
    model: 'claude-sonnet-4-6',
    max_tokens: 400,
    messages: [{ role: 'user', content: PROGRESS_PROMPT + text }]
  });

  const raw = message.content.filter(b => b.type === 'text').map(b => b.text).join('\n').trim();
  const cleaned = raw.replace(/^```json\s*|```$/g, '').trim();

  let parsed;
  try {
    parsed = JSON.parse(cleaned);
  } catch (e) {
    res.json({ extractions: [] });
    return;
  }

  const extractions = (parsed.extractions || []).filter(
    (ex) => STAGES.includes(ex.stage) && typeof ex.progress === 'number' && ex.progress >= 0 && ex.progress <= 100
  );
  res.json({ extractions });
}

http('analyzeLegalDoc', async (req, res) => {
  withCors(res);
  if (req.method === 'OPTIONS') { res.status(204).send(''); return; }
  if (req.method !== 'POST') { res.status(405).json({ error: 'POST만 허용됩니다.' }); return; }

  try {
    if (!ANTHROPIC_API_KEY) {
      res.status(500).json({ error: 'ANTHROPIC_API_KEY 환경변수가 설정되지 않았습니다.' });
      return;
    }

    if (req.body && req.body.action === 'extractProgress') {
      await handleProgressExtraction(req, res);
      return;
    }

    if (!req.body || !req.body.docId) {
      res.status(400).json({ error: 'docId가 필요합니다.' });
      return;
    }
    await handleLegalAnalysis(req, res);
  } catch (e) {
    console.error(e);
    res.status(500).json({ error: e.message || '서버 오류' });
  }
});
