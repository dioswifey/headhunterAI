/* 첫 페이지·About 유니콘 밴드 — 점(입자) 유니콘 (2026-11-20 사용자 확정 ③: 3D 모델 없이 그림 + 코드, 첫 화면 지구본과 같은 점 스타일).
   원래 그림(images/about_unicorn_dots_src.png = about_unicorn_hero.png에서 유니콘만 남기고 발 아래 지형·네온 점을 지운 사본)의 밝기를 점으로 뽑아
   판화 결이 점 밀도로 보이게 하고, 2D 뼈대(관절 회전 + 다리 IK)로 점을 움직인다. 바닥 네온 와이어 지형은 코드로 그린다.
   6초 연속 동작이 끊김 없이 반복된다(모든 곡선은 0초와 6초 값·속도가 같음, 흔들림 주기는 1.5·3·6초).
   색: 검정 배경 · 흰/회색 점(유니콘) · 네온 #39FF14(지형·불꽃·뿔 빛)만. 움직임 줄이기 = 한 자세 정지, 화면 밖 = 그리기 멈춤,
   그림을 못 읽으면 원래 PNG 그대로. 확인용 ?unicorn_t=초 · 끄기 ?unicorn=0 */
const NEON = '57,255,20';
const SRC = { url: '/images/about_unicorn_dots_src.png', ox: 492, oy: 0, W: 1604, H: 887 };   // 사본의 원본 그림 속 위치
const LOOP = 8;
const still = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
const Q = new URLSearchParams(location.search);
const FIX_T = Q.get('unicorn_t');
const D = Math.PI / 180;
const clamp01 = (x) => Math.min(1, Math.max(0, x));
const seg = (t, a, b) => clamp01((t - a) / (b - a));
const lerp = (a, b, k) => a + (b - a) * k;
const smooth = (u) => u * u * (3 - 2 * u);
const inCubic = (u) => u * u * u;
const outBack = (u, s = 1.6) => 1 + (s + 1) * Math.pow(u - 1, 3) + s * Math.pow(u - 1, 2);
const damp = (t, t0, amp, per, dec) => (t < t0 ? 0 : amp * Math.sin(2 * Math.PI * (t - t0) / per) * Math.exp(-(t - t0) * dec));
const wave = (t, per, amp, ph = 0) => amp * Math.sin(2 * Math.PI * t / per + ph);
const EASE = { s: smooth, in: inCubic, out: (u) => 1 - Math.pow(1 - u, 3), back: (u) => outBack(u, 1.6),
  io: (u) => (u < 0.5 ? 4 * u * u * u : 1 - Math.pow(-2 * u + 2, 3) / 2) };   // io = 천천히 시작 → 가운데서 힘 → 천천히 멈춤(무게감)
function curve(keys) {
  return (t) => {
    if (t <= keys[0][0]) return keys[0][1];
    for (let i = 1; i < keys.length; i++) {
      const [t1, v1, e] = keys[i];
      if (t <= t1) { const [t0, v0] = keys[i - 1]; return lerp(v0, v1, EASE[e || 's']((t - t0) / (t1 - t0))); }
    }
    return keys[keys.length - 1][1];
  };
}

/* ---------- 2D 뼈대(사본 그림 좌표, 화면 y는 아래로) ----------
   종이인형처럼 보이지 않게(2026-11-20 사용자 피드백): ① 목·몸통·꼬리·날개·다리를 여러 마디로 나누고 굽힘을 마디에 나눠 실어
   경첩이 아니라 곡선으로 휘게 ② 점마다 가까운 뼈 여러 개를 부드럽게 섞어(가우스 가중치) 관절에서 꺾이는 선이 생기지 않게
   ③ 마디마다 시간차(겹치는 동작: 뿌리가 먼저, 끝이 뒤따름) ④ 숨쉬기·갈기·깃털 끝 떨림 같은 2차 움직임 */
