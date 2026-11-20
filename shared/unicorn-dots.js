/* 첫 페이지·About 유니콘 밴드 — 점(입자) 유니콘 (2026-11-20 사용자 확정 ③: 3D 모델 없이 그림 + 코드, 첫 화면 지구본과 같은 점 스타일).
   원래 그림(images/about_unicorn_dots_src.png = about_unicorn_hero.png에서 유니콘만 남기고 발 아래 지형·네온 점을 지운 사본)의 밝기를 점으로 뽑아
   판화 결이 점 밀도로 보이게 하고, 2D 뼈대(관절 회전 + 다리 IK)로 점을 움직인다. 바닥 네온 와이어 지형은 코드로 그린다.
   6초 연속 동작이 끊김 없이 반복된다(모든 곡선은 0초와 6초 값·속도가 같음, 흔들림 주기는 1.5·3·6초).
   색: 검정 배경 · 흰/회색 점(유니콘) · 네온 #39FF14(지형·불꽃·뿔 빛)만. 움직임 줄이기 = 한 자세 정지, 화면 밖 = 그리기 멈춤,
   그림을 못 읽으면 원래 PNG 그대로. 확인용 ?unicorn_t=초 · 끄기 ?unicorn=0 */
const NEON = '57,255,20';
const SRC = { url: '/images/about_unicorn_dots_src.png', ox: 492, oy: 0, W: 1604, H: 887 };   // 사본의 원본 그림 속 위치
const LOOP = 6;
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
const EASE = { s: smooth, in: inCubic, out: (u) => 1 - Math.pow(1 - u, 3), back: (u) => outBack(u, 1.6) };
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

/* ---------- 2D 뼈대(사본 그림 좌표, 화면 y는 아래로) ---------- */
const BONES = [
  ['body', null, [600, 530], [700, 520]],
  ['chest', 'body', [640, 520], [740, 525]],
  ['neck', 'chest', [715, 470], [815, 378]],
  ['head', 'neck', [815, 378], [835, 495]],
  ['frU', 'chest', [735, 545], [845, 615]], ['frL', 'frU', [845, 615], [835, 678]],
  ['flU', 'chest', [745, 575], [742, 690]], ['flL', 'flU', [742, 690], [715, 800]],
  ['hrU', 'body', [445, 590], [372, 720]], ['hrL', 'hrU', [372, 720], [345, 825]],
  ['hlU', 'body', [540, 600], [505, 690]], ['hlL', 'hlU', [505, 690], [600, 795]],
  ['tail1', 'body', [450, 525], [330, 565]], ['tail2', 'tail1', [330, 565], [215, 675]],
  ['wn1', 'chest', [585, 440], [430, 240]], ['wn2', 'wn1', [430, 240], [60, 60]],
  ['wf1', 'chest', [610, 410], [640, 250]], ['wf2', 'wf1', [640, 250], [690, 30]],
].map(([name, parent, a, b]) => ({ name, parent, a, b, len: Math.hypot(b[0] - a[0], b[1] - a[1]), ang: Math.atan2(b[1] - a[1], b[0] - a[0]) }));
const BI = Object.fromEntries(BONES.map((b, i) => [b.name, i]));
BONES.forEach((b) => { b.pi = b.parent ? BI[b.parent] : -1; });
const HORN_TIP = [950, 481];

