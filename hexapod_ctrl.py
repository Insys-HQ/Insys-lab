#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
헥사포드 PCB 정렬 제어 프로그램  v1.0
인시스 AI혁신 연구개발실

기능 : ISL38-300 프로파일로 PCB 평면(기울기)·위치를 구하고,
       PCB 법선 방향 300mm 위(센서 기준)로 헥사포드를 구동한다.
구성 : LaserSensor(측정) -> fit_plane(평면) -> target_pose(목표자세)
       -> ik(레그 길이) -> check_limits(한계) -> trajectory(궤적)
       -> ActuatorDriver(구동) -> 재측정(검증) -> 카메라 트리거

좌표계 : Z 아래방향(+Z = PCB 쪽). 베이스판 중심이 원점, 베이스 조인트 Z=0.
         플랫폼 홈 위치 = (0,0,H0). 단위 mm / deg.
실행   : python hexapod_ctrl.py            (시뮬레이션 자체 검증)
         python hexapod_ctrl.py --n 300    (시험 횟수 지정)
의존   : numpy 만 필요
"""
import argparse
import math
import numpy as np

VERSION = "v1.0"


# ───────────────────────── 설정 ─────────────────────────
class Cfg:
    # 헥사포드 형상 (스트로크 해석 결과: 높이400 기준 형상)
    RB, RP = 100.0, 60.0          # 베이스/플랫폼 조인트 반경
    DB, DP = 13.0, 17.0           # 조인트 쌍 반각(deg)
    H0 = 400.0                    # 홈 높이(베이스~플랫폼 조인트면)
    # 액추에이터 (대성 사양 확정 전 가정값 → 사양 받으면 교체)
    L_MIN, STROKE = 340.0, 190.0  # 최단 길이, 스트로크
    V_MAX, A_MAX = 80.0, 400.0    # mm/s, mm/s^2
    # 측정/목표
    D_TOOL = 300.0 + 117.0        # 플랫폼 피벗 ~ PCB 면 (초점 300 + 센서 하단 117)
    TOL_TILT = 0.10               # deg
    TOL_DIST = 0.50               # mm (법선 방향 거리)
    TOL_XY = 1.00                 # mm
    MAX_ITER = 3                  # 재측정 보정 횟수
    XY_RADIUS = 150.0             # PCB 위치 허용 반경
    TILT_MAX = 15.0               # PCB 기울기 허용(deg)
    SCAN_Y = np.arange(-60, 60.1, 1.0)   # 프로파일 취득 위치(툴 Y 방향 mm, 컨베이어 엔코더 스캔으로 구현)


# ───────────────────────── 수학 유틸 ─────────────────────────
def rotvec_to_R(w):
    th = np.linalg.norm(w)
    if th < 1e-12:
        return np.eye(3)
    k = w / th
    K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + math.sin(th) * K + (1 - math.cos(th)) * K @ K


def R_to_rotvec(R):
    c = (np.trace(R) - 1) / 2
    th = math.acos(max(-1, min(1, c)))
    if th < 1e-12:
        return np.zeros(3)
    w = np.array([R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1]]) / (2 * math.sin(th))
    return w * th


def R_align_z(t):
    """툴 Z축을 단위벡터 t 로 보내는 최소 회전(요 0)."""
    z = np.array([0, 0, 1.0])
    v = np.cross(z, t)
    s = np.linalg.norm(v)
    if s < 1e-12:
        return np.eye(3)
    ang = math.atan2(s, float(z @ t))
    return rotvec_to_R(v / s * ang)


def tilt_deg(t):
    """툴 Z축과 수직(0,0,1)의 각도."""
    return math.degrees(math.acos(max(-1, min(1, t[2]))))


# ───────────────────────── 헥사포드 기구학 ─────────────────────────
class Hexapod:
    def __init__(self, cfg=Cfg):
        self.c = cfg
        a, b = [], []
        for k in range(3):
            bA = [120 * k + cfg.DB, 120 * (k + 1) - cfg.DB]
            pA = [120 * k + 60 - cfg.DP, 120 * k + 60 + cfg.DP]
            for j in range(2):
                ab, ap = math.radians(bA[j]), math.radians(pA[j])
                a.append([cfg.RB * math.cos(ab), cfg.RB * math.sin(ab), 0.0])
                b.append([cfg.RP * math.cos(ap), cfg.RP * math.sin(ap), 0.0])
        self.a, self.b = np.array(a), np.array(b)

    def ik(self, p, R):
        """플랫폼 위치 p, 회전 R -> 레그 길이 6개."""
        return np.linalg.norm((R @ self.b.T).T + p - self.a, axis=1)

    def fk(self, L, p0=None, R0=None, it=30):
        """레그 길이 -> 자세 (뉴턴-랩슨). 엔코더 값으로 현재 센서 자세 계산."""
        p = np.array([0, 0, self.c.H0]) if p0 is None else np.array(p0, float)
        R = np.eye(3) if R0 is None else R0.copy()
        for _ in range(it):
            e = self.ik(p, R) - L
            if np.max(np.abs(e)) < 1e-6:
                break
            J = np.zeros((6, 6))
            for i in range(6):
                d = np.zeros(6); d[i] = 1e-5
                pp, RR = p.copy(), R.copy()
                if i < 3:
                    pp += d[:3]
                else:
                    RR = rotvec_to_R(d[3:]) @ R
                J[:, i] = (self.ik(pp, RR) - self.ik(p, R)) / 1e-5
            dx = np.linalg.lstsq(J, -e, rcond=None)[0]
            p = p + dx[:3]
            R = rotvec_to_R(dx[3:]) @ R
        return p, R

    def jac_cond(self, p, R):
        """특이점 판정용 조건수 (클수록 위험)."""
        J = np.zeros((6, 6))
        for i in range(6):
            d = 1e-5
            pp, RR = p.copy(), R.copy()
            if i < 3:
                pp[i] += d
            else:
                w = np.zeros(3); w[i - 3] = d
                RR = rotvec_to_R(w) @ R
            J[:, i] = (self.ik(pp, RR) - self.ik(p, R)) / d
            if i >= 3:
                J[:, i] /= self.c.RP          # 회전 열을 mm 단위(호 길이)로 환산
        s = np.linalg.svd(J, compute_uv=False)
        return s[0] / max(s[-1], 1e-12)

    def check_limits(self, L):
        lo, hi = self.c.L_MIN, self.c.L_MIN + self.c.STROKE
        bad = [i + 1 for i, l in enumerate(L) if l < lo or l > hi]
        return (len(bad) == 0), bad


# ───────────────────────── 센서 / 구동 인터페이스 ─────────────────────────
class LaserSensor:
    """ISL38-300 인터페이스. 실장비는 이 클래스를 구현(Cognex 통신 프로토콜 사용)."""
    def grab_profile(self):
        """툴 좌표계(x, z) 프로파일 (N,2) 반환. y=0 슬라이스."""
        raise NotImplementedError


class ActuatorDriver:
    """대성 액추에이터 구동 인터페이스. 프로토콜(EtherCAT/CAN/RS485) 확정 후 구현."""
    def home(self): raise NotImplementedError
    def move_abs(self, L): raise NotImplementedError      # 6개 동기 지령
    def read(self): raise NotImplementedError             # 엔코더 레그 길이
    def stop(self): raise NotImplementedError


class SimWorld:
    """시뮬레이션: 실제 PCB 자세(숨김)·액추에이터·기구 오차(조인트 위치 ±0.05mm)를 모사."""
    def __init__(self, hexa, pcb_c, pcb_t, seed=0):
        self.hex = hexa                                  # 제어기가 아는 이상 형상
        self.true = Hexapod(hexa.c)                      # 실제 형상(조립 오차 포함)
        rng = np.random.default_rng(seed + 1000)
        self.true.a = hexa.a + rng.normal(0, 0.05, hexa.a.shape)
        self.true.b = hexa.b + rng.normal(0, 0.05, hexa.b.shape)
        self.pcb_c, self.pcb_t = np.array(pcb_c, float), np.array(pcb_t, float)
        self.rng = np.random.default_rng(seed)
        self.L = hexa.ik(np.array([0, 0, hexa.c.H0]), np.eye(3))
        self.res = 0.01                                  # 엔코더 분해능(mm)

    # --- ActuatorDriver ---
    def home(self):
        self.L = self.hex.ik(np.array([0, 0, self.hex.c.H0]), np.eye(3))
    def move_abs(self, L):
        self.L = np.round(np.asarray(L) / self.res) * self.res
    def read(self):
        return self.L.copy()
    def stop(self):
        pass

    # --- LaserSensor (툴 좌표계 프로파일) ---
    def true_pose(self):
        return self.true.fk(self.L)

    def grab_profile_at(self, dy, pose=None):
        """툴 Y 방향 dy 슬라이스 프로파일 (툴 좌표 x, y, z). 벡터화."""
        p, R = pose if pose is not None else self.true_pose()
        n_pcb = -self.pcb_t                              # PCB 면 법선(센서 쪽)
        x = np.arange(-120, 120.1, 1.0)                  # 시야폭 약 240mm (중거리 시야 278mm 가정)
        o = p + (R @ np.c_[x, np.full_like(x, dy), np.zeros_like(x)].T).T
        d = R @ np.array([0, 0, 1.0])
        den = float(n_pcb @ d)
        s_pcb = (self.pcb_c - o) @ n_pcb / den
        q = o + d * s_pcb[:, None]
        rel = q - self.pcb_c
        u = rel - (rel @ n_pcb)[:, None] * n_pcb
        e1 = np.cross(n_pcb, [0, 1, 0]); e1 /= np.linalg.norm(e1); e2 = np.cross(n_pcb, e1)
        on = (np.abs(u @ e1) <= 50) & (np.abs(u @ e2) <= 25)      # 판 100x50
        s_belt = ((self.pcb_c + self.pcb_t * 15.0) - o) @ n_pcb / den   # 벨트면(15mm 아래)
        z = np.where(on, s_pcb, s_belt) + self.rng.normal(0, 0.01, len(x))
        return np.c_[x, np.full_like(x, dy), z]


# ───────────────────────── 평면 추정 ─────────────────────────
def fit_plane_ransac(P, thr=0.4, iters=300, seed=1):
    """점군 P(N,3, 베이스 좌표) -> (중심, 법선(센서쪽), 인라이어 마스크).
    센서에 가장 가까운 평면(=PCB 상면)을 선택한다."""
    rng = np.random.default_rng(seed)
    best, best_key = None, None
    n = len(P)
    for _ in range(iters):
        i = rng.choice(n, 3, replace=False)
        v = np.cross(P[i[1]] - P[i[0]], P[i[2]] - P[i[0]])
        if np.linalg.norm(v) < 1e-9:
            continue
        v /= np.linalg.norm(v)
        m = np.abs((P - P[i[0]]) @ v) < thr
        if m.sum() < 0.2 * n:
            continue
        key = P[m][:, 2].mean()            # 작을수록(센서에서 멀리=위쪽?) 아님: Z-아래 프레임, PCB 상면이 더 작은 Z
        if best_key is None or key < best_key:
            best_key, best = key, m
    if best is None:
        raise RuntimeError("PCB 평면을 찾지 못함")
    m = best
    for _ in range(6):                      # 최소제곱 재적합 + 인라이어 재선별(편향 제거)
        Q = P[m]
        c = Q.mean(0)
        _, _, vt = np.linalg.svd(Q - c, full_matrices=False)
        nrm = vt[2]
        if nrm[2] > 0:                      # 센서 쪽(위, -Z)을 향하게
            nrm = -nrm
        m_new = np.abs((P - c) @ nrm) < thr
        if m_new.sum() < 10 or (m_new == m).all():
            break
        m = m_new
    return c, nrm, m


def measure_plane(world, hexa, cfg=Cfg):
    """프로파일 다중 취득 -> 툴 좌표계 평면 추정 -> 베이스 좌표로 변환.
    (스캔 방향 이동은 실장비에서 컨베이어 엔코더/스테이지로 구현)
    반환 : 툴좌표 (c_t, n_t) 와 베이스좌표 (c_b, n_b)"""
    p, R = hexa.fk(world.read())                  # 엔코더 -> 현재 센서 자세
    Pt = np.vstack([world.grab_profile_at(dy) for dy in cfg.SCAN_Y])
    c, n, m = fit_plane_ransac(Pt)
    Q = Pt[m]
    e1 = np.cross(n, [0, 1, 0]); e1 /= np.linalg.norm(e1)
    e2 = np.cross(n, e1)
    uv = np.c_[(Q - c) @ e1, (Q - c) @ e2]
    mid = (uv.min(0) + uv.max(0)) / 2             # PCB 외곽 중앙
    c_t = c + e1 * mid[0] + e2 * mid[1]
    return c_t, n, p + R @ c_t, R @ n


# ───────────────────────── 목표 자세 / 궤적 ─────────────────────────
def target_pose(center, n_up, cfg=Cfg):
    """PCB 중심·법선 -> 플랫폼 목표 (p, R). 센서는 PCB 직각 상단 D_TOOL 위."""
    t = -n_up                                # 툴 Z(PCB 쪽) = -법선(센서쪽)
    p = center - t * cfg.D_TOOL
    return p, R_align_z(t)


def plan_trajectory(hexa, p0, R0, p1, R1, dt=0.01, cfg=Cfg):
    """자세 공간 최소저크(quintic) 보간 -> 레그 길이 시퀀스. 6축 동기."""
    L0, L1 = hexa.ik(p0, R0), hexa.ik(p1, R1)
    dmax = np.max(np.abs(L1 - L0)) + 1e-9
    T = max(1.875 * dmax / cfg.V_MAX, math.sqrt(5.7735 * dmax / cfg.A_MAX), 0.2)
    rel = R_to_rotvec(R1 @ R0.T)
    seq = []
    for k in range(int(T / dt) + 1):
        tau = min(1.0, k * dt / T)
        s = 10 * tau**3 - 15 * tau**4 + 6 * tau**5
        seq.append(hexa.ik(p0 + (p1 - p0) * s, rotvec_to_R(rel * s) @ R0))
    return np.array(seq), T


# ───────────────────────── 제어기 (상태머신) ─────────────────────────
class AlignController:
    STATES = ["HOME", "MEASURE", "PLAN", "CHECK", "MOVE", "VERIFY", "CAPTURE", "DONE", "ALARM"]

    def __init__(self, world, cfg=Cfg, on_capture=None, log=None):
        self.w, self.cfg, self.hex = world, cfg, world.hex
        self.on_capture = on_capture or (lambda: None)
        self.log = log or (lambda *a: None)
        self.state = "HOME"

    @staticmethod
    def errors(c_t, n_t, cfg):
        """툴 좌표 측정값 -> (각도오차 deg, 법선거리오차 mm, 횡오차 mm). 센서 자체 측정이라 기구 오차 무관."""
        t = np.array([0, 0, 1.0])
        ang = math.degrees(math.acos(max(-1, min(1, float(t @ (-n_t))))))
        dist = float(c_t @ (-n_t))
        lat = float(np.linalg.norm(c_t - c_t @ (-n_t) * (-n_t)))
        return ang, dist - cfg.D_TOOL, lat

    def run_cycle(self):
        c, hx = self.cfg, self.hex
        self.state = "MEASURE"
        for it in range(1, c.MAX_ITER + 1):
            c_t, n_t, c_b, n_b = measure_plane(self.w, hx, c)
            # 이미 허용오차 이내면 이동 없이 촬영
            ang, de, lat = self.errors(c_t, n_t, c)
            if it > 1 or not (ang <= c.TOL_TILT and abs(de) <= c.TOL_DIST and lat <= c.TOL_XY):
                tilt = tilt_deg(-n_b)
                if tilt > c.TILT_MAX + 0.3 or math.hypot(c_b[0], c_b[1]) > c.XY_RADIUS + 1.0:
                    self.state = "ALARM"
                    return dict(ok=False, why=f"범위 초과 tilt={tilt:.1f}°")
                p1, R1 = target_pose(c_b, n_b, c)
                self.state = "PLAN"
                p0, R0 = hx.fk(self.w.read())
                L_now = self.w.read()
                L1 = L_now + (hx.ik(p1, R1) - hx.ik(p0, R0))     # 증분 지령: 기구 오차 보정
                self.state = "CHECK"
                ok, bad = hx.check_limits(L1)
                if not ok:
                    self.state = "ALARM"
                    return dict(ok=False, why=f"스트로크 초과 레그 {bad}", L=L1)
                if hx.jac_cond(p1, R1) > 80:
                    self.state = "ALARM"
                    return dict(ok=False, why="특이점 근접")
                seq, T = plan_trajectory(hx, p0, R0, p1, R1, cfg=c)
                self.state = "MOVE"
                for L in (L_now + (seq - seq[0]))[::5]:
                    self.w.move_abs(L)
                self.w.move_abs(L1)
                self.state = "VERIFY"
                c_t, n_t, _, _ = measure_plane(self.w, hx, c)
                ang, de, lat = self.errors(c_t, n_t, c)
            else:
                seq, T = np.array([self.w.read()]), 0.0
            self.log(f"  보정 {it}회: 각도오차 {ang:.3f}° 거리오차 {de:+.2f}mm 횡오차 {lat:.2f}mm")
            if ang <= c.TOL_TILT and abs(de) <= c.TOL_DIST and lat <= c.TOL_XY:
                self.state = "CAPTURE"
                self.on_capture()
                self.state = "DONE"
                return dict(ok=True, iters=it, ang=ang, dist_err=de, xy_err=lat,
                            Lmin=float(seq.min()), Lmax=float(seq.max()), T=T)
        self.state = "ALARM"
        return dict(ok=False, why="수렴 실패")


# ───────────────────────── 시뮬레이션 자체 검증 ─────────────────────────
def make_case(r, ang, tilt, tdir, dz, seed):
    """PCB 위치(반경 r, 각 ang), 기울기 tilt(deg, 방향 tdir), 높이편차 dz -> SimWorld."""
    t = math.radians(tilt)
    n_up = np.array([math.sin(t) * math.cos(tdir), math.sin(t) * math.sin(tdir), -math.cos(t)])
    c = np.array([r * math.cos(ang), r * math.sin(ang), Cfg.H0 + Cfg.D_TOOL + dz])
    return SimWorld(Hexapod(), c, -n_up, seed=seed), tilt


def selftest(n_trials, seed=7, worst=False):
    rng = np.random.default_rng(seed)
    res = []
    cases = []
    if worst:        # 모서리 조건 : 반경150 x 기울기15° x 8방향 x 높이±10
        for ang in np.radians(np.arange(0, 360, 45)):
            for tdir in np.radians(np.arange(0, 360, 45)):
                for dz in (-10, 10):
                    cases.append((150.0, ang, 15.0, tdir, dz))
    else:
        for _ in range(n_trials):
            cases.append((150 * math.sqrt(rng.uniform()), rng.uniform(0, 2 * math.pi),
                          15 * math.sqrt(rng.uniform()), rng.uniform(0, 2 * math.pi), rng.uniform(-10, 10)))
    for k, cs in enumerate(cases):
        w, tilt = make_case(*cs, seed=k)
        out = AlignController(w).run_cycle()
        out["tilt"], out["r"] = tilt, cs[0]
        res.append(out)
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--worst", action="store_true", help="모서리 조건(반경150·15°) 전수 시험")
    a = ap.parse_args()
    print(f"헥사포드 PCB 정렬 제어 {VERSION} — 시뮬레이션 {"모서리 128조건" if a.worst else str(a.n)+"회"} (PCB 위치 반경150mm·기울기≤15°·높이±10mm)")
    R = selftest(a.n, worst=a.worst)
    okc = [r for r in R if r["ok"]]
    print(f"성공 {len(okc)}/{len(R)}")
    if okc:
        print("보정 횟수 평균 %.2f / 최대 %d" % (np.mean([r["iters"] for r in okc]), max(r["iters"] for r in okc)))
        print("각도오차 최대 %.3f° / 거리오차 최대 %.3fmm / 횡오차 최대 %.3fmm" % (
            max(r["ang"] for r in okc), max(abs(r["dist_err"]) for r in okc), max(r["xy_err"] for r in okc)))
        print("사용 레그 길이 %.0f ~ %.0f mm (스트로크 %.0f mm)" % (
            min(r["Lmin"] for r in okc), max(r["Lmax"] for r in okc),
            max(r["Lmax"] for r in okc) - min(r["Lmin"] for r in okc)))
        print("이동시간 최대 %.2f s" % max(r["T"] for r in okc))
    for r in R:
        if not r["ok"]:
            print("실패:", r["why"])