const BONES = [
  ['hip', null, [470, 545], [600, 530]], ['mid', 'hip', [600, 530], [690, 520]], ['chest', 'mid', [690, 520], [745, 540]],
  ['n1', 'chest', [705, 482], [745, 432]], ['n2', 'n1', [745, 432], [785, 397]], ['n3', 'n2', [785, 397], [818, 378]],
  ['head', 'n3', [818, 378], [838, 495]],
  ['t1', 'hip', [455, 525], [390, 542]], ['t2', 't1', [390, 542], [320, 582]], ['t3', 't2', [320, 582], [262, 640]], ['t4', 't3', [262, 640], [212, 690]],
  ['wn1', 'mid', [585, 440], [500, 330]], ['wn2', 'wn1', [500, 330], [330, 190]], ['wn3', 'wn2', [330, 190], [60, 60]],
  ['wf1', 'mid', [610, 410], [625, 320]], ['wf2', 'wf1', [625, 320], [650, 190]], ['wf3', 'wf2', [650, 190], [690, 30]],
  ['frU', 'chest', [740, 548], [835, 600]], ['frC', 'frU', [835, 600], [852, 640]], ['frP', 'frC', [852, 640], [838, 678]],
  ['flU', 'chest', [745, 575], [745, 690]], ['flC', 'flU', [745, 690], [730, 770]], ['flP', 'flC', [730, 770], [715, 801]],
  ['hrU', 'hip', [445, 590], [372, 720]], ['hrC', 'hrU', [372, 720], [352, 800]], ['hrP', 'hrC', [352, 800], [345, 826]],
  ['hlU', 'hip', [540, 600], [505, 690]], ['hlC', 'hlU', [505, 690], [570, 770]], ['hlP', 'hlC', [570, 770], [600, 796]],
].map(([name, parent, a, b]) => ({ name, parent, a, b, len: Math.hypot(b[0] - a[0], b[1] - a[1]), ang: Math.atan2(b[1] - a[1], b[0] - a[0]) }));
const BI = Object.fromEntries(BONES.map((b, i) => [b.name, i]));
BONES.forEach((b) => { b.pi = b.parent ? BI[b.parent] : -1; });
BONES.forEach((b, i) => { b.anc = []; for (let j = b.pi; j >= 0; j = BONES[j].pi) b.anc.push(j); });
const HORN_TIP = [950, 481];
const FR_CONTACT = [792, 800];          // 들린 앞발이 디딜 자리(발굽 바닥, 지형 위 — 지형이 오른쪽으로 갈수록 올라가 다른 발보다 높게 보임)

// 8초 안무 — 앞다리를 들고 일어서는 유니콘(2026-11-20 사용자 지시: 레퍼런스 = 뒷다리로 서서 앞발을 접어 든 유니콘, "무겁게, 파닥파닥 금지").
// 0~1.2 그림 자세에서 들린 앞발을 무게 실어 디딤(네온 고리) → 1.2~2.0 뒷다리에 체중을 모으며 웅크림(머리 살짝 숙임)
// → 2.0~3.6(1.6초) 뒷다리를 축으로 몸 전체가 천천히 일어섬(34°), 앞다리 둘을 차례로 접어 듦, 날개는 위로 크게 펼침, 꼬리는 늘어짐
// → 3.6~5.2 선 채로 앞발을 천천히 번갈아 젓고 고개를 들어 뿔을 하늘로(가장 빠른 순간 뿔 끝 불꽃), 날개 한 번 천천히 내려침
// → 5.2~6.5 무게에 끌려 내려오며 앞발을 뻗어 착지(가속, 네온 고리) → 6.5~7.2 충격을 무릎·가슴으로 받음 → 7.2~8.0 앞발 하나를 다시 접어 들어 그림 자세 = 처음.
// 모든 곡선은 0초와 8초 값·속도가 같고, 흔들림 주기는 2·4·8초(8초를 나누어떨어짐)
const C = {
  rear: curve([[0, 0], [2.0, 0], [3.6, 34, 'io'], [4.4, 32], [5.2, 33], [6.5, 0, 'in'], [6.85, -1.5, 'out'], [7.4, 0], [8, 0]]),   // 몸 전체가 일어서는 각도(도, + = 앞이 위로)
  frR: curve([[0, 1], [0.6, 1], [1.2, 0, 'in'], [2.0, 0], [2.6, 1, 'io'], [5.3, 1], [6.5, 0, 'in'], [7.2, 0], [8, 1, 'io']]),       // 앞발(그림에서 들린 쪽) 들림 1 ↔ 디딤 0
  flL: curve([[0, 0], [2.2, 0], [2.9, 1, 'io'], [5.3, 1], [6.42, 0, 'in'], [8, 0]]),                                               // 다른 앞발 접어 듦 1 ↔ 디딤 0
  frE: curve([[0, 0], [0.4, 0], [0.75, -8], [1.2, 0, 'in'], [8, 0]]),                                                              // 디디기 전 살짝 더 듦(도)
  nk: curve([[0, 0], [1.2, 3], [2.0, 6, 'io'], [3.6, 8, 'io'], [5.2, 7], [6.5, 2, 'in'], [7.1, 6, 'out'], [8, 0]]),              // + = 숙임(몸이 서는 만큼 목은 덜 듦 — 목을 둥글게 세운 자세)
  hd: curve([[0, 0], [1.2, 5], [2.0, 8, 'io'], [3.6, -10, 'io'], [4.05, -32, 'io'], [4.8, -24], [5.2, -18], [6.5, 3, 'in'], [7.1, 6, 'out'], [8, 0]]),
  rx: curve([[0, 0], [1.2, 3], [2.0, -6, 'io'], [3.6, -14, 'io'], [5.2, -12], [6.5, 2, 'in'], [7.2, 1], [8, 0]]),
  ry: curve([[0, 0], [1.2, 4], [2.0, 10, 'io'], [3.6, -2, 'io'], [5.2, 0], [6.5, 6, 'in'], [6.9, 11, 'out'], [7.8, 0], [8, 0]]),
  ch: curve([[0, 0], [1.2, 2], [2.0, 3], [3.6, -6, 'io'], [5.2, -5], [6.5, 2, 'in'], [7.1, 3], [8, 0]]),
  wz: curve([[0, 0], [1.2, 2], [2.0, 0], [3.6, 34, 'io'], [4.3, 34], [5.1, 16, 'io'], [5.8, 20, 'io'], [6.6, 6], [7.4, 3], [8, 0]]),   // + = 날개가 위·뒤로(일어설 때 크게 펼치고 한 번 천천히 내려침)
};
const PIVOT = [450, 640];                                                  // 일어설 때 몸이 도는 축(뒷다리 위, 그림 좌표)
const BURSTS = [{ t: 3.8, n: 220 }];                                     // 고개를 들어 뿔이 하늘로 가는 가장 빠른 순간
const RIPPLES = [1.2, 6.5];                                                // 디딜 때·착지할 때 지형에 네온 고리
const paw = (t, ph) => { const u = seg(t, 3.0, 5.2); return u > 0 && u < 1 ? Math.sin(Math.PI * u) * Math.sin(2 * Math.PI * (t - 3.0) / 1.1 + ph) : 0; };   // 선 채로 천천히 젓기
const FL_FOLD = { hoof: [792, 668], ang: 1.92 };                          // 접어 든 앞발의 발굽 자리·각도(가슴 기준, 그림 좌표)