// 6초 안무(3D 시안과 같은 순서): 날개 치켜 엶 → 앞발 디딤 + 뿔 아래로 → 아래에서 위로 쳐올림(불꽃①)
// → 고개 듦·날개 두 번 침 → 한 번 더 쳐올림(불꽃②) → 앞발 다시 듦·그림 자세 → 6초 = 처음
const C = {
  frR: curve([[0, 1], [1.2, 1], [1.45, 0, 'in'], [4.3, 0], [4.95, 1], [6, 1]]),
  frE: curve([[0, 0], [0.9, -8], [1.2, -8], [1.45, 0, 'in'], [6, 0]]),
  nk: curve([[0, 0], [0.9, -5], [1.2, -5], [1.45, 16, 'in'], [1.85, -22, 'back'], [2.6, -15], [3.3, -13], [3.85, -2], [4.15, -19, 'back'], [4.5, -12], [5.1, 0], [6, 0]]),
  hd: curve([[0, 0], [1.2, 0], [1.45, 14, 'in'], [1.85, -30, 'back'], [2.6, -22], [3.3, -20], [3.85, 6], [4.15, -26, 'back'], [4.5, -17], [5.1, 0], [6, 0]]),
  rx: curve([[0, 0], [1.45, 0], [1.85, 4, 'out'], [4.3, 4], [5.1, 0], [6, 0]]),
  ry: curve([[0, 0], [0.9, -4], [1.2, -4], [1.45, 8, 'in'], [1.85, -4, 'out'], [2.6, 0], [3.3, -3], [3.85, 4], [4.15, -3, 'out'], [5.0, 0], [6, 0]]),
  ch: curve([[0, 0], [0.9, -2], [1.2, -2], [1.45, 4, 'in'], [1.85, -6, 'back'], [2.6, -3], [3.3, -4], [3.85, 2], [4.15, -5, 'back'], [5.0, 0], [6, 0]]),
  wz: curve([[0, 0], [0.7, 9], [1.45, 9], [2.4, 3], [4.4, 3], [5.2, 7], [6, 0]]),            // + = 날개가 위·뒤로 치켜짐(화면 시계방향)
};
const flap = (t) => { const u = seg(t, 2.9, 4.1); return u > 0 && u < 1 ? 16 * (1 - Math.cos(2 * Math.PI * 2 * u)) / 2 : 0; };
const BURSTS = [{ t: 1.45, n: 170 }, { t: 3.85, n: 110 }];

/* 2D 행렬 [a b c d e f]: x' = a x + c y + e, y' = b x + d y + f */
const mul = (m, n) => [m[0] * n[0] + m[2] * n[1], m[1] * n[0] + m[3] * n[1], m[0] * n[2] + m[2] * n[3], m[1] * n[2] + m[3] * n[3], m[0] * n[4] + m[2] * n[5] + m[4], m[1] * n[4] + m[3] * n[5] + m[5]];
const rotAbout = (p, th) => { const c = Math.cos(th), s = Math.sin(th); return [c, s, -s, c, p[0] - c * p[0] + s * p[1], p[1] - s * p[0] - c * p[1]]; };
const app = (m, p) => [m[0] * p[0] + m[2] * p[1] + m[4], m[1] * p[0] + m[3] * p[1] + m[5]];

