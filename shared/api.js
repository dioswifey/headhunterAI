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
      if (!h.dev_mode) return;
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

  return { api, get, post, put, del, esc, $, $$, toast, modal, fmtDate, fmtDateTime, fmtYMD, score, ITEM_KO, CAT_KO, ATTEMPT_KO, ANALYSIS_KO, STEP_KO, SECTION_KO, devbar };
})();