/* 2D 행렬 [a b c d e f]: x' = a x + c y + e, y' = b x + d y + f */
const mul = (m, n) => [m[0] * n[0] + m[2] * n[1], m[1] * n[0] + m[3] * n[1], m[0] * n[2] + m[2] * n[3], m[1] * n[2] + m[3] * n[3], m[0] * n[4] + m[2] * n[5] + m[4], m[1] * n[4] + m[3] * n[5] + m[5]];
const rotAbout = (p, th) => { const c = Math.cos(th), s = Math.sin(th); return [c, s, -s, c, p[0] - c * p[0] + s * p[1], p[1] - s * p[0] - c * p[1]]; };
const app = (m, p) => [m[0] * p[0] + m[2] * p[1] + m[4], m[1] * p[0] + m[3] * p[1] + m[5]];
const mAng = (m) => Math.atan2(m[1], m[0]);
// 그림 속 마디(a, 각도 ang) → 화면 위 새 마디(시작 A2, 각도 phi, 길이 배율 s): 마디 방향으로만 늘고 굵기는 그대로
const segMat = (bn, A2, phi, s) => {
  const c1 = Math.cos(-bn.ang), s1 = Math.sin(-bn.ang), c2 = Math.cos(phi), s2 = Math.sin(phi);
  const l00 = c2 * s * c1 - s2 * s1, l01 = -c2 * s * s1 - s2 * c1, l10 = s2 * s * c1 + c2 * s1, l11 = -s2 * s * s1 + c2 * c1;
  return [l00, l10, l01, l11, A2[0] - (l00 * bn.a[0] + l01 * bn.a[1]), A2[1] - (l10 * bn.a[0] + l11 * bn.a[1])];
};

// 다리 무릎이 굽는 쪽(그림 속 쉬는 자세 기준)
const BEND = {};
for (const [u, l] of [['frU', 'frC'], ['flU', 'flC'], ['hrU', 'hrC'], ['hlU', 'hlC']]) {
  const U = BONES[BI[u]], L = BONES[BI[l]];
  const ac = Math.atan2(L.b[1] - U.a[1], L.b[0] - U.a[0]);
  BEND[BI[u]] = Math.sign(Math.sin(U.ang - ac)) || 1;
}