function poseMats(t0) {
  const t = ((t0 % LOOP) + LOOP) % LOOP;
  const rot = new Array(BONES.length).fill(0);
  rot[BI.chest] = (C.ch(t) + wave(t, 1.5, 0.5, 1)) * D;
  rot[BI.neck] = C.nk(t) * D; rot[BI.head] = C.hd(t) * D;
  // 날개: + = 화면 시계방향 = 깃 끝이 위로 치켜짐, − = 아래로 내려침(끝 마디가 한 박자 늦게)
  const beat = (d) => damp(t, 1.45 + d, -11, 0.6, 3.2) - flap(t - d);
  rot[BI.wn1] = (C.wz(t) + beat(0.06) + wave(t, 3, 1.2)) * D; rot[BI.wn2] = (beat(0.14) * 0.8 + wave(t, 3, 1.5, -0.6)) * D;
  rot[BI.wf1] = (C.wz(t) * 0.7 + beat(0.06) * 0.7 + wave(t, 3, 1, 0.4)) * D; rot[BI.wf2] = (beat(0.14) * 0.6) * D;
  rot[BI.tail1] = (wave(t, 3, 4, 0) + damp(t, 1.5, 7, 0.5, 3) + damp(t, 4.2, 5, 0.5, 3)) * D;
  rot[BI.tail2] = (wave(t, 3, 6, -0.7) + damp(t, 1.58, 9, 0.5, 3)) * D;
  const M = new Array(BONES.length);
  const root = [1, 0, 0, 1, C.rx(t), C.ry(t) + wave(t, 1.5, 1.2)];
  const world = (i) => {
    if (M[i]) return M[i];
    const b = BONES[i], P = b.pi < 0 ? root : world(b.pi);
    return (M[i] = mul(P, rotAbout(b.a, rot[i])));
  };
  // 다리: 2관절 IK — 세 발굽은 그림 속 자리에 고정, 앞발(fr)은 들림 ↔ 디딤
  const ik = (u, l, target) => {
    const P = world(BONES[u].pi), A = app(P, BONES[u].a), L1 = BONES[u].len, L2 = BONES[l].len;
    const dx = target[0] - A[0], dy = target[1] - A[1], d = Math.min(Math.hypot(dx, dy), L1 + L2 - 0.5);
    const cosA = Math.max(-1, Math.min(1, (L1 * L1 + d * d - L2 * L2) / (2 * L1 * d)));
    const a1 = Math.atan2(dy, dx) + BEND[u] * Math.acos(cosA);                      // 무릎은 그림과 같은 쪽으로 굽힘
    const pAng = Math.atan2(P[1], P[0]);
    rot[u] = a1 - BONES[u].ang - pAng; M[u] = null; M[l] = null;
    const K = app(world(u), BONES[u].b);
    const a2 = Math.atan2(target[1] - K[1], target[0] - K[0]);
    rot[l] = a2 - BONES[l].ang - pAng - rot[u]; M[l] = null; world(l);
  };
  for (const [u, l] of [['flU', 'flL'], ['hrU', 'hrL'], ['hlU', 'hlL']]) ik(BI[u], BI[l], BONES[BI[l]].b);
  rot[BI.frU] = C.frE(t) * D; M[BI.frU] = null; M[BI.frL] = null;
  const r = C.frR(t);
  if (r < 0.999) {
    const from = app(world(BI.frL), BONES[BI.frL].b);
    const contact = [FR_CONTACT[0], FR_CONTACT[1]];
    ik(BI.frU, BI.frL, [lerp(contact[0], from[0], r), lerp(contact[1], from[1], r) - Math.sin(Math.PI * r) * 14]);
  }
  for (let i = 0; i < BONES.length; i++) world(i);
  return { M, t };
}
const FR_CONTACT = [805, 725];          // 들린 앞발이 디딜 자리(지형 위 — 지형이 오른쪽으로 갈수록 올라가 다른 발보다 높게 보임)
// 다리 무릎이 굽는 쪽(그림 속 쉬는 자세 기준): 엉덩이→발굽 선에서 무릎이 어느 쪽에 있는지
const BEND = {};
for (const [u, l] of [['frU', 'frL'], ['flU', 'flL'], ['hrU', 'hrL'], ['hlU', 'hlL']]) {
  const U = BONES[BI[u]], L = BONES[BI[l]];
  const ac = Math.atan2(L.b[1] - U.a[1], L.b[0] - U.a[0]);
  BEND[BI[u]] = Math.sign(Math.sin(U.ang - ac)) || 1;
}

const tipCache = new Map();
function tipAt(ts) {                                                       // 불꽃 출발점(뿔 끝) — 시각별로 한 번만 계산
  const key = Math.round(ts * 100);
  if (!tipCache.has(key)) tipCache.set(key, app(poseMats(key / 100).M[BI.head], HORN_TIP));
  return tipCache.get(key);
}

