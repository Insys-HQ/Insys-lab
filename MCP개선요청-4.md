# ERP MCP 개선요청-4 — 개발사 요청 통합본 v1.0

- 작성: 인시스 AI혁신 연구개발실 (ys.kim@in-sys.co.kr)
- 작성일: 2026-11-27
- 참고 문서: [개선요청-1 권한배정](https://insys-hq.github.io/Insys-lab/ERP_MCP_%EA%B0%9C%EC%84%A0%EC%9A%94%EC%B2%AD_%EA%B6%8C%ED%95%9C%EB%B0%B0%EC%A0%95_v1.0.html) · [개선요청-2 현장·배치](https://insys-hq.github.io/Insys-lab/MCP%EA%B0%9C%EC%84%A0%EC%9A%94%EC%B2%AD-2.html) · [개선요청-3 부서 간 연계](https://insys-hq.github.io/Insys-lab/MCP%EA%B0%9C%EC%84%A0%EC%9A%94%EC%B2%AD-3.html)

> **요약**: ERP 작업이 MCP가 아니라 브라우저로 처리되는 원인은 **쓰기 도구가 없기 때문**입니다. 긴급 데이터·결함 4건, MCP 쓰기 도구 12종, 조회 보강 3건, 운영·보안 5건을 한 번에 요청합니다. 모든 쓰기는 기존과 같은 **propose → confirm** 방식으로 요청합니다.

---

## 1. 긴급 (데이터·결함)

| No | 요청 | 내용 |
|---|---|---|
| U1 | **SITE-083 소속 회사 수정** | 「셰플러 창원3공장(파인)」 소속 회사 (주)인시스 → **(주)파인 창원본사** (DB/Studio). GPS 출퇴근 확대 일정에 직접 영향 |
| U2 | 현장 등록 시 회사 자동 지정 결함 | 등록자 소속 회사로 강제 저장됨. 등록·수정 화면에 「소속 회사」 칸 제공 |
| U3 | 표 수정의 「회사」 칸 | 입력되는 것처럼 보이지만 저장 안 됨 → 읽기 전용 표시 |
| U4 | 다른 회사 현장으로 배치 이동 | 선택 목록에서 사라지기만 함 → 「소속 회사가 달라 이동 불가」 안내 |

## 2. MCP 쓰기 도구 추가 (모두 propose → confirm 방식)

| 우선 | 도구 | 대상 | 근거 문서 |
|---|---|---|---|
| ★1 | `propose_site_upsert` | 현장 등록·수정(소속 회사 포함) | 개선요청-2 |
| ★1 | `propose_line_upsert` / `propose_line_delete` | 라인·구역 | 개선요청-2 |
| ★1 | `propose_placement_move` | 배치 이동(여러 명, 시작일 유지) | 개선요청-2 |
| ★1 | `propose_work_request` · `propose_request_comment` | 부서 간 업무 요청·댓글 | 개선요청-3 |
| ★1 | `list_requests_for_me` / `list_requests_by_me` | 받은·보낸 요청 조회 | 개선요청-3 |
| ★1 | **`propose_board_post`** (신규) | 게시판 글 등록(게시판 선택, 제목·본문, 중요 표시) | 이번 요청 |
| ★2 | `propose_field_attendance_setup` | 출퇴근 켜기, 반경, 근무시간, 담당자 | 개선요청-2 |
| ★2 | `propose_request_status` · `propose_request_assign` | 요청 상태·담당자 변경 | 개선요청-3 |
| ★2 | `propose_role_assignment` + 조회 2종 | 기능 카탈로그 담당자·회사 범위 배정 | 개선요청-1 |
| ★2 | `propose_record_trash` / `propose_record_restore` | 휴지통 이동·복구(영구 삭제 제외) | 개선요청-2 |
| ★3 | `export_table` | 대량 목록 CSV/XLSX 내려받기(권한 범위 안) | 개선요청-2 |
| ★3 | `propose_send_attendance_link` | 현장 근로자 출퇴근 링크 문자 | 개선요청-2 |

**미리보기 경고 공통:** 소속 회사 불일치, 중복 등록 의심, 배치·근태 기록이 있는 항목 삭제, 대량 변경(30명 이상), 민감정보 포함

### `propose_board_post` 상세 (신규)

| 항목 | 내용 |
|---|---|
| 입력 | 게시판(이름 또는 코드), 제목, 본문(마크다운 또는 HTML), 중요 표시 여부, 첨부 링크 |
| 미리보기 | 게시판 이름, 제목, 본문 렌더링, 공개 범위(볼 수 있는 사람), 중요 표시 여부 |
| 권한 | 연결한 직원이 화면에서 글을 쓸 수 있는 게시판만 |
| 처리 | confirm 시 화면 등록과 같은 알림·후처리. 수정·삭제는 1차 범위 제외 |

## 3. MCP 조회 보강

| No | 요청 |
|---|---|
| Q1 | `staffing_site`에 **company_id(소속 회사)** 칸 노출, `staffing_line`·근무지 표 조회 허용 |
| Q2 | 휴지통 레코드 조회 옵션 (`include_trashed`) |
| Q3 | 역할 배정·기능 카탈로그 표 조회 허용 |

## 4. 운영·보안

| No | 요청 |
|---|---|
| S1 | 감사 로그에 **클라이언트 구분**(Claude / ChatGPT / ERP 비서)과 user_request·변경 전후 값 기록 |
| S2 | 새 쓰기 도구의 **AI 허용 설정 위치** 안내 (Studio ▸ AI 정책 등)와 표별 on/off |
| S3 | 하루 실행 상한(예: 제안 20건, 배치 50명/회) 테넌트 설정 |
| S4 | **조회 전용 「그룹 지출 조회」 역할 템플릿** 제공 가능 여부 (전 계열사 카드·전표·통장·세금계산서·지출결의서 읽기 전용 → 기능 카탈로그에서 배정) |
| S5 | 도구 배포 시 공지 → ChatGPT 플러그인 재게시·Claude 재연결 시점 안내 |

## 5. 회신 요청

1. U1 처리 일정 (긴급)
2. U2~U4 결함 수정 일정
3. 2장 ★1 도구 제공 가능 여부·일정, 기존 도구(`list_my_requests`·`propose_todo_plan` 등) 확장으로 대신할 수 있는 항목
4. S2 AI 쓰기 허용 설정 위치, S4 조회 전용 역할 가능 여부

*v1.0 — 2026-11-27 최초 작성*