function poseMats(t0) {
  const t = ((t0 % LOOP) + LOOP) % LOOP;
  const at = (f, lag) => f(((t - lag) % LOOP + LOOP) % LOOP);            // 시간차(끝 마디가 늦게 따라옴, 반복 경계도 이어짐)
  const rot = new Array(BONES.length).fill(0);
  const rearA = C.rear(t);
  // 몸통: 엉덩이 → 가운데 → 가슴으로 굽힘을 나눠 싣고 숨쉬기(4초 주기)
  rot[BI.hip] = at(C.ch, 0.08) * 0.25 * D; rot[BI.mid] = at(C.ch, 0.04) * 0.3 * D; rot[BI.chest] = (C.ch(t) * 0.45 + wave(t, 4, 0.4, 1)) * D;
  // 목 세 마디 + 머리: 굽힘을 나눠 싣고 끝으로 갈수록 늦게 — 경첩이 아니라 곡선으로 휨, 머리는 목보다 늦게(무게)
  rot[BI.n1] = at(C.nk, 0) * 0.30 * D; rot[BI.n2] = at(C.nk, 0.06) * 0.35 * D; rot[BI.n3] = at(C.nk, 0.12) * 0.35 * D;
  rot[BI.head] = at(C.hd, 0.16) * D + wave(t, 8, 1.0, 0.5) * D;
  // 날개: 뿌리 → 가운데 → 끝으로 늦게 따라옴(무겁게, 빠른 떨림 없음)
  const wing = (lag) => at(C.wz, lag) + damp(t, 6.5 + lag, -5, 1.0, 1.8);
  rot[BI.wn1] = (wing(0) * 0.55 + wave(t, 8, 0.8)) * D; rot[BI.wn2] = (wing(0.1) * 0.3 + wave(t, 8, 0.8, -0.7)) * D; rot[BI.wn3] = (wing(0.2) * 0.25 + wave(t, 8, 0.6, -1.4)) * D;
  rot[BI.wf1] = (wing(0.04) * 0.4 + wave(t, 8, 0.6, 0.4)) * D; rot[BI.wf2] = (wing(0.13) * 0.25) * D; rot[BI.wf3] = (wing(0.22) * 0.2 + wave(t, 8, 0.5, -1)) * D;
  // 꼬리 네 마디: 몸이 일어서면 무게로 늘어지고(일어선 각도만큼 반대로), 착지 때 늦게 출렁
  const tl = (lag, a, g) => (wave(t - lag, 8, a * 0.6) + at(C.rear, lag + 0.1) * g + damp(t, 6.55 + lag, a * 1.0, 1.0, 1.6)) * D;
  rot[BI.t1] = tl(0, 2.5, 0.25); rot[BI.t2] = tl(0.1, 3.5, 0.3); rot[BI.t3] = tl(0.2, 4.5, 0.3); rot[BI.t4] = tl(0.3, 5.5, 0.25);

  const M = new Array(BONES.length);
  const root = mul([1, 0, 0, 1, C.rx(t), C.ry(t) + wave(t, 4, 0.8)], rotAbout(PIVOT, -rearA * D));   // 뒷다리 위 축으로 몸 전체가 일어섬
  const world = (i) => {
    if (M[i]) return M[i];
    const b = BONES[i], P = b.pi < 0 ? root : world(b.pi);
    return (M[i] = mul(P, rotAbout(b.a, rot[i])));
  };
  const reset = (...ns) => ns.forEach((n) => { M[BI[n]] = null; });
  // 다리 IK: 어깨(엉덩이)에서 발굽까지 세 마디의 화면 위치를 직접 정하고, 마디마다 '그림 속 마디 → 새 위치' 행렬을 만든다.
  // 들린 앞발은 그림에서 앞으로 접혀 짧아 보이므로(원근), 내려 디딜 땐 마디 길이를 디딘 앞다리(fl) 길이 쪽으로 늘린다
  const legSolve = (u, c, p, hoof, pAng, L1, L2, L3) => {
    const iu = BI[u], ic = BI[c], ip = BI[p];
    const P = world(BONES[iu].pi), A = app(P, BONES[iu].a);
    let ank = [hoof[0] - Math.cos(pAng) * L3, hoof[1] - Math.sin(pAng) * L3];
    let dx = ank[0] - A[0], dy = ank[1] - A[1], d = Math.hypot(dx, dy);
    if (d > L1 + L2 - 0.5) { const k2 = (L1 + L2 - 0.5) / d; dx *= k2; dy *= k2; d = L1 + L2 - 0.5; ank = [A[0] + dx, A[1] + dy]; }
    const cosA = Math.max(-1, Math.min(1, (L1 * L1 + d * d - L2 * L2) / (2 * L1 * d)));
    const a1 = Math.atan2(dy, dx) + BEND[iu] * Math.acos(cosA);
    const K = [A[0] + Math.cos(a1) * L1, A[1] + Math.sin(a1) * L1];
    const a2 = Math.atan2(ank[1] - K[1], ank[0] - K[0]);
    M[iu] = segMat(BONES[iu], A, a1, L1 / BONES[iu].len);
    M[ic] = segMat(BONES[ic], K, a2, L2 / BONES[ic].len);
    M[ip] = segMat(BONES[ip], ank, pAng, L3 / BONES[ip].len);
    return L2 / BONES[ic].len;
  };
  const len = (n) => BONES[BI[n]].len;
  for (const [u, c, p] of [['hrU', 'hrC', 'hrP'], ['hlU', 'hlC', 'hlP']]) legSolve(u, c, p, BONES[BI[p]].b, BONES[BI[p]].ang, len(u), len(c), len(p));   // 뒷발은 땅에 고정
  const Pc = world(BI.chest), chA = mAng(Pc);
  // 다른 앞발(fl): 디딤 0 ↔ 가슴 앞으로 접어 듦 1, 선 채로 천천히 젓기
  const fl = C.flL(t), flPaw = paw(t, Math.PI) * 0.6;
  const flFold = app(Pc, FL_FOLD.hoof), flRest = BONES[BI.flP].b;
  const flHoof = [lerp(flRest[0], flFold[0], fl) + flPaw * 10, lerp(flRest[1], flFold[1], fl) - Math.sin(Math.PI * fl) * 16 - flPaw * 14];
  legSolve('flU', 'flC', 'flP', flHoof, lerp(BONES[BI.flP].ang, FL_FOLD.ang + chA, fl), len('flU'), len('flC'), len('flP'));
  // 그림에서 들린 앞발(fr): r=1 그림 자세(무릎 접힘) ↔ r=0 바닥 디딤. 발굽은 호를 그리며 오가고 발굽 각도도 바닥에 맞게
  const r = C.frR(t), Pf = world(BONES[BI.frU].pi), A0 = app(Pf, BONES[BI.frU].a);
  const restHoof = app(Pf, BONES[BI.frP].b), e = (C.frE(t) + paw(t, 0) * 13) * D, ce = Math.cos(e), se = Math.sin(e);
  const raised = [A0[0] + ce * (restHoof[0] - A0[0]) - se * (restHoof[1] - A0[1]), A0[1] + se * (restHoof[0] - A0[0]) + ce * (restHoof[1] - A0[1])];
  const raisedP = BONES[BI.frP].ang + mAng(Pf) + e, plantedP = BONES[BI.flP].ang;
  const hoof = [lerp(FR_CONTACT[0], raised[0], r), lerp(FR_CONTACT[1], raised[1], r) - Math.sin(Math.PI * r) * 12];
  const frStretch = legSolve('frU', 'frC', 'frP', hoof, lerp(plantedP, raisedP, r),
    lerp(128, len('frU'), r), lerp(100, len('frC'), r), lerp(36, len('frP'), r));
  for (let i = 0; i < BONES.length; i++) world(i);
  return { M, t, frStretch };
}

