/* 첫 화면 회전 지구 (첫 페이지 · 지원자 홈 공통).
   층 순서(.hero-art 안): 원본 이미지(자리만 차지, 숨김) → 캔버스(지구 + 지평선에서 솟는 점 열) → 오려 낸 사람(images/hero_man.png).
   지구 바깥의 세로 빛은 원본 그림의 광선 대신, 대륙 점과 같은 dot()로 찍은 점 열이다.
   지구 위치·크기는 원본 이미지의 지구 곡면에 맞춘 값(원본 1536×1024 기준 중심 1130,1327 · 반지름 905).
   대륙 점은 images/globe_land_points.json([경도, 위도, …], Natural Earth 50m 육지 기반).
   색은 네온(#39FF14)·흰색·검정의 투명도만 쓴다. '움직임 줄이기' 설정이면 멈춘 한 장면만 그린다. */
(function () {
  const GEO = { cx: 1130 / 1536, cy: 1327 / 1024, r: 905 / 1536 };          // cx·r = 폭 비율, cy = 높이 비율
  const MAN = { left: 1031 / 1536, top: 220 / 1024, width: 180 / 1536 };
  const TILT = -22 * Math.PI / 180;     // 위쪽으로 북반구가 보이게 기울임
  const SECONDS_PER_TURN = 140;         // 한 바퀴 140초 — 천천히
  const NEON = '57, 255, 20';
  const still = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
  const T0 = +(new URLSearchParams(location.search).get('globe_t') || 0);   // 확인용: ?globe_t=60 → 60초 뒤 모습
  let pointsP = null;

  function points() {
    if (!pointsP) pointsP = fetch('/images/globe_land_points.json').then(r => r.json()).then(a => {
      const out = new Float32Array(a.length * 1.5);       // lon, cosLat, sinLat
      for (let i = 0, j = 0; i < a.length; i += 2, j += 3) {
        const lat = a[i + 1] * Math.PI / 180; out[j] = a[i] * Math.PI / 180; out[j + 1] = Math.cos(lat); out[j + 2] = Math.sin(lat);
      }
      return out;
    }).catch(() => new Float32Array(0));
    return pointsP;
  }

  function mount(art) {
    art.dataset.globe = '1';
    const cv = document.createElement('canvas'); cv.className = 'hero-globe';
    const man = document.createElement('img'); man.className = 'hero-man'; man.src = '/images/hero_man.png'; man.alt = '';
    man.style.left = (MAN.left * 100) + '%'; man.style.top = (MAN.top * 100) + '%'; man.style.width = (MAN.width * 100) + '%';
    art.append(cv, man);
    art.classList.add('globe-on');   // 원본 이미지의 광선·glow·빛 띠를 숨긴다(styles.css)
    const ctx = cv.getContext('2d');
    let W = 0, H = 0, dpr = 1;
    const fit = () => { dpr = Math.min(2, window.devicePixelRatio || 1); W = art.clientWidth; H = art.clientHeight; cv.width = W * dpr; cv.height = H * dpr; };
    fit();
    if (window.ResizeObserver) new ResizeObserver(fit).observe(art);
    const sinT = Math.sin(TILT), cosT = Math.cos(TILT);
    // 코드 비처럼 떨어지는 가는 빛줄기 (지구 앞)
    const rain = Array.from({ length: 46 }, () => ({ x: Math.random(), y: Math.random(), v: 0.03 + Math.random() * 0.05, l: 0.04 + Math.random() * 0.08 }));
    // 지평선 위 점 열: 열마다 높이를 조금씩 다르게(열 번호로 고정된 난수라 크기가 바뀌어도 같은 모양)
    const rnd = i => { const x = Math.sin(i * 127.1 + 311.7) * 43758.5453; return x - Math.floor(x); };
    let last = performance.now();

    points().then(P => {
      function frame(now) {
        if (!art.isConnected) return;                         // 화면이 바뀌면 멈춤
        const dt = Math.min(0.05, (now - last) / 1000); last = now;
        const lam = still ? 0.6 : (now / 1000 + T0) * (2 * Math.PI / SECONDS_PER_TURN);
        const cx = GEO.cx * W, cy = GEO.cy * H, r = GEO.r * W;
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.clearRect(0, 0, W, H);
        // 1) 구 몸체: 원본 지구를 덮는 검정 + 가장자리로 갈수록 은은한 네온
        ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2);
        const body = ctx.createRadialGradient(cx, cy, r * 0.6, cx, cy, r);
        body.addColorStop(0, `rgba(${NEON},0)`); body.addColorStop(0.96, `rgba(${NEON},0.02)`); body.addColorStop(1, `rgba(${NEON},0.10)`);
        ctx.fillStyle = '#000'; ctx.fill(); ctx.fillStyle = body; ctx.fill();
        ctx.save(); ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2); ctx.clip();
        // 2) 경위선
        ctx.strokeStyle = `rgba(${NEON},0.14)`; ctx.lineWidth = 1;
        const proj = (lon, cl, sl) => { const d = lon - lam, cd = Math.cos(d); return [cl * Math.sin(d), cosT * sl - sinT * cl * cd, sinT * sl + cosT * cl * cd]; };
        for (let m = -180; m < 180; m += 20) {
          ctx.beginPath(); let pen = false; const lon = m * Math.PI / 180;
          for (let la = -80; la <= 80; la += 4) { const L = la * Math.PI / 180, p = proj(lon, Math.cos(L), Math.sin(L)); if (p[2] < 0) { pen = false; continue; } const X = cx + r * p[0], Y = cy - r * p[1]; pen ? ctx.lineTo(X, Y) : ctx.moveTo(X, Y); pen = true; }
          ctx.stroke();
        }
        for (let la = -60; la <= 80; la += 20) {
          ctx.beginPath(); let pen = false; const L = la * Math.PI / 180, cl = Math.cos(L), sl = Math.sin(L);
          for (let m = -180; m <= 180; m += 4) { const p = proj(m * Math.PI / 180, cl, sl); if (p[2] < 0) { pen = false; continue; } const X = cx + r * p[0], Y = cy - r * p[1]; pen ? ctx.lineTo(X, Y) : ctx.moveTo(X, Y); pen = true; }
          ctx.stroke();
        }
        // 3) 대륙 점: 앞쪽일수록 크고 밝게. dot() = 점 하나의 모양·크기·색·선명도. 지평선 위 점 열(5번)도 이 함수만 쓴다
        const s = Math.max(1, W / 620);
        const dot = (X, Y, z, fade = 1) => {
          ctx.fillStyle = `rgba(${NEON},${((0.4 + 0.6 * z) * fade).toFixed(3)})`;
          const sz = s * (0.8 + 0.9 * z); ctx.fillRect(X - sz / 2, Y - sz / 2, sz, sz);
        };
        for (let j = 0; j < P.length; j += 3) {
          const d = P[j] - lam, cl = P[j + 1], sl = P[j + 2], cd = Math.cos(d);
          const z = sinT * sl + cosT * cl * cd; if (z <= 0.02) continue;
          const Y = cy - r * (cosT * sl - sinT * cl * cd); if (Y > H + 2 || Y < -2) continue;
          const X = cx + r * cl * Math.sin(d);
          dot(X, Y, z);
        }
        // 4) 앞쪽 빛줄기
        if (!still) for (const d of rain) { d.y += d.v * dt; if (d.y > 1.1) { d.y = -0.1; d.x = Math.random(); } }
        for (const d of rain) {
          const g = ctx.createLinearGradient(0, (d.y - d.l) * H, 0, d.y * H); g.addColorStop(0, `rgba(${NEON},0)`); g.addColorStop(1, `rgba(${NEON},0.35)`);
          ctx.fillStyle = g; ctx.fillRect(d.x * W, (d.y - d.l) * H, Math.max(1, s), d.l * H);
        }
        ctx.restore();
        // 4-b) 지평선 위로 퍼지는 빛 띠(대기): 지구 바깥쪽만, 테두리에서 멀어질수록 옅게. 6초 주기로 은은하게 숨쉰다
        const breathe = still ? 1 : 0.85 + 0.15 * Math.sin(now / 1000 * (2 * Math.PI / 6));
        ctx.save();
        ctx.beginPath(); ctx.arc(cx, cy, r * 1.2, 0, Math.PI * 2); ctx.arc(cx, cy, r, 0, Math.PI * 2, true); ctx.clip('evenodd');
        const haze = ctx.createRadialGradient(cx, cy, r, cx, cy, r * 1.2);
        haze.addColorStop(0, `rgba(${NEON},${(0.42 * breathe).toFixed(3)})`); haze.addColorStop(0.18, `rgba(${NEON},${(0.20 * breathe).toFixed(3)})`);
        haze.addColorStop(0.5, `rgba(${NEON},${(0.06 * breathe).toFixed(3)})`); haze.addColorStop(1, `rgba(${NEON},0)`);
        ctx.fillStyle = haze; ctx.fillRect(0, 0, W, H);
        ctx.restore();
        // 5) 지평선에서 수직으로 솟는 점 열 (지구 바깥)
        //    - 첫 점: 그 x의 둥근 지평선 바로 위. x는 고정, 화면 기준 위로 같은 점을 하나씩 쌓는다
        //    - 점 사이 간격(검은 배경 보임), 열마다 높이 조금씩 다름, 위로 갈수록 불투명도만 낮아짐(크기·모양 그대로)
        //    - z = 지평선 바로 아래(점 3줄 아래) 대륙 점이 갖는 값 → 두 점이 같은 dot()·같은 값으로 찍힌다
        //    - 열 간격 = 대륙 점의 가로 간격(0.75° 격자를 화면에 옮긴 값), 세로 간격은 점 크기의 약 4배라 사이가 검게 보인다
        const pitchX = r * 0.75 * Math.PI / 180, pitchY = s * 4.8;
        const zH = Math.sqrt(Math.max(0, 1 - Math.pow(1 - (3 * pitchY) / r, 2)));
        const x0 = Math.max(0, cx - r), x1 = Math.min(W, cx + r);
        for (let i = Math.ceil(x0 / pitchX); i * pitchX <= x1; i++) {
          const X = i * pitchX, dx = X - cx;
          const yh = cy - Math.sqrt(r * r - dx * dx);                 // 그 x의 지평선 높이
          if (yh > H || yh < 0) continue;
          const n = Math.max(3, Math.round((H * (0.16 + 0.30 * rnd(i) + 0.08 * rnd(i + 999))) / pitchY));
          for (let k = 0; k < n; k++) {
            const Y = yh - pitchY * (k + 1);
            if (Y < -2) break;
            dot(X, Y, zH, Math.pow(1 - k / n, 1.15));
          }
        }
        // 6) 지평선 테두리: 선은 덜 진하게(0.85→0.55), 대신 넓게 번지는 빛(두 겹)으로 퍼지는 느낌
        ctx.save(); ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2);
        ctx.shadowColor = `rgba(${NEON},${(0.9 * breathe).toFixed(3)})`; ctx.shadowBlur = 40 * s;
        ctx.strokeStyle = `rgba(${NEON},0.30)`; ctx.lineWidth = 3 * s; ctx.stroke();
        ctx.shadowBlur = 14 * s; ctx.strokeStyle = `rgba(${NEON},0.55)`; ctx.lineWidth = 1.4 * s; ctx.stroke(); ctx.restore();
        if (!still) requestAnimationFrame(frame);
      }
      requestAnimationFrame(frame);
    });
  }

  const scan = () => document.querySelectorAll('.hero-art:not([data-globe])').forEach(mount);
  new MutationObserver(scan).observe(document.documentElement, { childList: true, subtree: true });
  scan();
})();
