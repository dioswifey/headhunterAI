/* headhunterAI shared client helpers (admin + applicant) */
const HH = (() => {
  async function api(path, opts = {}) {
    const init = { method: opts.method || 'GET', headers: {}, credentials: 'same-origin' };
    if (opts.body instanceof FormData) init.body = opts.body;
    else if (opts.body !== undefined) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(opts.body); }
    const res = await fetch(path, init);
    let data = null;
    try { data = await res.json(); } catch (_) { /* empty body */ }
    if (!res.ok) {
      const msg = data && data.detail ? (typeof data.detail === 'string' ? data.detail : '입력값을 확인해 주세요.') : `요청에 실패했습니다 (${res.status})`;
      const err = new Error(msg); err.status = res.status; throw err;
    }
    return data;
  }
  const get = (p) => api(p);
  const post = (p, body) => api(p, { method: 'POST', body: body === undefined ? {} : body });
  const put = (p, body) => api(p, { method: 'PUT', body });
  const del = (p) => api(p, { method: 'DELETE' });

  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

  let toastTimer;
  function toast(msg, dark) {
    let el = $('#hh-toast');
    if (!el) { el = document.createElement('div'); el.id = 'hh-toast'; document.body.appendChild(el); }
    el.className = 'toast' + (dark ? ' dark' : '');
    el.textContent = msg; el.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { el.hidden = true; }, 3200);
  }

  /** in-page modal (no browser dialogs). Resolves with the clicked button value. */
  function modal({ title, body, buttons = [{ label: '확인', value: true, primary: true }] }) {
    return new Promise((resolve) => {
      const bg = document.createElement('div');
      bg.className = 'modal-bg';
      bg.innerHTML = `<div class="modal" role="dialog" aria-modal="true"><h2>${esc(title)}</h2><div class="sub">${body}</div>
        <div class="row" style="justify-content:flex-end">${buttons.map((b, i) => `<button class="btn ${b.primary ? 'primary' : ''}" data-i="${i}">${esc(b.label)}</button>`).join('')}</div></div>`;
      document.body.appendChild(bg);
      bg.addEventListener('click', (e) => {
        const i = e.target.dataset && e.target.dataset.i;
        if (i === undefined) return;
        bg.remove(); resolve(buttons[+i].value);
      });
    });
  }

  const fmtDate = (s) => { if (!s) return '-'; const d = new Date(s); return `${d.getMonth() + 1}월 ${d.getDate()}일`; };
  const fmtDateTime = (s) => { if (!s) return '-'; const d = new Date(s); return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`; };
  const fmtYMD = (s) => { if (!s) return '-'; const [y, m, d] = String(s).slice(0, 10).split('-'); return `${y}.${m}.${d}`; };
  const score = (v) => (v === null || v === undefined ? '-' : Number(v).toFixed(2).replace(/\.00$/, ''));

  const ITEM_KO = { expertise: '직무 전문성', logic: '논리성', relevance: '내용 관련성·구체성', delivery: '전달력', fit: '조직 적합성' };
  const CAT_KO = { personality: '인성', resume: '이력서 기반', company: '기업 관련', job: '직무', followup: '꼬리질문' };
  const ATTEMPT_KO = { ready: '준비 중', interview: '면접 진행 중', coding: '코딩테스트 진행 중', interrupted: '장애 중단', completed: '응시 완료', terminated: '응시 종료', cancelled: '응시 취소' };
  const ANALYSIS_KO = { none: '-', queued: '분석 대기', running: '분석 중', done: '분석 완료', needs_check: '평가 지연' };
  const STEP_KO = { consent: '동의 필요', resume: '이력서 등록 필요', env: '환경 점검 필요', ready: '응시 가능', started: '응시함' };
  const SECTION_KO = { education: '학력', career: '경력', projects: '프로젝트', certificates: '자격·수상' };

  async function devbar(where) {
    try {
      const h = await get('/api/system/health');
      if (!(h.dev_bar ?? h.dev_mode)) return;   // 배포에서는 SHOW_DEVBAR=0이면 DEV 바를 숨김
      const bar = document.createElement('div');
      bar.className = 'devbar';
      bar.innerHTML = `<b>DEV</b><span>개발·시연 환경 · LLM ${esc(h.llm.model)} ${h.llm.ok ? '연결됨' : '연결 안 됨'} · DB ${h.db ? '정상' : '오류'}</span><span class="sp"></span>
        <a href="/applicant/">지원자 화면</a><a href="/admin/">기업 담당자 화면</a>`;
      document.body.prepend(bar);
      if (where) where(bar);
    } catch (_) { /* health unavailable */ }
  }

  /* 비밀번호 보기: 눈 아이콘을 누르면 2초간 보여 주고 다시 가린다. 렌더링되는 모든 password 입력에 자동 적용 */
  const EYE = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>';
  const EYE_OFF = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/><path d="M3 3l18 18"/></svg>';
  const PW_PEEK_MS = 2000;
  function enhancePasswords(root) {
    root.querySelectorAll('input[type="password"]:not([data-eye])').forEach((input) => {
      input.dataset.eye = '1';
      const wrap = document.createElement('span');
      wrap.className = 'pw-wrap';
      input.parentNode.insertBefore(wrap, input);
      wrap.appendChild(input);
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'pw-eye';
      wrap.appendChild(btn);
      let timer = null;
      const mask = () => {
        clearTimeout(timer); timer = null;
        input.type = 'password';
        btn.innerHTML = EYE; btn.setAttribute('aria-label', '비밀번호 보기'); btn.classList.remove('on');
      };
      mask();
      btn.addEventListener('mousedown', (e) => e.preventDefault()); // 입력 포커스 유지
      btn.addEventListener('click', () => {
        if (input.type === 'text') { mask(); return; }
        input.type = 'text';
        btn.innerHTML = EYE_OFF; btn.setAttribute('aria-label', '비밀번호 숨기기'); btn.classList.add('on');
        timer = setTimeout(mask, PW_PEEK_MS);
      });
      input.form && input.form.addEventListener('submit', mask);
    });
  }
  enhancePasswords(document);
  new MutationObserver(() => enhancePasswords(document)).observe(document.documentElement, { childList: true, subtree: true });

  /* 요금제·결제 화면(PRD v0.9 PAY-07~12, 2026-11-06 사용자 승인: 기존 카드 1개 + 표 + 버튼만, 새 색·장식 없음).
     지원자 #/billing · 기업 담당자 #/billing 공통. 네이버페이 등록창은 공식 SDK(nsp.pay.naver.com)만 쓴다 */
  const PAY_STATUS_KO = { active: '이용 중', canceled: '해지됨(기간 끝까지 이용)', past_due: '결제 재시도 중', expired: '종료', failed: '실패', pending: '등록 중' };
  const PAYMENT_KO = { ready: '진행 중', paid: '결제 완료', failed: '결제 실패', canceled: '취소(환불)' };
  const RESULT_KO = { ok: ['정기결제가 시작되었습니다. Advanced를 이용할 수 있습니다.', false], invalid: ['이미 처리되었거나 잘못된 결제 요청입니다.', true],
    UserCancel: ['결제 수단 등록을 취소했습니다.', true], DataExist: ['이미 등록된 네이버페이 자동결제가 있습니다. 네이버페이에서 기존 등록을 해지한 뒤 다시 시도해 주세요.', true] };
  const wonFmt = (n) => `${Number(n || 0).toLocaleString('ko-KR')}원`;
  function loadScript(src) {
    return new Promise((ok, bad) => {
      if ([...document.scripts].some((s) => s.src === src)) return ok();
      const s = document.createElement('script'); s.src = src; s.onload = ok; s.onerror = () => bad(new Error('네이버페이 화면을 불러오지 못했습니다.'));
      document.head.appendChild(s);
    });
  }
  async function billing(el, params) {
    const r = params && params.get('result');
    if (r) { const [msg, bad] = RESULT_KO[r] || [`결제를 완료하지 못했습니다. (${r})`, true]; toast(msg, bad); history.replaceState(null, '', location.pathname + '#/billing'); }
    const d = await get('/api/payments/me');
    const s = d.subscription;
    const adv = d.plan === 'advanced';
    const until = s && s.current_period_end ? fmtYMD(s.current_period_end) : '-';
    el.innerHTML = `<h1>요금제</h1><p class="sub">${d.plan_code === 'company_advanced' ? '회사 단위 요금제입니다. 같은 회사 담당자 모두에게 적용됩니다.' : '내 계정의 요금제입니다.'}</p>
      <div class="card col" style="margin-top:18px">
        <div class="between"><b style="font-size:18px">${adv ? 'Advanced' : 'Basic'}</b>${s ? `<span class="pill ${s.status === 'active' ? 'on' : 'dim'}">${esc(PAY_STATUS_KO[s.status] || s.status)}</span>` : '<span class="pill dim">무료</span>'}</div>
        ${s ? `<div class="sub">월 ${wonFmt(s.amount)} · 네이버페이 자동결제 · 이용 기간 ${until}까지${s.next_charge_at ? ` · 다음 결제 ${fmtYMD(s.next_charge_at)}` : ''}</div>`
            : `<div class="sub">Advanced는 월 ${wonFmt(d.price)}이며 네이버페이로 매월 자동 결제됩니다. 언제든 해지할 수 있고, 해지해도 결제한 기간 끝까지 이용할 수 있습니다.</div>`}
        <div class="row">${!d.enabled ? '<button class="btn primary" disabled>결제 준비 중</button>'
          : s && s.status !== 'canceled' ? '<button class="btn ghost" id="pay-cancel">정기결제 해지</button>'
          : s ? '' : '<button class="btn primary" id="pay-start">네이버페이로 구독하기</button>'}
          ${d.mode === 'mock' ? '<span class="small">개발용 가짜 결제 모드 — 실제 결제가 일어나지 않습니다.</span>' : ''}</div>
        <p class="small">결제를 누르면 자동 갱신(매월 같은 금액 결제)에 동의하는 것으로 봅니다. 카드·계좌 번호는 네이버페이가 처리하며 headhunterAI는 저장하지 않습니다. <a href="/terms" target="_blank" rel="noopener">이용약관</a> 제8조</p>
      </div>
      <h2 style="margin-top:26px">결제 내역</h2>
      ${d.payments && d.payments.length ? `<div class="card" style="padding:0;overflow-x:auto"><table class="tbl"><thead><tr><th>결제일</th><th>이용 기간</th><th>금액</th><th>상태</th></tr></thead><tbody>
        ${d.payments.map((p) => `<tr><td>${fmtYMD(p.paid_at)}</td><td>${fmtYMD(p.period_start)} ~ ${fmtYMD(p.period_end)}</td><td>${wonFmt(p.amount)}</td><td>${esc(PAYMENT_KO[p.status] || p.status)}${p.fail_code ? ` <span class="small">(${esc(p.fail_code)})</span>` : ''}</td></tr>`).join('')}
        </tbody></table></div>` : '<p class="sub">결제 내역이 없습니다.</p>'}`;
    const start = $('#pay-start', el);
    if (start) start.onclick = async () => {
      start.disabled = true;
      try {
        const o = await post('/api/payments/subscriptions');
        if (o.mock) { location.href = o.redirect; return; }
        await loadScript(o.sdk);
        window.Naver.Pay.create(o.create).open(o.open);   // 등록 후 네이버페이가 서버 returnUrl로 돌려보낸다
      } catch (e) { toast(e.message, true); start.disabled = false; }
    };
    const cancel = $('#pay-cancel', el);
    if (cancel) cancel.onclick = async () => {
      const ok = await modal({ title: '정기결제를 해지할까요?', body: `다음 결제부터 멈추고, 이미 결제한 기간(${until}까지)은 계속 Advanced로 이용할 수 있습니다.`,
        buttons: [{ label: '취소', value: false }, { label: '해지하기', value: true, primary: true }] });
      if (!ok) return;
      try { await post('/api/payments/subscriptions/cancel'); toast('정기결제를 해지했습니다.'); billing(el); } catch (e) { toast(e.message, true); }
    };
  }

  return { api, get, post, put, del, esc, $, $$, toast, modal, fmtDate, fmtDateTime, fmtYMD, score, ITEM_KO, CAT_KO, ATTEMPT_KO, ANALYSIS_KO, STEP_KO, SECTION_KO, devbar, billing };
})();

/* ───────── 언어 전환 (2026-11-19 사용자 지시) ─────────
   지원자·기업 담당자 화면도 KO/EN을 고를 수 있게 한다. 첫 페이지(main/main.js)와 **같은 저장소 키**
   `hh.lang`을 쓰므로 어느 화면에서 바꾸든 사이트 전체가 같은 언어로 유지된다.
   사전에 없는 말은 한국어 원문을 그대로 돌려주므로, 번역이 덜 된 화면도 깨지지 않는다. */
(function () {
  let lang = 'ko';
  try { if (localStorage.getItem('hh.lang') === 'en') lang = 'en'; } catch (e) { /* 저장소를 못 쓰면 한국어 */ }

  const DICT = {};                       // { '한국어 원문': '영문' }
  HH.lang = () => lang;
  HH.isEn = () => lang === 'en';
  HH.addDict = (map) => Object.assign(DICT, map || {});
  /** 번역. 사전에 없으면 원문 그대로. `HH.t('공고 N개', {n: 3})` 처럼 치환도 된다. */
  HH.t = (ko, vars) => {
    let s = (lang === 'en' && DICT[ko]) || ko;
    if (vars) for (const k of Object.keys(vars)) s = s.split('{' + k + '}').join(vars[k]);
    return s;
  };
  HH.setLang = (l) => {
    lang = l === 'en' ? 'en' : 'ko';
    try { localStorage.setItem('hh.lang', lang); } catch (e) { /* 무시 */ }
    document.documentElement.lang = lang === 'en' ? 'en' : 'ko';
    location.reload();                   // 화면을 통째로 다시 그린다(부분 갱신보다 안전하다)
  };
  /** 헤더에 KO/EN 스위치를 꽂는다. 넣을 자리는 각 화면이 정한다. */
  HH.langSwitch = () => `<div class="lang sm" role="group" aria-label="언어 / Language">
    <button type="button" data-setlang="ko"${lang === 'ko' ? ' class="on"' : ''}>KO</button>
    <button type="button" data-setlang="en"${lang === 'en' ? ' class="on"' : ''}>EN</button></div>`;
  document.addEventListener('click', (e) => {
    const b = e.target.closest('[data-setlang]');
    if (b) { e.preventDefault(); HH.setLang(b.dataset.setlang); }
  });
  document.documentElement.lang = lang === 'en' ? 'en' : 'ko';
})();

/* 헤더의 #lang-area 가 있으면 KO/EN 스위치를 그려 넣는다(지원자·기업 담당자 화면 공통) */
(function () {
  const put = () => { const el = document.getElementById('lang-area'); if (el && !el.innerHTML) el.innerHTML = HH.langSwitch(); };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', put);
  else put();
})();