const tipCache = new Map();
function tipAt(ts) {                                                       // 불꽃 출발점(뿔 끝) — 시각별로 한 번만 계산
  const key = Math.round(ts * 100);
  if (!tipCache.has(key)) tipCache.set(key, app(poseMats(key / 100).M[BI.head], HORN_TIP));
  return tipCache.get(key);
}

/* ---------- 점 뽑기 + 부드러운 뼈 가중치 ---------- */
const SIGMA = 26;                                                          // 가중치 퍼짐(그림 px) — 클수록 관절이 부드럽게 휨
function sampleDots(img, step) {
  const c = document.createElement('canvas'); c.width = img.width; c.height = img.height;
  const g = c.getContext('2d'); g.drawImage(img, 0, 0);
  const px = g.getImageData(0, 0, c.width, c.height).data;
  let seed = 11; const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
  const out = [];
  for (let y = 0; y < c.height; y += step) for (let x = 0; x < c.width; x += step) {
    const jx = Math.max(0, Math.min(c.width - 1, Math.round(x + (rnd() - 0.5) * step * 0.6))), jy = Math.max(0, Math.min(c.height - 1, Math.round(y + (rnd() - 0.5) * step * 0.6)));
    const b = px[(jy * c.width + jx) * 4] / 255;
    if (b < 0.16 || rnd() > Math.pow(b, 0.8) * 1.1) continue;                       // 밝을수록 점이 많고 진하게(판화 결)
    out.push({ x: jx, y: jy, b, r: rnd() });
  }
  const extra = [];
  const segD = (p, bn) => { const ax = bn.a[0], ay = bn.a[1], bx = bn.b[0] - ax, by = bn.b[1] - ay; const u = clamp01(((p.x - ax) * bx + (p.y - ay) * by) / (bx * bx + by * by)); return [Math.hypot(p.x - ax - bx * u, p.y - ay - by * u), u]; };
  for (const p of out) {
    const ds = BONES.map((bn) => segD(p, bn));
    let n = 0; ds.forEach((d, i) => { if (d[0] < ds[n][0]) n = i; });
    // 같은 사슬(가장 가까운 뼈의 조상·자손)만 섞는다 — 앞다리 점이 다른 다리·날개에 끌려가지 않게
    const chain = new Set([n]); if (BONES[n].pi >= 0) chain.add(BONES[n].pi); BONES.forEach((bn, i) => { if (bn.pi === n) chain.add(i); });   // 자기 · 부모 · 자식
    const cand = [...chain].map((i) => [i, Math.exp(-Math.pow(ds[i][0] / SIGMA, 2))]).sort((a, b) => b[1] - a[1]).slice(0, 3);
    const sum = cand.reduce((a, c2) => a + c2[1], 0) || 1;
    p.bi = cand.map((c2) => c2[0]); p.bw = cand.map((c2) => c2[1] / sum);
    if (!(sum > 1e-6)) { p.bi = [n]; p.bw = [1]; }
    const nm = BONES[n].name;
    p.head = nm === 'head' || (nm === 'n3' && ds[n][1] > 0.4);
    if (nm === 'frC' || nm === 'frP') for (let k2 = 1; k2 <= 2; k2++) extra.push({ ...p, x: p.x + (rnd() - 0.5) * step, y: p.y + (rnd() - 0.5) * step, dup: k2 });
    p.grp = nm[0] === 'w' ? 'wing' : nm[0] === 'n' || nm === 'head' ? 'neck' : nm[0] === 't' ? 'tail' : ['hip', 'mid', 'chest'].includes(nm) ? 'body' : 'leg';
    // 2차 움직임 재료: 날개 끝까지 거리, 갈기(목 위쪽) 여부
    if (p.grp === 'wing') { const rt = BONES[BI[nm[1] === 'n' ? 'wn1' : 'wf1']].a; p.wd = Math.hypot(p.x - rt[0], p.y - rt[1]); }
    if (p.grp === 'neck') { const bn = BONES[n], cross = (bn.b[0] - bn.a[0]) * (p.y - bn.a[1]) - (bn.b[1] - bn.a[1]) * (p.x - bn.a[0]); p.mane = cross < 0 && ds[n][0] > 18; }
  }
  for (const q of extra) out.push(q);                                       // 늘어날 때 성겨지지 않게 덧점(겹쳐도 같은 자리라 평소엔 안 보임)
  return out;
}