/* ---------- 점 뽑기 ---------- */
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
  // 뼈 가중치: 가장 가까운 뼈 + 관절 근처면 부모와 섞음
  const segD = (p, bn) => { const ax = bn.a[0], ay = bn.a[1], bx = bn.b[0] - ax, by = bn.b[1] - ay; const u = clamp01(((p.x - ax) * bx + (p.y - ay) * by) / (bx * bx + by * by)); return [Math.hypot(p.x - ax - bx * u, p.y - ay - by * u), u]; };
  for (const p of out) {
    let best = 0, bd = 1e9, bu = 0;
    BONES.forEach((bn, i) => { const [d, u] = segD(p, bn); if (d < bd) { bd = d; best = i; bu = u; } });
    p.b0 = best; p.b1 = BONES[best].pi; p.w = 1;
    if (p.b1 >= 0 && bu < 0.3) p.w = 0.5 + 0.5 * (bu / 0.3);
    if (p.b1 < 0) p.b1 = best;
    p.head = BONES[best].name === 'head' || (BONES[best].name === 'neck' && bu > 0.7);
  }
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
    const { M, t } = poseMats(t0);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, W, H);
    const s = Math.max(1, 1.65 * k * step / 1.9);
    // 1) 지형: 회색 선 + 네온 마디. 디딜 때(1.45초) 디딘 자리에서 네온 고리가 지형을 타고 퍼짐
    const tp = terrain(t), rip = t - 1.45, cx = FR_CONTACT[0], cyy = FR_CONTACT[1];
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
      const ring = rip > 0 && rip < 1.2 ? Math.exp(-Math.pow((dd - rip * 520) / 40, 2)) * (1 - rip / 1.2) : 0;
      const base = ((i * 7 + j * 13) % 7 === 0 ? 0.8 : 0.18) * (0.45 + 0.55 * j / tp.length);
      const a = Math.min(1, base + ring * 1.2); if (a < 0.03) continue;
      ctx.fillStyle = `rgba(${NEON},${a.toFixed(3)})`; const z = s * (1 + ring);
      ctx.fillRect(q[0] - z / 2, q[1] - z / 2, z, z);
    }
    // 2) 유니콘 점(흰·회색, 밝기 → 진하기·크기). 쳐올릴 때 머리 쪽 점 일부가 흩어졌다가 다시 모임
    const buckets = [[], [], [], [], [], []];
    const scat = (bt) => { const u = (t - bt) / 0.9; return u > 0 && u < 1 ? Math.sin(Math.PI * u) * Math.pow(1 - u, 0.5) : 0; };
    const sc1 = scat(1.5), sc2 = scat(3.9);
    for (const p of dots) {
      const A = app(M[p.b0], [p.x, p.y]), B = p.w < 1 ? app(M[p.b1], [p.x, p.y]) : A;
      let x = A[0] * p.w + B[0] * (1 - p.w), y = A[1] * p.w + B[1] * (1 - p.w);
      if (p.head && p.r < 0.35 && (sc1 || sc2)) {
        const sc = sc1 + sc2 * 0.7, ang = p.r * 40, R = 26 + p.r * 70;
        x += Math.cos(ang) * R * sc; y += (Math.sin(ang) * R - 30) * sc;
      }
      const tw = 0.85 + 0.15 * Math.sin(2 * Math.PI * (t / 1.5) + p.r * 30);    // 은은한 반짝임(1.5초 주기)
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
    const glow = 0.1 + 0.9 * Math.max(scat(1.45) * 1.4, scat(3.85) * 1.2);
    const gr = ctx.createRadialGradient(tq[0], tq[1], 0, tq[0], tq[1], 26 * k * 2.2);
    gr.addColorStop(0, `rgba(${NEON},${(0.55 * glow).toFixed(3)})`); gr.addColorStop(1, `rgba(${NEON},0)`);
    ctx.fillStyle = gr; ctx.fillRect(tq[0] - 70 * k * 2.2, tq[1] - 70 * k * 2.2, 140 * k * 2.2, 140 * k * 2.2);
    ctx.globalCompositeOperation = 'lighter';
    for (const bu of BURSTS) {
      let seed = Math.round(bu.t * 1000); const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
      for (let i = 0; i < bu.n; i++) {
        const ts = bu.t + Math.pow(rnd(), 1.6) * 0.18, life = 0.45 + 0.4 * rnd(), dt = t - ts, u = dt / life;
        const vx0 = rnd(), vy0 = rnd(), core = rnd() < 0.25, sp0 = rnd();
        if (dt < 0 || u > 1) continue;
        const a0 = tipAt(ts), a1 = tipAt(ts + 0.02);
        const vx = (a1[0] - a0[0]) * 50 * (0.3 + 0.5 * sp0) + (vx0 - 0.5) * 420, vy = (a1[1] - a0[1]) * 50 * (0.3 + 0.5 * sp0) + (vy0 - 0.75) * 380;
        const x = a0[0] + vx * dt, y = a0[1] + vy * dt + 0.5 * 900 * dt * dt;
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
    if (still) { render(2.7); cv.dataset.state = 'still'; return; }
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