/* ---------- 장면 ---------- */
function mount(art) {
  art.dataset.rig3d = '1';
  const host = art.parentElement; host.classList.add('ab-band-rig');
  const cv = document.createElement('canvas'); cv.className = 'ab-band-canvas ab-band-3d'; cv.setAttribute('aria-hidden', 'true');
  host.appendChild(cv);
  const ctx = cv.getContext('2d');
  const img = new Image(); img.src = SRC.url + '?v=20261120a';
  img.onerror = () => { cv.remove(); host.classList.remove('ab-band-rig'); };              // 못 읽으면 원래 PNG
  let dots = [], W = 0, H = 0, dpr = 1, k = 1, padT = 0, curT = 0, step = 3;
  const toScr = (p) => [((SRC.ox + p[0]) / SRC.W) * art.clientWidth, padT + (p[1] / SRC.H) * art.clientHeight];

  function fit() {
    const r = art.getBoundingClientRect(); if (!r.width) return;
    padT = r.height * 0.18; const padR = r.width * 0.06;
    cv.style.left = art.offsetLeft + 'px'; cv.style.top = (art.offsetTop - padT) + 'px';
    W = r.width + padR; H = r.height + padT; dpr = Math.min(2, window.devicePixelRatio || 1);
    cv.style.width = W + 'px'; cv.style.height = H + 'px'; cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
    k = r.width / SRC.W;                                                      // 그림 1px → 화면 px
    const st = Math.max(2, Math.round(1.9 / k));                               // 화면에서 점 간격 ≈ 1.9px(판화 결이 보이는 밀도)
    if (img.naturalWidth && (st !== step || !dots.length)) { step = st; dots = sampleDots(img, step); }   // 화면 크기가 바뀌면 점 간격에 맞춰 다시 뽑음
    render(curT);
  }

  // 바닥 네온 와이어 지형(원래 그림 오른쪽 아래 지형 자리, 그림 좌표). 6초에 한 번 출렁임이 제자리로
  function terrain(t) {
    const dens = clamp01(k / 0.65), ROWS = Math.round(lerp(8, 16, dens)), COLS = Math.round(lerp(26, 60, dens)), pts = [];
    for (let j = 0; j < ROWS; j++) {
      const v = j / (ROWS - 1), row = [];
      for (let i = 0; i < COLS; i++) {
        const u = i / (COLS - 1);
        const x = lerp(470, 1640, u) + v * 40 * (u - 0.5);
        const yf = 905 - 40 * u, yb = 812 - 210 * u;
        let y = lerp(yf, yb, v) + 7 * Math.sin(2 * Math.PI * (u * 1.6 + v * 0.8 - t / LOOP)) * (0.4 + 0.6 * v);
        row.push([x - SRC.ox, y]);
      }
      pts.push(row);
    }
    return pts;
  }

  function render(t0) {
    curT = t0;
    const { M, t, frStretch } = poseMats(t0);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, W, H);
    const s = Math.max(1, 1.65 * k * step / 1.9);
    // 1) 지형: 회색 선 + 네온 마디. 디딜 때(1.45초) 디딘 자리에서 네온 고리가 지형을 타고 퍼짐
    const tp = terrain(t), cx = FR_CONTACT[0], cyy = FR_CONTACT[1];
    ctx.lineWidth = Math.max(0.5, 0.9 * k * 1.6);
    for (let j = 0; j < tp.length; j++) {
      const v = j / (tp.length - 1);
      ctx.strokeStyle = `rgba(${NEON},${(0.06 + 0.16 * v).toFixed(3)})`; ctx.beginPath();
      tp[j].forEach((p, i) => { const q = toScr(p); i ? ctx.lineTo(q[0], q[1]) : ctx.moveTo(q[0], q[1]); }); ctx.stroke();
    }
    for (let i = 0; i < tp[0].length; i += 1) {
      ctx.strokeStyle = `rgba(${NEON},0.07)`; ctx.beginPath();
      tp.forEach((row, j) => { const q = toScr(row[i]); j ? ctx.lineTo(q[0], q[1]) : ctx.moveTo(q[0], q[1]); }); ctx.stroke();
    }
    for (let j = 0; j < tp.length; j++) for (let i = 0; i < tp[j].length; i++) {
      const p = tp[j][i]; const q = toScr(p);
      const dd = Math.hypot(p[0] - cx, (p[1] - cyy) * 2.2);
      let ring = 0; for (const t1 of RIPPLES) { const rip = t - t1; if (rip > 0 && rip < 1.4) ring += Math.exp(-Math.pow((dd - rip * 460) / 40, 2)) * (1 - rip / 1.4); }
      const base = ((i * 7 + j * 13) % 7 === 0 ? 0.8 : 0.18) * (0.45 + 0.55 * j / tp.length);
      const a = Math.min(1, base + ring * 1.2); if (a < 0.03) continue;
      ctx.fillStyle = `rgba(${NEON},${a.toFixed(3)})`; const z = s * (1 + ring);
      ctx.fillRect(q[0] - z / 2, q[1] - z / 2, z, z);
    }
    // 2) 유니콘 점(흰·회색, 밝기 → 진하기·크기)
    const buckets = [[], [], [], [], [], []];
    const scat = (bt) => { const u = (t - bt) / 0.9; return u > 0 && u < 1 ? Math.sin(Math.PI * u) * Math.pow(1 - u, 0.5) : 0; };
    // 날개를 내려칠 때 깃이 화면 안쪽으로 접혀 보이게(원근감): 뿌리 쪽으로 살짝 짧아짐
    const beatAmt = (() => { const u = seg(t, 4.3, 5.8); return u > 0 && u < 1 ? Math.sin(Math.PI * u) * 0.8 : 0; })();   // 날개를 천천히 내려칠 때만
    const rootN = app(M[BI.wn1], BONES[BI.wn1].a), rootF = app(M[BI.wf1], BONES[BI.wf1].a), fsh = 1 - 0.14 * beatAmt;
    const br = Math.sin(2 * Math.PI * t / 4);                                     // 숨쉬기(4초 주기, 느리고 깊게)
    const maneKick = damp(t, 1.25, 1.2, 1.0, 1.8) + damp(t, 6.55, 2.2, 1.0, 1.6);
    for (const p of dots) {
      if (p.dup && frStretch < 1 + 0.45 * p.dup) continue;                     // 앞발이 늘어날 때만 덧점을 그림
      let sx = p.x, sy = p.y;
      if (p.grp === 'body') { sx = 600 + (sx - 600) * (1 + 0.003 * br); sy = 540 + (sy - 540) * (1 + 0.008 * br); }   // 가슴·배가 부풀었다 가라앉음
      let x = 0, y = 0;
      for (let j = 0; j < p.bi.length; j++) { const m = M[p.bi[j]], w = p.bw[j]; x += (m[0] * sx + m[2] * sy + m[4]) * w; y += (m[1] * sx + m[3] * sy + m[5]) * w; }
      if (p.grp === 'wing') {
        const rt = p.wd !== undefined && p.bi.some((i) => BONES[i].name[1] === 'f') ? rootF : rootN;
        x = rt[0] + (x - rt[0]) * fsh; y = rt[1] + (y - rt[1]) * (1 - (1 - fsh) * 0.5);
        y += (p.wd / 500) * 0.8 * Math.sin(2 * Math.PI * t / 4 - p.wd * 0.006);                                  // 깃 끝이 느리게 일렁임(끝일수록 크게)
      }
      if (p.mane) { x += 1.0 * Math.sin(2 * Math.PI * t / 4 - p.y * 0.02) + maneKick * (0.5 + p.r); y += 0.5 * Math.cos(2 * Math.PI * t / 4 - p.x * 0.02); }   // 갈기가 느리게 흩날림
      const tw = 0.95 + 0.05 * Math.sin(2 * Math.PI * (t / 4) + p.r * 30);      // 아주 은은한 반짝임(3초 주기)
      const lv = Math.min(5, Math.floor(p.b * tw * 6));
      buckets[lv].push(x, y);
    }
    buckets.forEach((arr, lv) => {
      if (!arr.length) return;
      const a = 0.38 + lv * 0.124, z = s * (0.8 + lv * 0.1);
      ctx.fillStyle = `rgba(255,255,255,${a.toFixed(2)})`; ctx.beginPath();
      for (let i = 0; i < arr.length; i += 2) { const q = toScr([arr[i], arr[i + 1]]); ctx.rect(q[0] - z / 2, q[1] - z / 2, z, z); }
      ctx.fill();
    });
    // 3) 뿔 끝 네온 빛 + 불꽃(쳐올리는 가장 빠른 순간)
    const tip = app(M[BI.head], HORN_TIP), tq = toScr(tip);
    const glow = 0.1 + 0.9 * Math.min(1, scat(3.65) * 1.3);
    const gr = ctx.createRadialGradient(tq[0], tq[1], 0, tq[0], tq[1], 15 * k * 2.2);
    gr.addColorStop(0, `rgba(${NEON},${(0.5 * glow).toFixed(3)})`); gr.addColorStop(1, `rgba(${NEON},0)`);
    ctx.fillStyle = gr; ctx.fillRect(tq[0] - 70 * k * 2.2, tq[1] - 70 * k * 2.2, 140 * k * 2.2, 140 * k * 2.2);
    ctx.globalCompositeOperation = 'lighter';
    for (const bu of BURSTS) {
      let seed = Math.round(bu.t * 1000); const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
      for (let i = 0; i < bu.n; i++) {
        const ts = bu.t + Math.pow(rnd(), 1.6) * 0.18, life = 0.45 + 0.4 * rnd(), dt = t - ts, u = dt / life;
        const vx0 = rnd(), vy0 = rnd(), core = rnd() < 0.25, sp0 = rnd();
        if (dt < 0 || u > 1) continue;
        const a0 = tipAt(ts), a1 = tipAt(ts + 0.02);
        const vx = (a1[0] - a0[0]) * 50 * (0.3 + 0.5 * sp0) + (vx0 - 0.5) * 420, vy = (a1[1] - a0[1]) * 50 * (0.3 + 0.5 * sp0) + (vy0 - 0.9) * 360;
        const x = a0[0] + vx * dt, y = a0[1] + vy * dt + 0.5 * 520 * dt * dt;
        const q = toScr([x, y]), al = Math.pow(1 - u, 1.4), z = s * (core ? 1.8 : 1.3);
        ctx.fillStyle = core ? `rgba(255,255,255,${(al * 0.9).toFixed(3)})` : `rgba(${NEON},${al.toFixed(3)})`;
        ctx.fillRect(q[0] - z / 2, q[1] - z / 2, z, z);
      }
    }
    ctx.globalCompositeOperation = 'source-over';
  }

  img.onload = () => {
    art.classList.add('rig-on');
    fit();
    if (window.ResizeObserver) new ResizeObserver(fit).observe(art);
    window.__unicorn3d = { render };
    if (FIX_T !== null) { render(+FIX_T); return; }
    if (still) { render(5.0); cv.dataset.state = 'still'; return; }   // 움직임 줄이기 = 일어선 자세로 정지
    let visible = true, started = false;
    new IntersectionObserver((es) => { for (const e of es) { visible = e.isIntersecting; if (!started && e.isIntersecting && e.intersectionRatio >= 0.6) { started = true; play(); } } }, { threshold: [0, 0.3, 0.6, 1] }).observe(art);
    render(0);
    function play() {
      cv.dataset.state = 'playing'; const t0 = performance.now();
      const step2 = (now) => { if (!cv.isConnected) return; if (visible) render((now - t0) / 1000); requestAnimationFrame(step2); };
      requestAnimationFrame(step2);
    }
  };
}
const scan = () => { if (Q.get('unicorn') !== '0') document.querySelectorAll('.ab-band-art:not([data-rig3d])').forEach(mount); };
new MutationObserver(scan).observe(document.documentElement, { childList: true, subtree: true });
scan();
