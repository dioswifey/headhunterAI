/* headhunterAI 지원자 화면 — hash router SPA (PRD 3.2 지원자 흐름) */
const { get, post, del, esc, $, $$, toast, modal, fmtDate, fmtYMD, ATTEMPT_KO, STEP_KO, SECTION_KO } = HH;
const view = $('#view');
let me = null;
let roles = [];
let cleanup = [];           // per-view teardown (timers, listeners)
const media = { stream: null, micId: '', camId: '', spkId: '' };

/* 마이크·카메라를 따로 요청한다. 한쪽이 실패해도 다른 쪽은 쓸 수 있고, 실패 원인을 장치별로 알려 준다. */
const MEDIA_ERR = {
  NotAllowedError: ['권한 거부', '주소창 왼쪽 아이콘 → 사이트 설정에서 허용한 뒤 ‘다시 시도’를 누르세요. Windows 설정 → 개인 정보 → 마이크·카메라의 데스크톱 앱 접근도 켜져 있어야 합니다.'],
  InsecureContext: ['HTTPS 필요', `브라우저는 localhost나 HTTPS 주소에서만 마이크·카메라를 허용합니다. 같은 네트워크의 다른 PC로 접속했다면 Chrome 주소창에 chrome://flags/#unsafely-treat-insecure-origin-as-secure 를 열어 ${location.origin} 을 추가하고 Enabled → 다시 실행하세요.`],
  NotFoundError: ['장치 없음', '연결된 장치를 찾지 못했습니다. 장치를 연결한 뒤 ‘다시 시도’를 누르세요.'],
  NotReadableError: ['사용 중', '다른 프로그램(Zoom·Teams·Discord 등)이 장치를 쓰고 있거나 드라이버 오류입니다. 해당 프로그램을 닫고 ‘다시 시도’를 누르세요.'],
  OverconstrainedError: ['장치 없음', '선택한 장치를 찾지 못했습니다. 목록에서 다른 장치를 고르세요.'],
};
const mediaErr = (e) => MEDIA_ERR[e && e.name] || ['연결 실패', '장치를 열지 못했습니다. ‘다시 시도’를 누르세요.'];
async function openTrack(kind, deviceId) {
  const c = kind === 'audio'
    ? { audio: deviceId ? { deviceId: { exact: deviceId } } : true }
    : { video: Object.assign({ width: 640, height: 480 }, deviceId ? { deviceId: { exact: deviceId } } : {}) };
  if (!navigator.mediaDevices) throw Object.assign(new Error('insecure context'), { name: 'InsecureContext' });
  const s = await navigator.mediaDevices.getUserMedia(c);
  return kind === 'audio' ? s.getAudioTracks()[0] : s.getVideoTracks()[0];
}
async function openMedia() {
  const res = { stream: null, mic: null, cam: null };
  const tracks = [];
  try { tracks.push(await openTrack('audio', media.micId)); } catch (e) { res.mic = e; }
  try { tracks.push(await openTrack('video', media.camId)); } catch (e) { res.cam = e; }
  if (tracks.length) res.stream = new MediaStream(tracks);
  return res;
}
function stopMedia() {
  if (media.stream) { media.stream.getTracks().forEach((t) => t.stop()); media.stream = null; }
}
/* 카메라 트랙만 끈다(마이크는 유지). 코딩 단계처럼 미리보기가 필요 없을 때 카메라 표시등까지 꺼지게 */
function stopVideo() {
  if (!media.stream) return;
  media.stream.getVideoTracks().forEach((t) => { t.stop(); media.stream.removeTrack(t); });
}

function onLeave(fn) { cleanup.push(fn); }
function go(hash) { location.hash = hash; }
function setNav(key) {
  $$('#top nav a[data-nav]').forEach((a) => a.classList.toggle('on', a.dataset.nav === key));
  const b = document.getElementById('practice-dd-btn');
  if (b) b.classList.toggle('on', /^#\/practice\//.test(location.hash));
}

/* ───────── 접이식 사이드 메뉴 (2026-11-19 사용자 지시) ─────────
   로그인한 지원자에게만 보이고, 면접·응시 단계(/apply/*, /interview/*)에서는 숨긴다.
   접었는지 여부는 localStorage에 남겨 다음 방문에도 유지한다. */
const SIDE_KEY = 'hh.side';
const sideHidden = (path) => /^\/(apply|interview)\//.test(path);

function setSideOpen(open) {
  const el = document.getElementById('sidenav');
  if (!el) return;
  el.classList.toggle('off', !open);
  document.body.classList.toggle('side-off', !open);
  const btn = document.getElementById('sidenav-toggle');
  if (btn) btn.setAttribute('aria-expanded', String(open));
  try { localStorage.setItem(SIDE_KEY, open ? '1' : '0'); } catch (_) { /* 저장 못 해도 동작엔 지장 없음 */ }
}

/* 사이드 메뉴가 시작할 세로 위치 = 헤더(개발 바 포함)의 아래쪽.
   헤더는 sticky라 스크롤하면 위로 붙으므로 스크롤할 때마다 다시 잰다. */
function sideTop() {
  const top = document.getElementById('top');
  if (!top) return;
  const b = Math.max(0, Math.round(top.getBoundingClientRect().bottom));
  document.documentElement.style.setProperty('--top-h', `${b}px`);
}

function syncSideNav(path) {
  const el = document.getElementById('sidenav');
  if (!el) return;
  const cur = path || location.hash.slice(1) || '/';
  const show = !!me && !sideHidden(cur);
  el.hidden = !show;
  document.body.classList.toggle('side-on', show);
  if (!show) return;
  sideTop();
  const h = `#${cur}`;
  el.querySelectorAll('a[href^="#/"]').forEach((a) => {
    const href = a.getAttribute('href');
    a.classList.toggle('on', h === href || h.startsWith(`${href}/`));
  });
}

(function initSideNav() {
  const el = document.getElementById('sidenav');
  if (!el) return;
  // 좁은 화면에서는 메뉴가 본문을 덮으므로 처음엔 접어 둔다(한 번 접거나 편 뒤에는 그 선택을 따른다)
  let open = window.innerWidth > 1100;
  try { const v = localStorage.getItem(SIDE_KEY); if (v !== null) open = v !== '0'; } catch (_) { /* 기본값 사용 */ }
  setSideOpen(open);
  // 덮는 화면에서는 메뉴를 고르면 바로 접는다
  el.querySelectorAll('a').forEach((a) => a.addEventListener('click', () => { if (window.innerWidth <= 1100) setSideOpen(false); }));
  document.getElementById('sidenav-toggle').addEventListener('click', () => setSideOpen(el.classList.contains('off')));
  document.getElementById('side-logout').addEventListener('click', async () => {
    await post('/api/auth/logout'); me = null; location.href = '/';
  });
  window.addEventListener('resize', () => syncSideNav());
  window.addEventListener('scroll', () => { if (!el.hidden) sideTop(); }, { passive: true });
})();

// 상단 '무제한 코딩 연습' 드롭다운: 클릭으로 열기/닫기, 바깥 클릭·Esc·항목 선택·화면 이동 시 닫힘
function setPracticeMenu(open) {
  const dd = document.getElementById('practice-dd');
  if (!dd) return;
  dd.classList.toggle('open', open);
  const b = document.getElementById('practice-dd-btn');
  b.setAttribute('aria-expanded', String(open));
  b.querySelector('.caret').textContent = open ? '▴' : '▾';
}
(function initPracticeMenu() {
  const dd = document.getElementById('practice-dd');
  if (!dd) return;
  document.getElementById('practice-dd-btn').addEventListener('click', (e) => { e.stopPropagation(); setPracticeMenu(!dd.classList.contains('open')); });
  dd.querySelectorAll('.nav-dd-item').forEach((a) => a.addEventListener('click', () => setPracticeMenu(false)));
  document.addEventListener('click', (e) => { if (!dd.contains(e.target)) setPracticeMenu(false); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && dd.classList.contains('open')) { setPracticeMenu(false); document.getElementById('practice-dd-btn').focus(); } });
  window.addEventListener('hashchange', () => setPracticeMenu(false));
})();

// 지원자 테스트 계정(시드 이지원): 접수 취소 버튼이 보인다. 서버도 이 계정만 허용(config.TEST_APPLICANT_EMAIL)
const T = (k, v) => HH.t(k, v);   // 언어 전환(2026-11-19)
const isTestApplicant = () => !!(me && me.email === 'applicant@headhunter.ai');
/* 전체 화면 전환. 테스트 계정(이지원)은 건너뛴다 — 스크린샷·창 전환을 자유롭게 하려고
   (2026-11-19 사용자 지시, 화면 이탈 감지는 이미 테스트 계정만 꺼져 있다) */
async function goFullscreen() {
  if (isTestApplicant()) return;
  try { await document.documentElement.requestFullscreen(); } catch (_) { /* fullscreen not allowed */ }
}
async function loadMe() {
  me = await get('/api/auth/me');
  if (me && me.role !== 'applicant') me = null;
  const area = $('#auth-area');
  area.innerHTML = me
    ? `<span class="who">${esc(me.name)} 님</span> <a class="btn sm ghost" href="#/account">계정관리</a> <button class="btn sm ghost" id="logout">로그아웃</button>`
    : `<a class="btn sm ghost" href="#/login">로그인</a> <a class="btn sm primary" href="#/signup">회원가입</a>`;
  const pdd = document.getElementById('practice-dd');
  if (pdd) { pdd.hidden = !me; if (!me) setPracticeMenu(false); }
  const reset = document.getElementById('dev-reset');
  if (reset) reset.hidden = !isTestApplicant();
  syncSideNav();
  const lo = $('#logout');
  // 2026-11-19 사용자 지시 — 로그아웃하면 지원자 홈(#/)이 아니라 공통 첫 페이지로 나간다
  if (lo) lo.onclick = async () => { await post('/api/auth/logout'); me = null; location.href = '/'; };
}

function requireLogin(next) {
  if (me) return true;
  go(`#/login?next=${encodeURIComponent(next || location.hash.slice(1))}`);
  return false;
}

// ACC-04: 허용된 내부 경로만 복귀
function safeNext(next) {
  return next && /^\/(jobs\/\d+(\/apply)?|jobs\/applied|my|billing|apply\/\d+\/[a-z]+|practice\/(sql(\/(s[12]|\d+))?|coding(\/(\d+|junior|senior))?))$/.test(next) ? next : '/my';
}

const routes = [
  [/^\/?$/, landing],
  [/^\/jobs$/, jobs],
  [/^\/jobs\/role\/([a-z_]+)$/, jobsRole],
  [/^\/jobs\/browse$/, jobsBrowse],
  [/^\/jobs\/company\/(\d+)$/, jobsCompany],
  [/^\/jobs\/applied$/, jobsApplied],
  [/^\/jobs\/(\d+)$/, jobDetail],
  [/^\/jobs\/(\d+)\/apply$/, applyJob],
  [/^\/login$/, login],
  [/^\/signup$/, signup],
  [/^\/my$/, mypage],
  [/^\/resume\/new$/, resumeNew],
  [/^\/account$/, accountView],
  [/^\/billing$/, billingView],
  [/^\/apply\/(\d+)\/consent$/, consentView],
  [/^\/apply\/(\d+)\/resume$/, resumeView],
  [/^\/apply\/(\d+)\/env$/, envView],
  [/^\/interview\/(\d+)$/, interviewView],
  [/^\/done\/(\d+)$/, doneView],
  [/^\/result\/(\d+)$/, resultView],
  [/^\/practice\/coding(?:\/(junior|senior))?$/, practiceList],
  [/^\/practice\/sql(?:\/s([12]))?$/, practiceSql],
  [/^\/practice\/sql\/(\d+)$/, practiceSqlProblem],
  [/^\/practice\/coding\/(\d+)$/, practiceProblem],
];

async function router() {
  cleanup.forEach((f) => { try { f(); } catch (_) { /* ignore */ } });
  cleanup = [];
  const raw = location.hash.slice(1) || '/';
  const [path, qs] = raw.split('?');
  const params = new URLSearchParams(qs || '');
  syncSideNav(path);
  for (const [re, fn] of routes) {
    const m = path.match(re);
    if (m) {
      window.scrollTo(0, 0);
      try { await fn(...m.slice(1), params); } catch (e) { view.innerHTML = `<div class="wrap narrow"><div class="err">${esc(e.message)}</div></div>`; }
      return;
    }
  }
  go('#/');
}

/* ───────── 공통 메인 (랜딩) ───────── */
async function landing() {
  // 로그인 안 한 상태로 지원자 홈에 들어오면 공통 첫 페이지로 보낸다 (2026-11-19 사용자 지시)
  // — 로그인 전 기본 화면은 /applicant/가 아니라 / 다.
  if (!me) { location.replace('/'); return; }
  setNav('');
  view.innerHTML = `
  <div class="wrap hero-wrap">
    <section class="hero">
      <div>
        <h1>인재를 발견하는 새로운 기준,<br><span class="neon">headhunterAI</span></h1>
        <p class="lead">이력서를 올리면 AI가 내용을 읽고 나에게 맞춘 질문으로 1차 면접을 진행합니다. 기업은 같은 기준과 근거로 1차 결과를 받아 봅니다.</p>
        <div class="row">
          <a class="btn primary lg" href="#/jobs">지원자로 시작하기 →</a>
          <a class="btn lg" href="/admin/">기업 담당자 로그인</a>
        </div>
      </div>
      <div class="hero-art" aria-hidden="true"></div>
    </section>
  </div>`;
}

/* ───────── 공고 ───────── */
// '지금 n명이 보고 있어요' — 서버가 실측 10명 이상일 때만 숫자를 준다(presence.py). 없으면 아무것도 그리지 않는다
const viewersHtml = (n, sm) => (n ? `<span class="live-viewers${sm ? ' sm' : ''}"><span class="live-dot" aria-hidden="true"></span>지금 <b>${n}명</b>이 보고 있어요</span>` : '');

function jobCard(j) {
  const closed = j.status === 'closed';
  return `<button class="card click" data-job="${j.id}">
    <div class="between"><span class="row" style="gap:10px"><span class="pill ${closed ? 'dim' : 'neon'}">${closed ? T('마감') : T('접수 중')}</span>${j.is_new ? '<span class="new-badge">NEW</span>' : ''}</span>
      ${j.role_status !== 'active' ? `<span class="pill dim">${T('준비 중 · 지원 불가')}</span>` : j.coding_enabled ? `<span class="pill">${T('코딩테스트 포함')}</span>` : `<span class="pill dim">${T('AI 면접만')}</span>`}</div>
    <h2 style="margin:12px 0 4px">${esc(j.title)}</h2>
    <div class="sub">${esc(j.company)} · ${esc(j.role_name)} · ${esc(j.location || '')}</div>
    <div class="small" style="margin-top:10px">${T('접수 마감')} ${fmtYMD(j.close_date)} · ${T('결과 공개')} ${fmtYMD(j.result_date)}</div>
    ${j.viewers && !closed ? `<div style="margin-top:12px">${viewersHtml(j.viewers, true)}</div>` : ''}
  </button>`;
}

/* 채용 공고 (PRD v0.9 APP-10): ① 직무 → ② 둘러보기 / 내가 지원한 공고 → ③ 기업 → ④ 그 기업 공고 */
const backLink = (href, label) => `<a class="btn sm ghost" href="${href}">← ${label}</a>`;

async function jobs() {
  setNav('jobs');
  if (!roles.length) roles = await get('/api/job-roles');
  const list = await get('/api/jobs');
  const count = (code) => list.filter((j) => j.role_code === code && j.status === 'published').length;
  // 2026-11-18 사용자 지시: 안내 문구를 서비스 소개 + 개인화 멘트로 교체, 마이페이지·무제한 코딩 연습 바로가기 추가
  const lead = T('headhunterAI는 IT 직무에 특화된 기업 1차 면접 대행 AI 면접 서비스입니다. 지원자님께 맞는 공고를 아래에서 확인해 보세요.');
  view.innerHTML = `<div class="wrap">
    <h1>${T('채용 공고')}</h1><p class="sub">${lead}</p>
    <div class="row" style="margin-top:16px;gap:10px">
      <a class="btn primary" href="#/my">${T('마이페이지로 바로가기')}</a>
      <a class="btn launch-btn launch-btn-dark" href="#/practice/coding">${T('무제한 코딩 연습 바로가기')}</a>
    </div>
    <div class="grid3" style="margin-top:20px">${roles.map((r) => {
      const n = count(r.code);
      if (r.status === 'active') return `<button class="card click" data-role="${r.code}"><span class="pill neon">${T('지원 가능')}</span><h2 style="margin:12px 0 4px">${esc(T(r.name))}</h2><div class="sub">${T('접수 중 공고 {n}개', { n })}</div></button>`;
      // 2026-11-18 사용자 지시: 공고는 있되 면접 질문 은행이 없는 직무 — 공고는 둘러볼 수 있게 하고, 실제 지원·면접만 막는다(더미)
      if (n) return `<button class="card click" data-role="${r.code}"><span class="pill dim">${T('준비 중')}</span><h2 style="margin:12px 0 4px">${esc(T(r.name))}</h2><div class="sub">${T('공고 {n}개 · 질문 은행 준비 중', { n })}</div></button>`;
      return `<div class="card" style="opacity:.45"><span class="pill dim">${T('준비 중')}</span><h2 style="margin:12px 0 4px">${esc(T(r.name))}</h2><div class="sub">${T('질문 은행 준비 중')}</div></div>`;
    }).join('')}</div></div>`;
  $$('[data-role]').forEach((b) => (b.onclick = () => go(`#/jobs/role/${b.dataset.role}`)));
}

async function jobsRole(code) {
  setNav('jobs');
  if (!roles.length) roles = await get('/api/job-roles');
  const r = roles.find((x) => x.code === code);
  if (!r) { go('#/jobs'); return; }
  // 2026-11-18 사용자 지시: 질문 은행 준비 중인 직무도 공고 둘러보기는 허용 — 지원·면접은 서버가 막는다(routes_applicant.py apply)
  view.innerHTML = `<div class="wrap">
    ${backLink('#/jobs', T('직무 선택'))}
    <h1 style="margin-top:14px">${esc(T(r.name))}${r.status !== 'active' ? ` <span class="pill dim" style="font-size:13px;vertical-align:middle">${T('준비 중')}</span>` : ''}</h1>
    <p class="sub">${r.status === 'active' ? T('열려 있는 공고를 둘러보거나, 지원한 공고의 진행 상황을 확인하세요.') : T('공고는 둘러볼 수 있으나, 이 직무의 AI 면접은 아직 준비 중이라 지원할 수 없습니다.')}</p>
    <div class="grid2" style="margin-top:20px">
      <button class="card click" data-go="#/jobs/browse?role=${code}"><h2 style="margin:0 0 6px">${T('현재 열려 있는 공고 둘러보기')}</h2><div class="sub">${T('기업을 고른 뒤 그 기업의 공고를 봅니다. 로그인 없이 볼 수 있습니다.')}</div></button>
      <button class="card click" data-go="#/jobs/applied?role=${code}"><h2 style="margin:0 0 6px">${T('내가 지원한 공고')}</h2><div class="sub">${T('지원한 공고의 진행 상태와 응시할 면접을 확인합니다. 로그인이 필요합니다.')}</div></button>
    </div></div>`;
  $$('[data-go]').forEach((b) => (b.onclick = () => go(b.dataset.go)));
}

async function jobsBrowse(params) {
  setNav('jobs');
  const role = params.get('role') || 'ai_ax';
  const list = await get(`/api/companies?role=${encodeURIComponent(role)}`);
  view.innerHTML = `<div class="wrap">
    ${backLink(`#/jobs/role/${role}`, T('이전'))}
    <h1 style="margin-top:14px">${T('기업 선택')}</h1><p class="sub">${T('공고를 올린 기업입니다. 기업을 고르면 그 기업의 공고를 모두 볼 수 있습니다.')}</p>
    <div class="grid3" style="margin-top:20px">${list.length ? list.map((c) => `<button class="card click" data-company="${c.id}">
      <div class="between"><span class="pill ${c.open_jobs ? 'neon' : 'dim'}">${T('지원 가능 {n}개', { n: c.open_jobs })}</span><span class="small">${T('공고 {n}개', { n: c.jobs })}</span></div>
      <h2 style="margin:12px 0 4px">${esc(c.name)}</h2><div class="sub">${esc(c.location || '')}</div>
      <div class="small" style="margin-top:10px">${esc(c.intro || '')}</div></button>`).join('')
      : `<div class="empty" style="grid-column:1/-1">${T('공고를 올린 기업이 없습니다.')}</div>`}</div></div>`;
  $$('[data-company]').forEach((b) => (b.onclick = () => go(`#/jobs/company/${b.dataset.company}?role=${role}`)));
}

async function jobsCompany(id, params) {
  setNav('jobs');
  const role = params.get('role') || 'ai_ax';
  const list = await get(`/api/jobs?company=${id}`);
  if (!list.length) { go(`#/jobs/browse?role=${role}`); return; }
  view.innerHTML = `<div class="wrap">
    ${backLink(`#/jobs/browse?role=${role}`, T('기업 선택'))}
    <h1 style="margin-top:14px">${esc(list[0].company)}</h1><p class="sub">${T('이 기업의 공고 {n}개입니다. 준비 중인 직무의 공고는 볼 수만 있고 지원할 수 없습니다.', { n: list.length })}</p>
    <div class="grid3" style="margin-top:20px">${list.map(jobCard).join('')}</div></div>`;
  $$('[data-job]').forEach((b) => (b.onclick = () => go(`#/jobs/${b.dataset.job}`)));
}

async function jobsApplied(params) {
  setNav('jobs');
  if (!requireLogin('/jobs/applied')) return;
  const role = params.get('role') || 'ai_ax';
  const apps = await get('/api/me/applications');
  view.innerHTML = `<div class="wrap">
    ${backLink(`#/jobs/role/${role}`, T('이전'))}
    <h1 style="margin-top:14px">${T('내가 지원한 공고')}</h1><p class="sub">${T('{name} 님이 지원한 공고입니다.', { name: esc(me.name) })}</p>
    <div class="card" style="margin-top:20px">${appsTableHtml(apps)}</div></div>`;
  bindAppsTable(() => jobsApplied(params));
}

const lines = (t) => esc(t || '').replace(/\n/g, '<br>');

async function jobDetail(id) {
  setNav('jobs');
  const j = await get(`/api/jobs/${id}`);
  const closed = j.status === 'closed';
  view.innerHTML = `<div class="wrap mid">
    <a class="btn sm ghost" href="#/jobs/company/${j.company_id}">← ${T('{company} 공고', { company: esc(j.company) })}</a>
    <h1 style="margin-top:14px">${esc(j.title)}${j.is_new ? '<span class="new-badge">NEW</span>' : ''}</h1>
    <div class="sub">${esc(j.company)} · ${esc(T(j.role_name))} · ${esc(j.employment_type)} · ${esc(j.location)} · ${T('모집 {n}명', { n: j.headcount })}</div>
    <div id="viewers" style="margin-top:14px"></div>
    <div class="grid3" style="margin:22px 0">
      <div class="card"><div class="small">${T('접수 기간')}</div><b>${fmtYMD(j.open_date)} ~ ${fmtYMD(j.close_date)}</b></div>
      <div class="card"><div class="small">${T('진행 방식 · 예상 소요')}</div><b>${T('AI 면접')} ${j.coding_enabled ? T('+ 코딩테스트') : ''} · ${T('약 {n}분', { n: j.expected_minutes })}</b></div>
      <div class="card"><div class="small">${T('1차 결과 공개')}</div><b>${fmtYMD(j.result_date)} 10:00</b></div>
    </div>
    <div class="card col">
      <h3>${T('회사 소개')}</h3><p class="sub">${lines(j.company_intro)}</p>
      <h3>${T('주요 업무')}</h3><p class="sub">${lines(j.duties)}</p>
      <h3>${T('자격 요건')}</h3><p class="sub">${lines(j.requirements)}</p>
      ${j.preferred ? `<h3>${T('우대 사항')}</h3><p class="sub">${lines(j.preferred)}</p>` : ''}
    </div>
    <div class="note" style="margin:18px 0">${T('지원 후 이력서를 올리면 AI가 내용을 읽어 맞춤 질문 2개를 만듭니다. AI 면접은 1회만 응시할 수 있고 중단 버튼이 없습니다.')}</div>
    <button class="btn primary lg" id="apply" ${closed || j.role_status !== 'active' ? 'disabled' : ''}>${closed ? T('접수 마감됨') : j.role_status !== 'active' ? T('준비 중인 직무 · 지원 불가') : T('지원하기')}</button>
  </div>`;
  $('#apply').onclick = () => go(`#/jobs/${id}/apply`);
  if (!closed) {                      // 보고 있다는 신호: 탭이 보일 때만 30초마다
    const beat = async () => {
      if (document.hidden) return;
      try { const r = await post(`/api/jobs/${id}/viewing`); const el = $('#viewers'); if (el) el.innerHTML = viewersHtml(r.viewers); } catch (_) { /* 표시 안 함 */ }
    };
    beat();
    const timer = setInterval(beat, 30000);
    onLeave(() => clearInterval(timer));
  }
}

async function applyJob(id) {
  if (!requireLogin(`/jobs/${id}/apply`)) return;
  const r = await post('/api/applications', { job_id: +id });
  if (r.existing) toast('이미 지원한 공고입니다. 이어서 진행합니다.');
  const app = await get(`/api/applications/${r.id}`);
  continueApplication(app);
}

function continueApplication(app) {
  const step = app.step;
  if (step === 'consent') return go(`#/apply/${app.id}/consent`);
  if (step === 'resume') return go(`#/apply/${app.id}/resume`);
  if (step === 'env' || step === 'ready') return go(`#/apply/${app.id}/env`);
  return go('#/my');
}

/* ───────── 로그인·가입 ───────── */
async function login(params) {
  setNav('');
  const next = safeNext(params.get('next'));
  view.innerHTML = `<div class="wrap narrow"><div class="card">
    <h1>${T('지원자 로그인')}</h1><p class="sub">${T('로그인 후 지원 절차로 돌아갑니다.')}</p>
    <div class="err" id="err" hidden></div>
    <div class="field" style="margin-top:16px"><label>${T('이메일')}</label><input id="email" type="email" autocomplete="username"></div>
    <div class="field"><label>${T('비밀번호')}</label><input id="pw" type="password" autocomplete="current-password"></div>
    <button class="btn primary" id="go" style="width:100%">${T('로그인')}</button>
    <hr><div class="between"><span class="small">${T('아직 회원이 아니신가요?')}</span><a class="btn sm" href="#/signup?next=${encodeURIComponent(next)}">${T('지원자로 회원가입하기')}</a></div>
    <div class="small" style="margin-top:12px">${T('기업 담당자는 {link}을 이용해 주세요.', { link: `<a href="/admin/">${T('기업 담당자 로그인')}</a>` })}</div>
  </div></div>`;
  const submit = async () => {
    try {
      await post('/api/auth/login', { email: $('#email').value, password: $('#pw').value, role: 'applicant' });
      await loadMe(); go('#' + next);
    } catch (e) { const el = $('#err'); el.textContent = e.message; el.hidden = false; }
  };
  $('#go').onclick = submit;
  $('#pw').onkeydown = (e) => { if (e.key === 'Enter') submit(); };
}

async function signup(params) {
  setNav('');
  const next = safeNext(params.get('next'));
  view.innerHTML = `<div class="wrap narrow"><div class="card">
    <h1>${T('지원자 회원가입')}</h1><p class="sub">${T('평가와 무관한 생년월일·성별·주소·사진은 받지 않습니다.')}</p>
    <div class="err" id="err" hidden></div>
    <div class="field" style="margin-top:16px"><label>${T('이름 *')}</label><input id="name" autocomplete="name"></div>
    <div class="field"><label>${T('이메일 (아이디) *')}</label><input id="email" type="email" autocomplete="email"></div>
    <div class="field"><label>${T('비밀번호 * (8자 이상)')}</label><input id="pw" type="password" autocomplete="new-password"></div>
    <div class="field"><label>${T('휴대전화번호 *')}</label><input id="phone" placeholder="010-0000-0000" autocomplete="tel"></div>
    <label class="check"><input type="checkbox" id="age14"><span>${T('만 14세 이상입니다.')} <span class="small">${T('(필수)')}</span></span></label>
    <label class="check"><input type="checkbox" id="terms"><span>${T('이용약관에 동의합니다.')} <span class="small">${T('(필수)')}</span> <a class="small" href="/terms" target="_blank" rel="noopener">${T('전문 보기')}</a></span></label>
    <label class="check"><input type="checkbox" id="privacy"><span>${T('회원 서비스 제공을 위한 개인정보 수집·이용(이름·이메일·비밀번호·휴대전화번호, 탈퇴 시까지 보관)에 동의합니다. 서비스 서버는 미국(Railway)에 있습니다.')} <span class="small">${T('(필수)')}</span> <a class="small" href="/privacy" target="_blank" rel="noopener">${T('전문 보기')}</a></span></label>
    <button class="btn primary" id="go" style="width:100%;margin-top:10px">${T('가입하기')}</button>
    <p class="small" style="margin-top:10px">${T('응시 데이터 수집 동의는 공고에 지원할 때 따로 받습니다.')}</p>
  </div></div>`;
  $('#phone').oninput = (e) => {
    const d = e.target.value.replace(/\D/g, '').slice(0, 11);
    e.target.value = d.length > 7 ? `${d.slice(0, 3)}-${d.slice(3, 7)}-${d.slice(7)}` : d.length > 3 ? `${d.slice(0, 3)}-${d.slice(3)}` : d;
  };
  $('#go').onclick = async () => {
    try {
      await post('/api/auth/signup', { name: $('#name').value, email: $('#email').value, password: $('#pw').value, phone: $('#phone').value,
        age14: $('#age14').checked, terms: $('#terms').checked, privacy: $('#privacy').checked });
      await loadMe(); toast('가입이 완료되었습니다.'); go('#' + next);
    } catch (e) { const el = $('#err'); el.textContent = e.message; el.hidden = false; }
  };
}

/* ───────── 마이페이지 ───────── */
function appAction(a) {
  if (!a.attempt_id) {
    const label = { consent: '동의하고 계속하기', resume: '이력서 등록하기', env: '환경 점검하기', ready: '면접 시작하기' }[a.step] || '계속하기';
    return `<button class="btn sm primary" data-cont="${a.id}">${label}</button>`;
  }
  if (a.attempt_status === 'interrupted') return `<a class="btn sm primary" href="#/interview/${a.attempt_id}">면접 이어가기</a>`;
  if (a.attempt_status === 'interview' || a.attempt_status === 'coding') return `<a class="btn sm" href="#/interview/${a.attempt_id}">응시 화면으로</a>`;
  if (a.result_open) return `<a class="btn sm primary" href="#/result/${a.id}">1차 결과 보기</a>`;
  if (a.analysis_status !== 'done') return `<a class="btn sm" href="#/result/${a.id}">AI 분석 중</a>`;
  return `<a class="btn sm" href="#/result/${a.id}">${a.decision_mode === 'hybrid' ? '담당자 확인 중' : '리포트 보기'}</a>`;
}

function appsTableHtml(apps) {
  return apps.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>공고</th><th>진행 상태</th><th>응시 상태</th><th></th></tr></thead><tbody>
        ${apps.map((a) => `<tr><td><b>${esc(a.title)}</b><div class="small">${esc(a.company)} · 지원일 ${fmtYMD(a.created_at)}${a.coding_enabled ? ' · 코딩테스트 포함' : ''}</div></td>
          <td>${a.attempt_id ? '<span class="pill dim">응시함</span>' : `<span class="pill neon">${STEP_KO[a.step]}</span>`}</td>
          <td>${a.attempt_status ? esc(ATTEMPT_KO[a.attempt_status]) : '-'}</td><td class="num">${appAction(a)}${isTestApplicant() ? ` <button class="btn sm ghost" data-devcancel="${a.id}" title="테스트 계정 전용">접수 취소</button>` : ''}</td></tr>`).join('')}
        </tbody></table></div>` : '<div class="empty">아직 지원한 공고가 없습니다.</div>';
}

/* 테스트 계정 전용 '접수 취소'. 목록 카드와 '더보기' 미니 페이지가 같이 쓴다 */
async function devCancelApp(appId, refresh) {
  const yes = await modal({ title: '접수 취소(테스트용)', body: '이 지원 건의 응시·답변·평가 기록을 모두 지웁니다. 같은 공고에 다시 지원할 수 있게 됩니다.',
    buttons: [{ label: '닫기', value: false }, { label: '접수 취소', value: true, primary: true }] });
  if (!yes) return;
  try { await post(`/api/dev/reset-my-applications?app_id=${appId}`); toast('접수를 취소했습니다.'); refresh(); } catch (e) { toast(e.message, true); }
}

function bindAppsTable(refresh) {
  $$('[data-cont]').forEach((b) => (b.onclick = async () => continueApplication(await get(`/api/applications/${b.dataset.cont}`))));
  $$('[data-devcancel]').forEach((b) => (b.onclick = () => devCancelApp(b.dataset.devcancel, refresh)));
}

/* 마이페이지 '나의 공고'·'나의 면접' 한 줄. 카드 안과 '더보기' 미니 페이지가 같은 모양을 쓴다 */
const ROW_STYLE = 'padding:10px 0;border-bottom:1px solid var(--line)';

function myJobRow(a) {
  // 2026-11-18 사용자 지시: 단계별 상세(동의 필요 등) 대신 미완료만, 버튼도 다음 단계 이름 말고 '이어서 진행하기'로 통일.
  // 제목줄과 상태·버튼줄을 항상 위아래로 쌓는다(한 줄에 욱여넣어 좁은 화면에서 지저분하게 줄바꿈되던 것을 없앰) —
  // 그만큼 줄 하나가 길어지니 '나의 공고'가 길면 CUT(3)로 자르고 미니 페이지로 더 보여준다(아래 mypage 참고)
  // 2026-11-19 사용자 지시(B): '나의 공고'와 '나의 면접'을 한 줄 양쪽 두 블록으로 되돌렸다 →
  // 블록 안에 들어가므로 카드가 아니라 가벼운 한 줄(제목줄 + 상태·버튼줄)로 쌓는다
  const status = a.attempt_id ? '<span class="pill dim">응시함</span>' : '<span class="pill dim">미완료</span>';
  const action = a.attempt_id ? appAction(a) : `<button class="btn sm primary" data-cont="${a.id}">이어서 진행하기</button>`;
  return `<div style="${ROW_STYLE}">
    <b>${esc(a.title)}</b><div class="small">${esc(a.company)} · 지원일 ${fmtYMD(a.created_at)}${a.coding_enabled ? ' · 코딩테스트 포함' : ''}</div>
    <div class="row" style="gap:8px;margin-top:10px;flex-wrap:wrap">${status}${action}${isTestApplicant() ? `<button class="btn sm ghost" data-devcancel="${a.id}" title="테스트 계정 전용">접수 취소</button>` : ''}</div></div>`;
}

/* '나의 면접' 한 줄 — 응시 기록이 있거나 바로 응시할 수 있는 지원 건 (2026-11-19 사용자 지시로 블록 복구) */
function myInterviewRow(a) {
  const state = a.attempt_status ? ATTEMPT_KO[a.attempt_status] : '응시 가능';
  return `<div style="${ROW_STYLE}">
    <b>${esc(a.title)}</b><div class="small">${esc(a.company)} · 결과 공개 ${fmtYMD(a.result_date)}</div>
    <div class="row" style="gap:8px;margin-top:10px;flex-wrap:wrap"><span class="pill ${a.attempt_status ? 'dim' : 'neon'}">${esc(state)}</span>${appAction(a)}</div></div>`;
}

/* 알림 한 줄 — 유형별 안내(2026-11-18 사용자 지시: 면접 진행상황을 '나의 면접' 블록 대신 여기로) */
const NOTE_KO = { deadline_1d: '마감 임박', deadline_today: '오늘 마감', result: '결과 도착', apply: '신규 지원', ops: '확인 필요' };
function myNoteRow(n) {
  // 2026-11-18 사용자 지시: '결과 도착' 알림을 누르면 마이페이지가 아니라 그 결과 화면으로 바로 이동
  const clickable = n.type === 'result' && n.application_id;
  const tag = clickable ? 'button' : 'div';
  return `<${tag} ${clickable ? `class="note-row click" data-noteapp="${n.application_id}"` : `style="${ROW_STYLE}"`}>
    <div class="row" style="gap:8px"><span class="pill ${n.read_at ? 'dim' : 'neon'}">${esc(NOTE_KO[n.type] || '알림')}</span><b>${esc(n.title)}</b></div>
    <div class="small" style="margin-top:6px">${esc(n.body)}</div></${tag}>`;
}

/* '더보기' 미니 페이지: 그 목록만 모아 띄운다. 안에서 버튼·링크를 누르면 창을 닫고 그 화면으로 간다 */
function miniPage(title, bodyHtml) {
  modal({ title, body: bodyHtml, buttons: [{ label: '닫기', value: true }] });
  const bg = $$('.modal-bg').pop();
  if (!bg) return;
  $$('a[href^="#/"]', bg).forEach((el) => el.addEventListener('click', () => bg.remove()));
  $$('[data-cont]', bg).forEach((b) => (b.onclick = async () => { bg.remove(); continueApplication(await get(`/api/applications/${b.dataset.cont}`)); }));
  $$('[data-devcancel]', bg).forEach((b) => (b.onclick = () => { bg.remove(); devCancelApp(b.dataset.devcancel, mypage); }));
  $$('[data-noteapp]', bg).forEach((b) => (b.onclick = () => { bg.remove(); go(`#/result/${b.dataset.noteapp}`); }));
}

/* '내가 좋아할만한 공고' 가로 롤링(사람인 추천 공고 방식, 2026-11-18 사용자 지시).
   움직임 줄이기 설정이면 멈춘 채 첫 화면만 보여 주고, 마우스를 올리면 잠시 멈춘다 */
function startReco(vp) {
  const track = vp.firstElementChild;
  const items = $$(':scope > *', track);
  if (items.length < 2) return;
  let i = 0, paused = false;
  const perView = () => Math.max(1, Math.round(vp.clientWidth / (items[0].offsetWidth || vp.clientWidth)));
  const maxIndex = () => Math.max(0, items.length - perView());
  // offsetParent가 무엇이든 틀어지지 않게 첫 카드 기준 상대 위치로 민다
  const show = () => { track.style.transform = `translateX(${-(items[i].offsetLeft - items[0].offsetLeft)}px)`; };
  show();
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  const id = setInterval(() => { if (!paused) { i = i >= maxIndex() ? 0 : i + 1; show(); } }, 4000);
  vp.onmouseenter = () => { paused = true; };
  vp.onmouseleave = () => { paused = false; };
  const onResize = () => { if (i > maxIndex()) i = 0; show(); };
  addEventListener('resize', onResize);
  onLeave(() => { clearInterval(id); removeEventListener('resize', onResize); });
}

const shuffle = (arr) => arr.map((v) => [Math.random(), v]).sort((x, y) => x[0] - y[0]).map((x) => x[1]);

async function mypage() {
  setNav('my');
  if (!requireLogin('/my')) return;
  const [apps, notes, resumes, allJobs] = await Promise.all([
    get('/api/me/applications'), get('/api/me/notifications'), get('/api/me/resumes'), get('/api/jobs'),
  ]);
  // 추천 공고: 헤드헌터랩 지점(서울·경기·인천) 접수 중 공고를 무작위로. 지원 여부와 무관하게 보여 준다
  const open = allJobs.filter((j) => j.status === 'published' && j.role_status === 'active');
  const branch = open.filter((j) => /^헤드헌터랩_/.test(j.company || ''));
  const reco = shuffle(branch.length ? branch : open).slice(0, 9);
  // 나의 면접 = 응시 기록이 있거나 바로 응시할 수 있는 지원 건 (2026-11-19 사용자 지시로 블록 복구)
  const mine = apps.filter((a) => a.attempt_id || a.step === 'ready');
  const CUT = 3;
  // 2026-11-18 사용자 지시: '나의 면접' 블록을 없애고 그 내용(진행 상황)은 Notification으로 옮긴다(아래
  // scheduler.deadline_reminders가 마감 1일 전·당일 알림을 만든다. 결과 도착 알림은 기존 그대로).
  // Notification·내 이력서 관리를 맨 위로, '나의 공고'는 짝이 없어졌으니 한 줄 꽉 채운 카드 하나로.
  const blockHtml = (name, list, rowFn, moreId, emptyMsg, opt = {}) => `<div class="card${opt.hl ? ' hl' : ''}">
    <div class="between"><h2${opt.hl ? ' class="neon"' : ''}>${name}</h2>${list.length > CUT ? `<button class="btn sm ghost" id="${moreId}">더보기</button>` : ''}</div>
    ${list.length ? list.slice(0, CUT).map(rowFn).join('') : `<p class="small">${emptyMsg}</p>`}
    ${list.length > CUT ? `<p class="small" style="margin-top:10px">전체 ${list.length}건 중 ${CUT}건을 보여 드립니다.</p>` : ''}</div>`;

  view.innerHTML = `<div class="wrap my-wrap">
    <h1>마이페이지</h1><p class="sub" style="font-size:19px;font-weight:700">${esc(me.name)} 님의 지원 현황입니다.</p>
    <div class="grid2" style="margin-top:24px;align-items:start">
      ${blockHtml('Notification', notes, myNoteRow, 'more-notes', '새 알림이 없습니다.', { hl: true })}
      <div class="card"><div class="between"><h2>내 이력서 관리</h2><a class="btn sm" href="#/resume/new">+ 내 이력서 등록하기</a></div>
        ${resumes.length ? resumes.map((r) => `<div class="between" style="${ROW_STYLE}"><span><b>${esc(r.title)}</b> <span class="small">${fmtYMD(r.updated_at)}</span></span><button class="btn sm ghost" data-delres="${r.id}">삭제</button></div>`).join('') : '<p class="small">등록한 이력서가 없습니다. 위 버튼으로 지금 등록하거나, 지원 과정에서 저장할 수 있습니다.</p>'}</div>
    </div>
    <div class="card" style="margin-top:16px">
      <div class="between"><h2>내가 좋아할만한 공고</h2><a class="btn sm" href="#/jobs">공고 둘러보기</a></div>
      ${reco.length ? `<div class="reco-vp" id="reco-vp" style="margin-top:12px"><div class="reco-track">${reco.map(jobCard).join('')}</div></div>`
      : '<p class="small" style="margin-top:12px">지금 접수 중인 공고가 없습니다.</p>'}
    </div>
    <div class="grid2" style="margin-top:16px;align-items:start">
      ${blockHtml(`나의 공고 ${apps.length ? `<span class="small" style="font-weight:500">(총 지원 공고 ${apps.length}개)</span>` : ''}`,
    apps, myJobRow, 'more-jobs', '아직 지원한 공고가 없습니다.')}
      ${blockHtml('나의 면접', mine, myInterviewRow, 'more-ivs', '아직 응시할 면접이 없습니다.')}
    </div></div>`;

  if ($('#reco-vp')) startReco($('#reco-vp'));
  $$('[data-job]').forEach((b) => (b.onclick = () => go(`#/jobs/${b.dataset.job}`)));
  $$('[data-noteapp]').forEach((b) => (b.onclick = () => go(`#/result/${b.dataset.noteapp}`)));
  bindAppsTable(mypage);
  if ($('#more-jobs')) $('#more-jobs').onclick = () => miniPage('나의 공고', apps.map(myJobRow).join(''));
  if ($('#more-ivs')) $('#more-ivs').onclick = () => miniPage('나의 면접', mine.map(myInterviewRow).join(''));
  if ($('#more-notes')) $('#more-notes').onclick = () => miniPage('Notification', notes.map(myNoteRow).join(''));
  $$('[data-delres]').forEach((b) => (b.onclick = async () => { await del(`/api/me/resumes/${b.dataset.delres}`); mypage(); }));
  if (notes.some((n) => !n.read_at)) post('/api/me/notifications/read');
}

/* ───────── 계정관리 (상단 '계정관리', 2026-11-18 사용자 지시 — 마이페이지 하단 회원 탈퇴를 여기로 옮김) ───────── */
async function accountView() {
  setNav('');
  if (!requireLogin('/account')) return;
  view.innerHTML = `<div class="wrap mid">
    <h1>계정관리</h1><p class="sub" style="font-size:19px;font-weight:700">${esc(me.name)} 님의 계정 정보입니다.</p>
    <div class="card col" style="margin-top:20px"><h2>로그인 정보</h2>
      <dl class="kv"><dt>이름</dt><dd>${esc(me.name)}</dd><dt>이메일</dt><dd>${esc(me.email)}</dd></dl></div>
    <div class="card col" style="margin-top:16px"><h2>비밀번호 변경</h2>
      <p class="small">8자 이상이고, 지금 쓰는 비밀번호와 달라야 합니다.</p>
      <input class="input" type="password" id="pw-cur" placeholder="현재 비밀번호" autocomplete="current-password">
      <input class="input" type="password" id="pw-new" placeholder="새 비밀번호" autocomplete="new-password">
      <div class="row"><button class="btn primary" id="pw-save">비밀번호 변경</button></div></div>
    <div class="card col" style="margin-top:16px"><h2>회원 탈퇴</h2>
      <p class="small">탈퇴하면 진행 중인 응시는 취소되고, 계정·이력서·지원 기록이 삭제되며 되돌릴 수 없습니다.</p>
      <div class="row"><button class="btn ghost" id="withdraw-btn">회원 탈퇴</button></div></div>
    <p class="small" style="margin-top:20px"><a href="#/my">← 마이페이지</a></p></div>`;
  $('#pw-save').onclick = async () => {
    try {
      await post('/api/auth/change-password', { current: $('#pw-cur').value, new: $('#pw-new').value });
      $('#pw-cur').value = ''; $('#pw-new').value = '';
      toast('비밀번호를 변경했습니다.');
    } catch (e) { toast(e.message, true); }
  };
  $('#withdraw-btn').onclick = () => withdrawFlow();
}

/* ───────── 요금제·결제 (PRD v0.9 PAY-07~12) ───────── */
async function billingView(params) {
  setNav('billing');
  if (!requireLogin('/billing')) return;
  view.innerHTML = '<div class="wrap mid" id="billing"></div>';
  await HH.billing($('#billing'), params);
}

/* ───────── 회원 탈퇴 (PRD v0.9 ACC-10) ───────── */
async function withdrawFlow() {
  const wait = modal({ title: '회원 탈퇴',
    body: `탈퇴하면 진행 중인 응시는 취소되고, 계정·이력서·지원 기록이 바로 삭제되며 되돌릴 수 없습니다. 정기결제는 해지되고, 결제 기록은 법령에 따라 5년간 보관됩니다.<br><br>
      <input class="input" type="password" id="wd-pw" placeholder="비밀번호 확인" autocomplete="current-password" style="width:100%">`,
    buttons: [{ label: '취소', value: false }, { label: '탈퇴하기', value: true, primary: true }] });
  const pwBox = document.getElementById('wd-pw');   // modal은 닫힐 때 요소를 지우므로 먼저 잡아 둔다
  if (!(await wait)) return;
  const pw = pwBox ? pwBox.value : '';
  try {
    await post('/api/auth/withdraw', { password: pw });
    me = null; await loadMe(); toast('탈퇴가 완료되었습니다. 그동안 이용해 주셔서 감사합니다.'); go('#/');
  } catch (e) { toast(e.message, true); }
}

/* ───────── 지원 단계 공통 ───────── */
function stepsBar(now) {
  const s = [['consent', '동의'], ['resume', '이력서'], ['env', '환경 점검'], ['interview', 'AI 면접'], ['done', '응시 완료']];
  const idx = s.findIndex((x) => x[0] === now);
  return `<div class="steps">${s.map((x, i) => `<span class="${i < idx ? 'done' : i === idx ? 'now' : ''}">${i + 1}. ${x[1]}</span>`).join('')}</div>`;
}

/* ───────── 동의 (APP-03) ───────── */
const CONSENT_TEXT = [
  ['data', '응시 데이터 수집·이용', '수집 항목: 이력서 원본과 인식 결과, 확인한 이력서 내용, 답변 텍스트, 코드, 응시 기록(시작·종료·화면 이탈·연결 끊김). 목적: 맞춤 질문 생성과 1차 평가. 답변 음성은 AI가 글로 바꾼 직후 삭제하고, 카메라 영상은 화면에 표시만 하며 녹화·저장하지 않습니다.'],
  ['ai', 'AI 질문 생성·자동화된 결정', '이력서 내용에 근거해 AI(로컬 실행 Qwen 모델)가 질문을 만들고 답변을 평가합니다. 공고가 자동 판정이면 점수와 공고 기준으로 1차 합격·불합격이 사람 검토 없이 정해질 수 있습니다. 이 결정을 거부하거나 설명을 요구할 수 있으며, 요청하면 기업 담당자가 직접 검토합니다.'],
  ['share', '지원 기업에 개인정보 제공', '제공받는 자: 지원한 기업. 목적: 1차 채용 심사. 항목: 이름·이메일·확인한 이력서 내용·답변 텍스트·코드·평가 점수와 근거·1차 결과. 보유: 결과 공개일부터 7일(서비스 안). 동의하지 않을 수 있으나 그러면 이 공고에 지원할 수 없습니다.'],
  ['retention', '보관 기간·국외 보관', '응시 데이터는 결과 공개일부터 7일 뒤 자동 삭제됩니다. 서비스 서버가 미국(Railway)에 있어 데이터가 국외에 보관되고, AI 처리 점검을 위해 AI 입력·결과 기록(이력서 내용·답변 텍스트·점수)이 미국 LangSmith(LangChain, Inc.)에 보관됩니다(개인정보 처리방침 5절).'],
  ['rules', '응시 규칙', '지원 건당 1회 응시, 중단 버튼 없음, 화면 이탈 1회 경고·2회 강제 퇴장(응시 취소).'],
];

async function consentView(appId) {
  if (!requireLogin()) return;
  const app = await get(`/api/applications/${appId}`);
  view.innerHTML = `<div class="wrap mid">${stepsBar('consent')}
    <h1>응시 데이터 수집·이용 동의</h1><p class="sub">${esc(app.job.company)} · ${esc(app.job.title)}</p>
    <div class="card" style="margin-top:18px">
      ${CONSENT_TEXT.map(([k, t, d]) => `<label class="check"><input type="checkbox" data-c="${k}"><span><b>${t}</b> <span class="small">(필수)</span><br><span class="sub">${d}</span></span></label>`).join('<hr style="margin:6px 0">')}
      <hr><label class="check"><input type="checkbox" id="all"><b>모든 항목에 동의합니다</b></label>
    </div>
    <div class="note" style="margin:14px 0">필수 항목에 모두 동의해야 다음 단계로 갈 수 있습니다. 동의를 철회하면 진행 중인 응시는 취소되고 데이터는 즉시 삭제됩니다. (동의 문구 버전은 기록됩니다) <a href="/privacy" target="_blank" rel="noopener">개인정보 처리방침 전문 보기</a></div>
    <div class="row"><button class="btn primary" id="next" disabled>동의하고 다음</button><a class="btn ghost" href="#/my">나중에 하기</a></div></div>`;
  const boxes = $$('[data-c]');
  const sync = () => { $('#next').disabled = !boxes.every((b) => b.checked); $('#all').checked = boxes.every((b) => b.checked); };
  boxes.forEach((b) => (b.onchange = sync));
  $('#all').onchange = (e) => { boxes.forEach((b) => (b.checked = e.target.checked)); sync(); };
  $('#next').onclick = async () => {
    const items = Object.fromEntries(boxes.map((b) => [b.dataset.c, b.checked]));
    await post(`/api/applications/${appId}/consent`, { items });
    go(`#/apply/${appId}/resume`);
  };
}

/* ───────── 이력서 (APP-04, 4.4 OCR) ───────── */
const FIELDS = {
  education: [['school', '학교'], ['major', '전공'], ['degree', '학위'], ['period', '기간'], ['detail', '세부 내용(졸업 과제 등)', 'ta']],
  career: [['company', '회사'], ['role', '직무·역할'], ['period', '기간'], ['tech', '사용 기술(쉼표로 구분)'], ['detail', '주요 업무·성과', 'ta']],
  projects: [['name', '프로젝트명'], ['role', '역할'], ['period', '기간'], ['tech', '사용 기술(쉼표로 구분)'], ['detail', '내용·성과', 'ta']],
  certificates: [['name', '자격·수상명'], ['date', '취득일']],
};
const blank = () => ({ basic: { name: '', email: '', phone: '' }, education: [], career: [], projects: [], skills: [], certificates: [] });

function itemHtml(sec, it, i) {
  const f = FIELDS[sec];
  const val = (k) => (k === 'tech' ? (it.tech || []).join(', ') : it[k] || '');
  return `<div class="item ${it.needs_check ? 'check-need' : ''}" data-sec="${sec}" data-i="${i}">
    <div class="between"><span class="small">${SECTION_KO[sec]} ${i + 1}${it.page ? ` · 원문 ${it.page}쪽` : ''}${it.needs_check ? ' · <b class="neon">확인 필요</b>' : ''}${it.origin === 'auto' ? ' · 자동 인식' : ''}</span>
      <button class="btn sm ghost" data-rm="${sec}:${i}">삭제</button></div>
    <div class="grid2">${f.filter((x) => !x[2]).map(([k, l]) => `<input data-k="${k}" placeholder="${l}" value="${esc(val(k))}">`).join('')}</div>
    ${f.filter((x) => x[2]).map(([k, l]) => `<textarea data-k="${k}" placeholder="${l}">${esc(val(k))}</textarea>`).join('')}
    ${it.source ? `<div class="quote" title="원문">원문: ${esc(it.source)}</div>` : ''}
  </div>`;
}

function formHtml(d) {
  return `<div class="col" id="rform">
    <div class="card"><h3>기본 정보</h3><div class="grid3">
      <div class="field"><label>성명 *</label><input data-b="name" value="${esc(d.basic.name)}"></div>
      <div class="field"><label>이메일</label><input data-b="email" value="${esc(d.basic.email)}"></div>
      <div class="field"><label>연락처</label><input data-b="phone" value="${esc(d.basic.phone)}"></div></div>
      <p class="small">기본 정보는 질문 생성과 평가에 쓰이지 않습니다.</p></div>
    ${['education', 'career', 'projects', 'certificates'].map((sec) => `<div class="card"><div class="between"><h3>${SECTION_KO[sec]}${sec === 'education' || sec === 'career' ? ' <span class="small">(학력 또는 경력 1건 이상 필수)</span>' : ''}</h3>
      <button class="btn sm" data-add="${sec}">+ 추가</button></div>
      <div data-list="${sec}">${(d[sec] || []).map((it, i) => itemHtml(sec, it, i)).join('') || '<p class="small">항목이 없습니다.</p>'}</div></div>`).join('')}
    <div class="card"><h3>기술</h3><input class="input" id="skills" placeholder="Python, PyTorch, ..." value="${esc((d.skills || []).join(', '))}"></div>
  </div>`;
}

function readForm(base) {
  const d = blank();
  $$('[data-b]').forEach((i) => (d.basic[i.dataset.b] = i.value.trim()));
  $$('#rform .item').forEach((el) => {
    const sec = el.dataset.sec;
    const orig = (base[sec] || [])[+el.dataset.i] || {};
    const it = { page: orig.page, source: orig.source, origin: orig.origin };
    $$('[data-k]', el).forEach((inp) => {
      it[inp.dataset.k] = inp.dataset.k === 'tech' ? inp.value.split(',').map((s) => s.trim()).filter(Boolean) : inp.value.trim();
    });
    if (it.origin === 'auto' && JSON.stringify(stripMeta(orig)) !== JSON.stringify(stripMeta(it))) it.origin = 'edited';
    if (!it.origin) it.origin = 'manual';
    d[sec].push(it);
  });
  d.skills = $('#skills').value.split(',').map((s) => s.trim()).filter(Boolean);
  return d;
}
const stripMeta = (o) => { const c = { ...o }; ['page', 'source', 'origin', 'needs_check'].forEach((k) => delete c[k]); Object.keys(c).forEach((k) => { if (Array.isArray(c[k])) c[k] = c[k].join(','); if (!c[k]) delete c[k]; }); return c; };

function bindForm(state, onChange) {
  onChange = onChange || renderEditor;
  $$('[data-add]').forEach((b) => (b.onclick = () => { state.data = readForm(state.data); state.data[b.dataset.add].push({}); onChange(state); }));
  $$('[data-rm]').forEach((b) => (b.onclick = () => { state.data = readForm(state.data); const [s, i] = b.dataset.rm.split(':'); state.data[s].splice(+i, 1); onChange(state); }));
}

function renderEditor(state) {
  const left = state.extraction ? originalPane(state.extraction) : '';
  $('#editor').innerHTML = `${state.extraction && state.extraction.status === 'failed'
    ? '<div class="err" style="margin-bottom:14px">파일에서 내용을 충분히 읽지 못했습니다. 직접 입력해 주세요. 인식된 부분은 미리 채워 두었습니다.</div>' : ''}
    ${state.extraction && state.extraction.status === 'review' ? '<div class="note" style="margin-bottom:14px">원문과 인식 결과를 비교해 틀린 부분을 고쳐 주세요.</div>' : ''}
    <div class="${left ? 'split' : ''}">${left}<div>${formHtml(state.data)}</div></div>
    <div class="card" style="margin-top:16px"><label class="check"><input type="checkbox" id="save"><span>이 내용을 마이페이지 '내 이력서'에도 저장</span></label>
      <div class="field" id="save-title-wrap" style="margin-top:10px;display:none"><label>이력서 제목 *</label><input id="save-title" placeholder="예: 백엔드 지원용 이력서" maxlength="100"></div>
      <div class="row" style="margin-top:8px"><button class="btn primary lg" id="confirm">내용 확인 완료</button><span class="small">확인 완료한 이력서는 지원 시점 기준으로 고정 보관됩니다.</span></div></div>`;
  bindForm(state);
  $('#save').onchange = () => { $('#save-title-wrap').style.display = $('#save').checked ? '' : 'none'; };
  $('#confirm').onclick = async () => {
    const data = readForm(state.data);
    const saveOn = $('#save').checked;
    const title = ($('#save-title').value || '').trim();
    if (saveOn && !title) { toast('이력서 제목을 입력해 주세요.', true); $('#save-title').focus(); return; }
    try {
      await post(`/api/applications/${state.appId}/resume`, { data, extraction_id: state.extraction ? state.extraction.id : null, save_title: saveOn ? title : null });
      toast('이력서를 확인했습니다.');
      go(`#/apply/${state.appId}/env`);
    } catch (e) { toast(e.message, true); }
  };
}

function originalPane(x) {
  const isImg = x.mime && x.mime.startsWith('image/');
  return `<div class="card col" style="position:sticky;top:80px"><div class="between"><h3>원문</h3><span class="small">${esc(x.filename)}</span></div>
    ${isImg ? `<img class="rawimg" src="/api/resume/extractions/${x.id}/file" alt="업로드한 이력서">` : `<a class="btn sm" target="_blank" href="/api/resume/extractions/${x.id}/file">원본 PDF 열기</a>`}
    ${(x.pages || []).map((p) => `<div><div class="small">${p.page}쪽 · ${p.method === 'ocr' ? `OCR 인식 (신뢰도 ${p.confidence}%)` : '텍스트 추출'}</div><div class="rawtext">${esc(p.text)}</div></div>`).join('')}</div>`;
}

const STAGE_KO = { text: '텍스트 추출 중', ocr: '문자 인식(OCR) 중', structure: '항목 정리 중(AI)', done: '완료' };
const givenName = (name) => (name && name.length > 1 ? name.slice(1) : name || '');
const stageLabel = (stage) => (stage === 'structure' && me && me.name)
  ? `headhunterAI가 ${esc(givenName(me.name))}님의 이력서를 꼼꼼히 읽어보고 있습니다. 잠시만 기다려주세요.`
  : (STAGE_KO[stage] || '처리 중');

/* 지원·면접과 별도로 이력서만 등록(2026-11-18 사용자 지시) — 마이페이지에서 바로, 공고 지원 없이 */
async function resumeNew() {
  if (!requireLogin('/resume/new')) return;
  const state = { data: blank() };
  view.innerHTML = `<div class="wrap">
    ${backLink('#/my', '마이페이지')}
    <h1 style="margin-top:14px">내 이력서 등록</h1><p class="sub">지원과 별도로 이력서를 미리 저장해 두면, 지원할 때 불러와 쓸 수 있습니다.</p>
    <div id="editor"></div></div>`;
  renderResumeNew(state);
}

function renderResumeNew(state) {
  $('#editor').innerHTML = `${formHtml(state.data)}
    <div class="card" style="margin-top:16px">
      <div class="field"><label>이력서 제목 *</label><input id="new-title" placeholder="예: 백엔드 지원용 이력서" maxlength="100"></div>
      <div class="row" style="margin-top:8px"><button class="btn primary lg" id="save-new">저장</button></div>
    </div>`;
  bindForm(state, renderResumeNew);
  $('#save-new').onclick = async () => {
    const data = readForm(state.data);
    const title = ($('#new-title').value || '').trim();
    if (!title) { toast('이력서 제목을 입력해 주세요.', true); $('#new-title').focus(); return; }
    if (!data.basic.name.trim() || !(data.education.length || data.career.length)) {
      toast('성명과 학력 또는 경력 1건 이상은 필수입니다.', true); return;
    }
    try {
      await post('/api/me/resumes', { title, data });
      toast('이력서를 등록했습니다.');
      go('#/my');
    } catch (e) { toast(e.message, true); }
  };
}

async function resumeView(appId) {
  if (!requireLogin()) return;
  const app = await get(`/api/applications/${appId}`);
  if (app.step === 'consent') return go(`#/apply/${appId}/consent`);
  const state = { appId, data: app.resume_snapshot || blank(), extraction: null };
  view.innerHTML = `<div class="wrap">${stepsBar('resume')}
    <h1>이력서 등록</h1><p class="sub">${esc(app.job.title)} · PDF(텍스트·스캔), JPG, PNG 파일을 올리면 자동으로 인식합니다.</p>
    <div class="grid3" style="margin:18px 0">
      <label class="card click" style="display:block"><b>파일 업로드 · 자동 인식</b><div class="small">PDF·JPG·PNG, 10MB, 5쪽 이하</div>
        <input type="file" id="file" accept=".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png" hidden></label>
      <button class="card click" id="manual"><b>직접 작성</b><div class="small">항목을 하나씩 입력</div></button>
      <button class="card click" id="load"><b>내 이력서 불러오기</b><div class="small">마이페이지에 저장한 이력서</div></button>
    </div>
    <div id="progress"></div><div id="editor"></div></div>`;
  $('#manual').onclick = () => { state.extraction = null; renderEditor(state); };
  $('#load').onclick = async () => {
    const list = await get('/api/me/resumes');
    if (!list.length) return toast('저장된 이력서가 없습니다.', true);
    const choice = await modal({ title: '내 이력서 불러오기', body: '불러올 이력서를 고르세요.', buttons: [...list.map((r) => ({ label: r.title, value: r })), { label: '취소', value: null }] });
    if (choice) { state.extraction = null; state.data = choice.data; renderEditor(state); }
  };
  $('#file').onchange = async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    if (f.size > 10 * 1024 * 1024) return toast('10MB 이하 파일만 올릴 수 있습니다.', true);
    const fd = new FormData(); fd.append('file', f);
    $('#editor').innerHTML = '';
    try {
      const { extraction_id } = await HH.api('/api/resume/uploads', { method: 'POST', body: fd });
      pollExtraction(extraction_id, state);
    } catch (err) { $('#progress').innerHTML = `<div class="err">${esc(err.message)}</div>`; }
  };
  if (app.resume_snapshot) renderEditor(state);
}

function pollExtraction(id, state) {
  let stopped = false;
  onLeave(() => { stopped = true; });
  const tick = async () => {
    if (stopped) return;
    const x = await get(`/api/resume/extractions/${id}`);
    if (x.status === 'processing') {
      $('#progress').innerHTML = `<div class="card"><div class="between"><b><span class="spinner"></span> ${stageLabel(x.stage)}</b><span class="small">${x.progress}%</span></div>
        <div class="bar" style="margin-top:10px"><i style="width:${x.progress}%"></i></div></div>`;
      setTimeout(tick, 1000);
      return;
    }
    $('#progress').innerHTML = '';
    state.extraction = x;
    state.data = x.structured || blank();
    renderEditor(state);
  };
  tick();
}

/* 인식(특히 AI 항목 정리)이 오래 걸릴 때 기다리지 않고 진행. 인식 결과는 쓰지 않는다.
   ① 이 지원 건에 확인한 이력서가 있으면 그대로 ② 마이페이지에 저장한 이력서가 있으면 가장 최근 것으로 확인 처리
   ③ 둘 다 없으면 직접 작성 화면(필수: 성명 + 학력 또는 경력 1건) */
async function skipWait(state) {
  $('#progress').innerHTML = '';
  const app = await get(`/api/applications/${state.appId}`);
  if (app.resume_snapshot) return go(`#/apply/${state.appId}/env`);
  const saved = await get('/api/me/resumes');
  if (saved.length) {
    const r = saved[0];
    try {
      await post(`/api/applications/${state.appId}/resume`, { data: r.data, extraction_id: null, save_title: null });
      toast(`저장된 이력서 '${r.title}'로 진행합니다.`);
      return go(`#/apply/${state.appId}/env`);
    } catch (e) { toast(e.message, true); state.data = r.data; }
  } else {
    state.data = blank();
    if (me) Object.assign(state.data.basic, { name: me.name || '', email: me.email || '', phone: me.phone || '' });
    toast('성명과 학력 또는 경력 1건 이상을 입력하고 ‘내용 확인 완료’를 누르면 다음 단계로 넘어갑니다.');
  }
  state.extraction = null; // 처리 중인 인식 작업은 확인 대상으로 묶지 않는다(끝난 뒤 결과가 덮어쓸 수 있음)
  renderEditor(state);
}

/* ───────── 환경 점검 (APP-06, APP-07) ───────── */
async function envView(appId) {
  if (!requireLogin()) return;
  const app = await get(`/api/applications/${appId}`);
  if (app.step === 'consent' || app.step === 'resume') return go(`#/apply/${appId}/${app.step}`);
  if (app.attempt) return go(`#/interview/${app.attempt.id}`);
  const coding = app.job.coding_enabled;
  const ok = { mic: false, cam: false, speaker: false, network: false, ai: false };
  view.innerHTML = `<div class="wrap">${stepsBar('env')}
    <h1>환경 점검</h1><p class="sub">모든 항목을 통과해야 면접을 시작할 수 있습니다. Chrome·Edge 최신 버전을 사용해 주세요.</p>
    <div class="grid2" style="margin-top:18px;align-items:start">
      <div class="col">
        <div class="card"><div class="between"><h3>① 마이크</h3><span class="pill dim" id="s-mic">대기</span></div>
          <div class="row" style="margin:8px 0"><select id="mic-sel" style="flex:1;min-width:0"><option value="">기본 마이크</option></select><button class="btn sm" id="dev-retry">다시 시도</button></div>
          <p class="small" id="mic-msg">말을 해서 막대가 움직이는지 확인하세요. 내장·유선(3.5mm)·USB·블루투스 마이크 모두 쓸 수 있으니 목록에서 면접에 쓸 장치를 고르세요.</p><div class="meter"><i id="mic-level"></i></div></div>
        <div class="card"><div class="between"><h3>② 카메라</h3><span class="pill dim" id="s-cam">대기</span></div>
          <div class="row" style="margin:8px 0"><select id="cam-sel" style="flex:1;min-width:0"><option value="">기본 카메라</option></select></div>
          <p class="small" id="cam-msg" style="display:none"></p>
          <div class="cam" id="cam" style="width:100%;max-width:320px">카메라 연결 중</div></div>
        <div class="card"><div class="between"><h3>③ 스피커</h3><span class="pill dim" id="s-speaker">대기</span></div><p class="small">면접 때 쓸 헤드폰·스피커를 고르고 테스트음을 재생하세요. 소리가 끝까지 들렸을 때만 '들립니다'를 누르세요.</p>
          <div class="row" style="margin:8px 0" id="spk-row"><select id="spk-sel" style="flex:1;min-width:0"><option value="">기본 출력 장치</option></select></div>
          <div class="row"><button class="btn sm" id="tone">테스트음 재생</button><button class="btn sm" id="heard" disabled>들립니다</button><button class="btn sm ghost" id="not-heard" disabled>안 들립니다</button></div>
          <p class="small" id="spk-msg" style="display:none"></p></div>
        <div class="card"><div class="between"><h3>④ 네트워크 · ⑤ AI 서버</h3><span class="pill dim" id="s-net">확인 중</span></div><p class="small" id="net-msg">서버 응답을 확인하고 있습니다.</p></div>
      </div>
      <div class="col">
        <div class="card hl"><h2>시작 전 안내</h2>
          <dl class="kv"><dt>진행 방식</dt><dd>AI 면접${coding ? ' + 코딩테스트(Python, 3문항 60분)' : ''}</dd>
          <dt>답변 시간</dt><dd>1분 이상입니다. 질문 음성이 끝나면 3-2-1 후 시작합니다.</dd>
          <dt>예상 소요</dt><dd>약 ${25 + (coding ? 25 : 0)}분</dd>
          <dt>주의</dt><dd>중단·일시정지 버튼이 없어 시작하면 끝까지 진행됩니다. 탭 닫기·새로고침은 바로 응시 종료, 다른 탭·창 전환이나 전체 화면 해제(2초 이상)는 1회 경고 후 2회째에 응시가 취소됩니다. 네트워크·기기 장애로 끊기면 재접속해 끊긴 질문부터 이어갈 수 있습니다.</dd></dl>
          <label class="check" style="margin-top:10px"><input type="checkbox" id="rules"><b>위 응시 규칙을 확인했습니다</b></label>
          <p class="small" id="pregen">맞춤 질문은 점검을 통과하면 준비를 시작합니다.</p>
          <button class="btn primary lg" id="start" disabled style="width:100%;margin-top:12px">headhunterAI 면접 시작하기</button>
          <p class="small" id="start-why" style="margin-top:8px"></p>
        </div>
      </div>
    </div></div>`;
  const mark = (k, pass, label) => { ok[k] = pass; const el = $(`#s-${k}`); if (el) { el.textContent = label || (pass ? '통과' : '실패'); el.className = 'pill ' + (pass ? 'on' : 'dim'); } refresh(); };
  let envPosted = false;
  // 맞춤 질문(그래프 1-A) 준비가 끝나기 전에 시작하면 서버가 모델을 안 쓰는 대체 경로로 떨어져 엉뚱한 질문이 나간다
  // → pregen_status === 'ready'(또는 'failed' = 더 기다려도 소용없음)일 때만 시작 버튼을 푼다 (2026-11-18 사용자 지시)
  let pregenReady = false;
  async function refresh() {
    const all = Object.values(ok).every(Boolean);
    if (all && !envPosted) {
      envPosted = true;
      try { await post(`/api/applications/${appId}/env`, { ...ok, cam: camReal }); watchPregen(); } catch (e) { envPosted = false; toast(e.message, true); }
    }
    $('#start').disabled = !(all && envPosted && $('#rules').checked && pregenReady);
    // 버튼이 왜 안 눌리는지 바로 보이게: 남은 항목을 적어 준다
    const names = { mic: '① 마이크', cam: '② 카메라', speaker: '③ 스피커', network: '④ 네트워크', ai: '⑤ AI 서버' };
    const left = Object.keys(names).filter((k) => !ok[k]).map((k) => names[k]);
    if (!$('#rules').checked) left.push('응시 규칙 확인');
    if (all && envPosted && !pregenReady) left.push('맞춤 질문 준비');
    const why = $('#start-why');
    if (why) { why.textContent = left.length ? `아직 남은 항목: ${left.join(', ')}` : ''; why.style.display = left.length ? '' : 'none'; }
  }
  $('#rules').onchange = refresh;

  let meter = null;
  let camReal = false; // 카메라가 실제로 연결됐는지(서버에 그대로 기록)
  let spkPicked = false; // 사용자가 출력 장치를 직접 고르기 전까지는 마이크와 같은 장치의 출력을 자동 선택
  let devList = [];
  const micLabel = () => { const tr = media.stream && media.stream.getAudioTracks()[0]; return tr ? tr.label : ''; };
  const micDev = () => devList.find((d) => d.kind === 'audioinput' && d.label === micLabel());
  const isHfp = (label) => /hands-free|hands free|핸즈프리|수화기/i.test(label || '');
  // 블루투스 헤드셋만의 예외: 마이크가 켜지면 통화(Hands-Free) 모드로 바뀌어 같은 헤드셋의 Stereo 출력은 무음이 된다.
  // 내장·유선·USB 장치는 해당 없음
  const hfpMismatch = () => {
    const mic = micDev(), out = devList.find((d) => d.kind === 'audiooutput' && d.deviceId === media.spkId);
    return !!(mic && out && isHfp(mic.label) && !isHfp(out.label) && mic.groupId === out.groupId);
  };
  async function fillDevices() {
    devList = navigator.mediaDevices ? await navigator.mediaDevices.enumerateDevices() : [];
    const list = devList;
    // 출력 자동 선택: 마이크와 같은 장치(헤드셋·USB 등)의 출력이 있으면 그것, 없으면 기본 출력 장치
    if (!spkPicked && micLabel()) {
      const outs = list.filter((d) => d.kind === 'audiooutput' && d.deviceId !== 'default' && d.deviceId !== 'communications');
      const mic = micDev();
      const hfp = mic && isHfp(mic.label);
      const same = outs.find((d) => d.label === micLabel())
        || (mic && outs.find((d) => d.groupId === mic.groupId && (!hfp || isHfp(d.label))));
      media.spkId = same ? same.deviceId : '';
    }
    // 2026-11-18 사용자 지시: Windows가 같은 물리 장치를 '기본값 -'·'커뮤니케이션 -'·실제 항목 셋으로
    // 나눠 알려 주는 것을 하나로 합친다(groupId 기준, 실제 항목을 우선) · 이름의 접두어도 지운다
    const put = (sel, kind, cur, base) => {
      let opts = list.filter((d) => d.kind === kind && d.deviceId);
      const isVirtual = (d) => d.deviceId === 'default' || d.deviceId === 'communications';
      const byKey = new Map();
      for (const d of opts) {
        const key = d.groupId || d.deviceId;
        const prev = byKey.get(key);
        if (!prev || (isVirtual(prev) && !isVirtual(d))) byKey.set(key, d);
      }
      opts = [...byKey.values()];
      const hasDefault = opts.some((d) => d.deviceId === 'default');
      const want = cur || (hasDefault ? 'default' : (opts[0] ? opts[0].deviceId : ''));
      const label = (d, i) => (d.label || `${base.slice(3)} ${i + 1}`).replace(/^(기본값|커뮤니케이션)\s*-\s*/, '');
      $(sel).innerHTML = (hasDefault || opts.length ? '' : `<option value="">${base}</option>`)
        + opts.map((d, i) => `<option value="${esc(d.deviceId)}"${d.deviceId === want ? ' selected' : ''}>${esc(label(d, i))}</option>`).join('');
    };
    if (!$('#mic-sel')) return;
    put('#mic-sel', 'audioinput', media.micId, '기본 마이크');
    put('#cam-sel', 'videoinput', media.camId, '기본 카메라');
    put('#spk-sel', 'audiooutput', media.spkId, '기본 출력 장치');
  }
  async function connectDevices() {
    if (meter) { meter(); meter = null; }
    stopMedia();
    ok.mic = false; ok.cam = false;
    mark('mic', false, '연결 중'); mark('cam', false, '연결 중');
    $('#cam').textContent = '카메라 연결 중';
    const r = await openMedia();
    media.stream = r.stream;
    const show = (k, err) => {
      const msg = $(`#${k}-msg`);
      if (err) { const [label, help] = mediaErr(err); mark(k, false, label); msg.textContent = help; msg.style.display = ''; }
      return !err;
    };
    if (show('mic', r.mic)) {
      $('#mic-msg').textContent = '말을 해서 막대가 움직이는지 확인하세요. 내장·유선(3.5mm)·USB·블루투스 마이크 모두 쓸 수 있으니 목록에서 면접에 쓸 장치를 고르세요.';
      mark('mic', false, '말해 보세요');
      const ctx = new AudioContext(); const src = ctx.createMediaStreamSource(new MediaStream(r.stream.getAudioTracks())); const an = ctx.createAnalyser(); an.fftSize = 512; src.connect(an);
      const buf = new Uint8Array(an.fftSize); let raf;
      const loop = () => {
        an.getByteTimeDomainData(buf);
        let s = 0; for (const x of buf) s += ((x - 128) / 128) ** 2;
        const lvl = Math.min(100, Math.sqrt(s / buf.length) * 400);
        const el = $('#mic-level'); if (el) el.style.width = lvl + '%';
        if (lvl > 8 && !ok.mic) mark('mic', true);
        raf = requestAnimationFrame(loop);
      };
      loop();
      meter = () => { cancelAnimationFrame(raf); ctx.close(); };
    } else { $('#mic-level').style.width = '0%'; }
    if (show('cam', r.cam)) {
      camReal = true; $('#cam-msg').style.display = 'none';
      const v = document.createElement('video'); v.autoplay = true; v.muted = true; v.playsInline = true; v.srcObject = new MediaStream(r.stream.getVideoTracks());
      $('#cam').innerHTML = ''; $('#cam').appendChild(v);
      v.onloadeddata = () => mark('cam', true);
    } else if (isTestApplicant()) { // 2026-11-16 사용자 결정: 카메라 필수(APP-06). 테스트 계정(이지원)만 카메라 없이 통과
      camReal = false; mark('cam', true, '없음 · 생략(테스트 계정)');
      $('#cam').textContent = '테스트 계정은 카메라 없이 진행합니다';
    } else {
      camReal = false; // show('cam', …)가 이미 실패로 표시함 → 카메라를 켜야 면접을 시작할 수 있다
      $('#cam').textContent = '카메라를 켜야 면접을 시작할 수 있습니다';
    }
    await fillDevices();
  }
  $('#mic-sel').onchange = (e) => { media.micId = e.target.value; connectDevices(); };
  $('#cam-sel').onchange = (e) => { media.camId = e.target.value; connectDevices(); };
  $('#dev-retry').onclick = () => connectDevices();
  const onDevChange = () => fillDevices();
  if (navigator.mediaDevices) navigator.mediaDevices.addEventListener('devicechange', onDevChange);
  onLeave(() => { if (meter) meter(); if (navigator.mediaDevices) navigator.mediaDevices.removeEventListener('devicechange', onDevChange); });
  connectDevices();
  // 스피커: 들렸는지는 브라우저가 알 수 없어 본인 확인(APP-06 ③). 출력 장치를 고르고, 테스트음이 끝난 뒤에만 확인 가능
  const canPickOut = 'setSinkId' in HTMLMediaElement.prototype;
  const spkMsg = (text) => { $('#spk-msg').textContent = text; $('#spk-msg').style.display = text ? '' : 'none'; };
  if (!canPickOut) { $('#spk-row').style.display = 'none'; spkMsg('이 브라우저는 출력 장치를 고를 수 없어 Windows 기본 출력 장치로 재생합니다. 다른 장치로 들으려면 Windows 소리 설정에서 기본 장치를 바꾸세요.'); }
  const HFP_HELP = '지금 마이크가 블루투스 헤드셋의 통화(Hands-Free) 마이크라, 같은 헤드셋의 ‘Stereo’ 출력은 소리가 나지 않습니다. 같은 이름의 Hands-Free 출력을 고르거나, 마이크를 PC 내장·유선·USB 마이크로 바꾸면 Stereo로 들을 수 있습니다. 면접 질문 음성도 들리려면 Windows 기본 출력 장치를 같은 항목으로 맞춰 두세요.';
  $('#spk-sel').onchange = (e) => {
    media.spkId = e.target.value; spkPicked = true;
    mark('speaker', false, '대기'); $('#heard').disabled = true; $('#not-heard').disabled = true;
    spkMsg(hfpMismatch() ? HFP_HELP : '');
  };
  function toneUrl() { // 도-미-솔 3음 WAV(약 2초). AudioContext보다 <audio>의 출력 장치 지정이 안정적이라 파일로 만들어 재생
    const sr = 44100, notes = [523, 659, 784], n = Math.round(sr * 1.95), d = new DataView(new ArrayBuffer(44 + n * 2));
    const w = (o, s) => [...s].forEach((c, i) => d.setUint8(o + i, c.charCodeAt(0)));
    w(0, 'RIFF'); d.setUint32(4, 36 + n * 2, true); w(8, 'WAVE'); w(12, 'fmt '); d.setUint32(16, 16, true); d.setUint16(20, 1, true); d.setUint16(22, 1, true);
    d.setUint32(24, sr, true); d.setUint32(28, sr * 2, true); d.setUint16(32, 2, true); d.setUint16(34, 16, true); w(36, 'data'); d.setUint32(40, n * 2, true);
    for (let i = 0; i < n; i++) {
      const t = i / sr, k = Math.floor(t / 0.65), u = t - k * 0.65;
      const v = k < 3 && u < 0.5 ? Math.sin(2 * Math.PI * notes[k] * t) * 0.35 * Math.min(1, u / 0.01, (0.5 - u) / 0.01) : 0;
      d.setInt16(44 + i * 2, v * 32767, true);
    }
    return URL.createObjectURL(new Blob([d], { type: 'audio/wav' }));
  }
  $('#tone').onclick = async () => {
    mark('speaker', false, '재생 중');
    $('#tone').disabled = true; $('#heard').disabled = true; $('#not-heard').disabled = true;
    spkMsg(hfpMismatch() ? HFP_HELP : '');
    const a = new Audio(toneUrl());
    const done = (err) => {
      URL.revokeObjectURL(a.src);
      $('#tone').disabled = false; $('#heard').disabled = !!err; $('#not-heard').disabled = false;
      mark('speaker', false, err ? '재생 실패' : '확인 필요');
      if (err) spkMsg('테스트음을 재생하지 못했습니다(' + err + '). 다른 출력 장치를 고르거나 페이지를 새로고침한 뒤 다시 시도하세요.');
    };
    try { if (canPickOut && media.spkId) await a.setSinkId(media.spkId); } catch (_) { toast('선택한 출력 장치를 열지 못해 기본 장치로 재생합니다.', true); }
    a.onended = () => done();
    try { await a.play(); } catch (e) { done(e.name || '알 수 없는 오류'); }
  };
  $('#heard').onclick = () => mark('speaker', true);
  $('#not-heard').onclick = () => {
    mark('speaker', false, '안 들림');
    spkMsg('① 볼륨·음소거를 확인하세요. ② 위 목록에서 실제로 쓰는 스피커·이어폰·헤드폰을 직접 고르세요(유선 이어폰은 꽂은 뒤 목록이 바뀝니다). ③ 블루투스 헤드셋을 마이크로도 쓰면 출력은 ‘Stereo’가 아니라 같은 이름의 Hands-Free 장치를 고르세요. ④ Windows 작업 표시줄 스피커 아이콘에서 출력 장치를 바꿀 수도 있습니다. 확인 후 다시 재생하세요.');
  };
  const t0 = performance.now();
  try {
    const h = await get('/api/system/health');
    const ms = Math.round(performance.now() - t0);
    ok.network = navigator.onLine && ms < 1500 && h.db;
    ok.ai = h.llm.ok;
    $('#net-msg').textContent = `서버 응답 ${ms}ms · AI 서버 ${h.llm.ok ? '정상' : '응답 없음'}`;
    mark('net', ok.network && ok.ai, ok.network && ok.ai ? '통과' : '실패');
    ok.net = undefined; delete ok.net;
  } catch (_) { mark('net', false); }

  function watchPregen() {
    let stopped = false; onLeave(() => { stopped = true; });
    const t = async () => {
      if (stopped) return;
      let a;
      try { a = await get(`/api/applications/${appId}`); } catch (_) { setTimeout(t, 2000); return; } // 일시적 오류로 폴링이 멈추면 버튼이 안 풀린다
      // 2026-11-18 사용자 지시: 준비 중 문구를 지원자 이름을 부르는 안내로
      const who = esc(me && me.name ? me.name : '지원자');
      const label = { running: `<span class="spinner"></span> headhunterAI가 신중하게 ${who} 님의 면접을 준비하고 있습니다. 잠시만 기다려 주십시오.`, ready: `✓ ${who} 님의 면접 준비가 끝났습니다. 시작하실 수 있습니다.`, failed: '맞춤 질문 준비에 실패해 기본 질문 형식으로 진행합니다.', none: `<span class="spinner"></span> headhunterAI가 신중하게 ${who} 님의 면접을 준비하고 있습니다. 잠시만 기다려 주십시오.` }[a.pregen_status];
      const el = $('#pregen'); if (el) el.innerHTML = label;
      pregenReady = a.pregen_status === 'ready' || a.pregen_status === 'failed';
      refresh();
      if (a.pregen_status === 'running' || a.pregen_status === 'none') setTimeout(t, 2000);
    };
    t();
  }

  $('#start').onclick = async () => {
    await goFullscreen();
    try {
      const r = await post(`/api/applications/${appId}/attempt`);
      go(`#/interview/${r.attempt_id}`);
    } catch (e) {
      if (document.fullscreenElement) document.exitFullscreen();
      await modal({ title: '면접을 시작할 수 없습니다', body: esc(e.message) });
    }
  };
}

/* ───────── AI 면접 · 코딩테스트 (4.5, 4.7, 5장) ───────── */
async function interviewView(attemptId) {
  if (!requireLogin()) return;
  $('#top').hidden = true;
  if ($('#legal-foot')) $('#legal-foot').hidden = true;   // 응시 중에는 약관 링크로 화면을 벗어나지 않게 숨김
  onLeave(() => { $('#top').hidden = false; if ($('#legal-foot')) $('#legal-foot').hidden = false; });
  let st = await get(`/api/attempts/${attemptId}`);
  let active = false;           // 이탈 감시 on/off
  let unloading = false;
  let reporting = false;
  let blurTimer = null;
  let qTimer = null;
  let remaining = 0;
  let current = null;           // current question
  let codingCtx = null;
  let wave = null;              // 음성 파형 { stop, listen(on) }
  let rec = null;               // v0.9 INT-03: 답변 녹음(MediaRecorder) → 서버에서 Qwen3-ASR로 글로 바꿈
  let qAudio = null;            // v0.9 INT-01: Qwen3-TTS 질문 음성

  // heartbeat (5초)
  const hb = setInterval(async () => {
    if (!active) return;
    try {
      const r = await post(`/api/attempts/${attemptId}/heartbeat`);
      if (['cancelled', 'terminated', 'completed', 'interrupted'].includes(r.status)) { st = await get(`/api/attempts/${attemptId}`); render(); }
    } catch (_) { /* offline: server will judge */ }
  }, 5000);
  onLeave(() => clearInterval(hb));

  // 임의 종료 신호 (RES-04)
  const beforeUnload = () => {
    if (!active) return;
    unloading = true;
    navigator.sendBeacon(`/api/attempts/${attemptId}/events`, new Blob([JSON.stringify({ type: 'user_exit', detail: {} })], { type: 'application/json' }));
  };
  window.addEventListener('beforeunload', beforeUnload);
  onLeave(() => window.removeEventListener('beforeunload', beforeUnload));

  // 화면 이탈 (5.2)
  async function reportExit(kind) {
    if (!active || unloading || reporting) return;
    reporting = true; active = false;
    clearInterval(qTimer);
    try {
      const r = await post(`/api/attempts/${attemptId}/events`, { type: 'exit', detail: { kind } });
      if (r.action === 'cancelled') { st = await get(`/api/attempts/${attemptId}`); reporting = false; return render(); }
      await modal({ title: '화면 이탈이 감지되었습니다 (1회)', body: '한 번 더 이탈하면 면접이 취소됩니다.<br>확인을 누르면 전체 화면으로 돌아가 같은 질문부터 다시 진행합니다.', buttons: [{ label: '확인하고 계속', value: true, primary: true }] });
      await goFullscreen();
      reporting = false;
      resumeAfterWarning();
    } catch (_) { reporting = false; }
  }
  const onVis = () => { if (document.hidden) reportExit('tab_hidden'); };
  const onBlur = () => { clearTimeout(blurTimer); blurTimer = setTimeout(() => { if (!document.hasFocus()) reportExit('focus_lost_2s'); }, 2000); };
  const onFocus = () => clearTimeout(blurTimer);
  const onFs = () => { if (!document.fullscreenElement) reportExit('fullscreen_exit'); };
  const onOffline = () => { active = false; clearInterval(qTimer); showDisconnected(); };
  const onOnline = async () => { st = await get(`/api/attempts/${attemptId}`); render(); };
  // 테스트 계정(시드 이지원)만 화면 이탈 감지를 꺼서 결과 확인 중 자유롭게 다른 창을 볼 수 있게 한다(실제 지원자는 규칙 그대로)
  if (!isTestApplicant()) {
    document.addEventListener('visibilitychange', onVis);
    window.addEventListener('blur', onBlur);
    window.addEventListener('focus', onFocus);
    document.addEventListener('fullscreenchange', onFs);
  }
  window.addEventListener('offline', onOffline);
  window.addEventListener('online', onOnline);
  onLeave(() => {
    document.removeEventListener('visibilitychange', onVis); window.removeEventListener('blur', onBlur); window.removeEventListener('focus', onFocus);
    document.removeEventListener('fullscreenchange', onFs); window.removeEventListener('offline', onOffline); window.removeEventListener('online', onOnline);
    clearInterval(qTimer); clearTimeout(blurTimer); speechSynthesis.cancel(); stopQuestionAudio(); stopRecording(true);
    if (codingCtx) codingCtx.stop();
    if (wave) { wave.stop(); wave = null; }
    stopMedia(); // 면접 화면을 벗어나면 카메라·마이크 트랙을 모두 끈다(카메라는 표시만, 녹화·저장 없음)
  });
  const onPageHide = () => stopMedia();
  window.addEventListener('pagehide', onPageHide);
  onLeave(() => window.removeEventListener('pagehide', onPageHide));

  /* 카메라 미리보기 창은 면접 화면에 들어올 때 한 번만 만들고, 질문·꼬리질문·답변이 바뀌어도 다시 그리지 않는다(같은 자리 고정) */
  const camFloat = document.createElement('div');
  camFloat.className = 'cam-float'; camFloat.hidden = true;
  // 미리보기 아래 안내 문구는 2026-11-18 사용자 지시로 제거(고지는 응시 동의 문구에 이미 있음). CSS .cam-note는 남겨 둠
  camFloat.innerHTML = '<div class="cam" id="cam">카메라 연결 중</div>';
  document.body.appendChild(camFloat);
  onLeave(() => camFloat.remove());
  const placeCam = () => { const bar = document.querySelector('.devbar'); camFloat.style.top = ((bar && bar.offsetParent ? bar.offsetHeight : 0) + 16) + 'px'; };

  const liveVideo = () => (media.stream ? media.stream.getVideoTracks().filter((t) => t.readyState === 'live') : []);
  async function ensureCamera() {
    // 카메라 필수(2026-11-16): 면접 단계에서 카메라 트랙이 없으면 다시 연다. 코딩 단계·테스트 계정은 마이크만 있으면 됨
    if (media.stream && media.stream.active && (liveVideo().length || st.status === 'coding' || isTestApplicant())) return media.stream;
    stopMedia();
    const r = await openMedia();
    media.stream = r.stream;
    return r.mic ? null : media.stream;
  }
  /* v0.9 INT-03: 답변 시간 동안 마이크만 녹음한다(카메라 영상은 넣지 않음). 제출할 때 서버로 보내 글로 바꾸고, 파일은 저장하지 않는다 */
  function startRecording() {
    stopRecording(true);
    if (!st.stt || !window.MediaRecorder || !media.stream || !media.stream.getAudioTracks().length) return;
    try {
      const types = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4'];
      const mimeType = types.find((t) => MediaRecorder.isTypeSupported(t)) || '';
      const r = new MediaRecorder(new MediaStream(media.stream.getAudioTracks()), mimeType ? { mimeType } : undefined);
      const chunks = [];
      r.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
      r.start(1000);
      rec = { r, chunks, type: r.mimeType || mimeType || 'audio/webm' };
    } catch (_) { rec = null; }
  }
  function stopRecording(discard) {
    const cur = rec; rec = null;
    if (!cur) return Promise.resolve(null);
    return new Promise((resolve) => {
      const done = () => resolve(discard || !cur.chunks.length ? null : new Blob(cur.chunks, { type: cur.type }));
      if (cur.r.state === 'inactive') return done();
      cur.r.onstop = done;
      try { cur.r.stop(); } catch (_) { done(); }
      setTimeout(done, 1500);
    });
  }
  function stopQuestionAudio() {
    if (qAudio) { try { qAudio.pause(); URL.revokeObjectURL(qAudio.src); } catch (_) { /* ignore */ } qAudio = null; }
  }

  /* 카메라 미리보기: srcObject로 화면에 표시만 한다. MediaRecorder·캔버스 캡처·업로드·저장 없음 */
  function attachCam() {
    const box = camFloat.querySelector('.cam');
    placeCam(); camFloat.hidden = false;
    const vt = liveVideo();
    if (!vt.length) { box.textContent = '카메라가 연결되지 않았습니다 · 면접은 계속 진행됩니다'; return; }
    const cur = box.querySelector('video');
    if (cur && cur.srcObject && cur.srcObject.getVideoTracks()[0] === vt[0]) return; // 같은 카메라면 그대로 둠(깜빡임 없음)
    const v = document.createElement('video'); v.autoplay = true; v.muted = true; v.playsInline = true; v.srcObject = new MediaStream(vt);
    box.innerHTML = ''; box.appendChild(v);
  }

  /* 답변 중 마이크 소리 크기에 맞춰 움직이는 파형. 인식된 글자 대신 '목소리가 들어가고 있다'만 보여 준다(3색 규칙: neon·white) */
  function startWave(canvas, label) {
    const stream = media.stream && media.stream.getAudioTracks().length ? new MediaStream(media.stream.getAudioTracks()) : null;
    let ctx = null, an = null, buf = null;
    if (stream) {
      ctx = new AudioContext(); an = ctx.createAnalyser(); an.fftSize = 1024; an.smoothingTimeConstant = 0.6;
      ctx.createMediaStreamSource(stream).connect(an); buf = new Uint8Array(an.fftSize);
    }
    const g = canvas.getContext('2d');
    // 색은 디자인 토큰(--neon)에서 읽는다: '#39ff14' → '57,255,20'
    const hex = getComputedStyle(document.documentElement).getPropertyValue('--neon').trim().replace('#', '') || '39ff14';
    const NEON = [0, 2, 4].map((i) => parseInt(hex.slice(i, i + 2), 16)).join(',');
    let listening = false, level = 0, t = 0, raf = 0, quiet = 0;
    const curves = [ // [진폭 비율, 파수, 속도, 위상, 투명도]
      [1.0, 2.2, 1.6, 0.0, 0.55], [0.75, 3.1, -2.1, 1.3, 0.4], [0.55, 1.6, 2.7, 2.6, 0.35], [0.35, 4.0, -1.2, 4.1, 0.3],
    ];
    function draw() {
      const w = canvas.clientWidth, h = canvas.clientHeight, dpr = devicePixelRatio || 1;
      if (canvas.width !== w * dpr) { canvas.width = w * dpr; canvas.height = h * dpr; }
      g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, w, h);
      let raw = 0;
      if (an && listening) { an.getByteTimeDomainData(buf); let s = 0; for (const x of buf) s += ((x - 128) / 128) ** 2; raw = Math.min(1, Math.sqrt(s / buf.length) * 5); }
      level += (raw - level) * (raw > level ? 0.35 : 0.08);           // 빠르게 커지고 천천히 줄어듦
      t += 0.016;
      const mid = h / 2, amp = (listening ? 0.06 + level * 0.88 : 0.03) * (h / 2 - 6);
      g.globalCompositeOperation = 'lighter';
      for (const [a, k, sp, ph, al] of curves) {
        g.beginPath();
        for (let x = 0; x <= w; x += 3) {
          const u = x / w, env = Math.sin(Math.PI * u) ** 2.2;            // 양 끝이 가늘어지는 모양
          const y = mid + Math.sin(u * Math.PI * 2 * k + t * sp + ph) * amp * a * env;
          x ? g.lineTo(x, y) : g.moveTo(x, y);
        }
        for (let x = w; x >= 0; x -= 3) {                                // 아래쪽 대칭 곡선으로 면을 채움
          const u = x / w, env = Math.sin(Math.PI * u) ** 2.2;
          g.lineTo(x, mid - Math.sin(u * Math.PI * 2 * k - t * sp + ph) * amp * a * env * 0.35);
        }
        g.closePath();
        g.fillStyle = `rgba(${NEON},${al * 0.35})`; g.fill();
        g.strokeStyle = `rgba(${NEON},${al})`; g.lineWidth = 1.4; g.shadowColor = `rgba(${NEON},.8)`; g.shadowBlur = 12; g.stroke();
      }
      g.shadowBlur = 0; g.globalCompositeOperation = 'source-over';
      g.fillStyle = 'rgba(255,255,255,.55)'; g.fillRect(0, mid - 0.5, w, 1);  // 가운데 빛줄기
      if (label && listening) {
        quiet = raw > 0.12 ? 0 : quiet + 1;
        label.textContent = !stream ? '마이크가 연결되지 않았습니다' : quiet < 40 ? '음성 인식 중' : '말씀해 주세요';
        label.classList.toggle('on', !!stream && quiet < 40);
      }
      raf = requestAnimationFrame(draw);
    }
    draw();
    return {
      listen(on) { listening = on; if (label && !on) { label.textContent = '대기'; label.classList.remove('on'); } },
      stop() { cancelAnimationFrame(raf); if (ctx) ctx.close(); },
    };
  }

  function showDisconnected() {
    view.innerHTML = `<div class="wrap narrow" style="padding-top:12vh"><div class="card hl col">
      <h1>연결이 끊겼습니다</h1><p>답변은 안전하게 저장되었습니다. 다시 접속하면 끊긴 질문부터 이어서 진행합니다.</p>
      <p class="small">인터넷 연결이 돌아오면 자동으로 확인합니다.</p></div></div>`;
  }

  function render() {
    clearInterval(qTimer);
    if (codingCtx) { codingCtx.stop(); codingCtx = null; }
    if (wave) { wave.stop(); wave = null; }
    if (st.status !== 'interview') camFloat.hidden = true; // 미리보기는 면접 질문·답변 단계에서만
    if (st.status === 'interrupted') return renderInterrupted();
    if (['completed', 'terminated', 'cancelled'].includes(st.status)) return renderEnded();
    if (st.status === 'interview') return renderInterview();
    if (st.status === 'coding') return renderCoding();
  }

  function renderEnded() {
    active = false;
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    stopMedia();
    const msg = st.status === 'cancelled'
      ? ['응시가 취소되었습니다', '화면 이탈이 2회 감지되어 규칙에 따라 응시가 취소되었습니다. 이 지원 건은 다시 응시할 수 없습니다.']
      : st.status === 'terminated'
        ? ['응시가 종료되었습니다', '면접 화면을 직접 닫거나 새로고침해 응시가 종료되었습니다. 답변한 문항까지 평가됩니다.']
        : ['응시가 완료되었습니다', 'AI가 답변을 분석하고 있습니다. 분석이 끝나면 아래 버튼으로 정량 리포트를 이메일로 받을 수 있습니다.'];
    view.innerHTML = `<div class="wrap narrow" style="padding-top:10vh">${stepsBar('done')}<div class="card ${st.status === 'completed' ? 'hl' : ''} col">
      <h1>${msg[0]}</h1><p>${msg[1]}</p>${st.status === 'cancelled' ? '' : '<div id="report-box" class="col"></div>'}<a class="btn primary" href="#/my">마이페이지로</a></div></div>`;
    if (st.status !== 'cancelled' && st.application_id) reportBox(st.application_id, $('#report-box'));
  }

  function renderInterrupted() {
    active = false;
    view.innerHTML = `<div class="wrap narrow" style="padding-top:10vh"><div class="card hl col">
      <h1>면접 이어가기</h1>
      <p>연결이 끊겼습니다. 답변은 안전하게 저장되었습니다. 다시 접속하면 끊긴 질문부터 이어서 진행합니다.</p>
      <p class="small">장애로 끊긴 시간은 답변 시간·코딩테스트 제한시간에서 빠지지 않습니다. 이어가기는 횟수 제한이 없습니다.</p>
      <div class="row"><span class="pill dim" id="re-dev">장치 재점검 중</span></div>
      <button class="btn primary lg" id="resume" disabled>전체 화면으로 이어가기</button></div></div>`;
    ensureCamera().then((s) => {
      const camOk = liveVideo().length > 0 || isTestApplicant(); // 카메라 필수(테스트 계정만 예외)
      const okDev = !!s && camOk;
      $('#re-dev').textContent = !s ? '마이크 권한이 필요합니다' : !camOk ? '카메라를 켜야 이어갈 수 있습니다' : '마이크·카메라 정상';
      $('#re-dev').className = 'pill ' + (okDev ? 'on' : 'dim');
      $('#resume').disabled = !okDev;
    });
    $('#resume').onclick = async () => {
      await goFullscreen();
      st = await post(`/api/attempts/${attemptId}/resume`);
      render();
    };
  }

  function renderInterview() {
    const q = st.question;
    if (!q) return;
    current = q;
    view.innerHTML = `<div class="wrap iv-wrap" style="max-width:1000px">
      <div class="between iv-head" style="margin-bottom:14px"><span class="logo"><img src="/images/headhunterAI_logo_reverse_crop.png" alt="headhunterAI"></span>
        <span class="row"><span class="pill neon">${st.step} / ${st.total}${q.followup ? ' · 꼬리질문' : ''}</span>
        ${st.exit_count ? `<span class="pill">화면 이탈 ${st.exit_count}회</span>` : ''}</span></div>
      <div class="stage">
        <div class="between" style="align-items:flex-start;gap:20px">
          <div style="flex:1;min-width:260px" class="col"><div class="small">${q.followup ? '' : `질문 ${q.seq}`}</div><div class="qbox" id="qtext">${esc(q.text)}</div></div>
          <div class="col" style="justify-items:center"><div class="timer idle" id="timer">${q.time_limit}</div></div>
        </div>
        <div id="phase" class="small">질문을 읽어 드리고 있습니다…</div>
        <div class="voice"><canvas id="wave"></canvas><span class="voice-label" id="voice-label">대기</span></div>
        <div class="between"><span class="small">시간 안에 '답변 완료'를 누르면 바로 다음으로 넘어갑니다.</span><button class="btn primary" id="done" disabled>답변 완료</button></div>
        <div class="emergency"><label for="ans">음성이 인식되지 않을 때만 쓰는 비상 입력란</label>
          <textarea class="input" id="ans" rows="1" placeholder="마이크 장애 시에만 입력" disabled></textarea></div>
      </div></div>`;
    if (wave) wave.stop();
    wave = null;
    ensureCamera().then(() => { attachCam(); if ($('#wave')) { wave = startWave($('#wave'), $('#voice-label')); if (!$('#done').disabled) wave.listen(true); } });
    $('#done').onclick = () => submitAnswer();
    // 2026-11-19 사용자 지시: 테스트 계정(이지원)은 질문·꼬리질문 품질 확인용으로 답변 붙여넣기 허용
    $('#ans').addEventListener('paste', (e) => { if (!isTestApplicant()) e.preventDefault(); });
    active = true;
    present(q);
  }

  function present(q) {
    clearInterval(qTimer);
    speechSynthesis.cancel(); stopQuestionAudio();
    const phase = $('#phase');
    let started = false;
    const afterSpeech = () => { if (!started) { started = true; countdown(3, () => startAnswer(q.time_limit)); } };
    if (phase) phase.textContent = '질문을 읽어 드리고 있습니다…';
    // v0.9 INT-01: Qwen3-TTS 음성(서버, 미리 만들어 둠)을 먼저 쓰고, 6초 안에 못 받거나 재생이 안 되면 브라우저 음성으로 읽는다
    const ctl = new AbortController();
    const limit = setTimeout(() => ctl.abort(), 6000);
    fetch(`/api/attempts/${attemptId}/questions/${q.id}/audio`, { credentials: 'same-origin', signal: ctl.signal })
      .then((r) => { clearTimeout(limit); if (!r.ok) throw new Error('tts'); return r.blob(); })
      .then((b) => {
        if (current !== q) return;
        const a = new Audio(URL.createObjectURL(b)); qAudio = a;
        a.onended = afterSpeech; a.onerror = () => browserSpeak(q, afterSpeech);
        setTimeout(afterSpeech, Math.max(6000, q.text.length * 220)); // 끝 이벤트가 오지 않을 때 대비
        return a.play();
      })
      .catch(() => { if (current === q && !started) browserSpeak(q, afterSpeech); });
  }

  function browserSpeak(q, afterSpeech) {
    stopQuestionAudio();
    try {
      const u = new SpeechSynthesisUtterance(q.text);
      u.lang = 'ko-KR'; u.rate = 1.0;
      const ko = speechSynthesis.getVoices().find((v) => v.lang && v.lang.startsWith('ko'));
      if (ko) u.voice = ko;
      let done = false;
      const fin = () => { if (!done) { done = true; afterSpeech(); } };
      u.onend = fin; u.onerror = fin;                       // INT-01: TTS 실패 시 자막만으로 진행
      setTimeout(fin, Math.max(4000, q.text.length * 160)); // 음성 끝 이벤트가 오지 않을 때 대비
      speechSynthesis.speak(u);
    } catch (_) { afterSpeech(); }
  }

  function countdown(n, then) {
    const phase = $('#phase');
    if (!phase) return;
    if (n === 0) return then();
    phase.innerHTML = `<div class="count">${n}</div>`;
    setTimeout(() => countdown(n - 1, then), 800);
  }

  function startAnswer(limit) {
    remaining = limit;
    const ans = $('#ans'); const timer = $('#timer');
    if (!ans) return;
    $('#phase').textContent = '답변해 주세요. 말씀하시는 동안 아래 파형이 움직입니다.';
    ans.disabled = false; $('#done').disabled = false;
    if (wave) wave.listen(true);
    startRecording();
    timer.classList.remove('idle'); timer.textContent = remaining;
    qTimer = setInterval(() => {
      remaining -= 1;
      timer.textContent = Math.max(0, remaining);
      if (remaining <= 0) { clearInterval(qTimer); submitAnswer(); }
    }, 1000);
  }

  async function submitAnswer() {
    clearInterval(qTimer);
    const ans = $('#ans');
    if (!ans || $('#done').disabled) return;
    $('#done').disabled = true; ans.disabled = true;
    if (wave) wave.listen(false);
    const audio = await stopRecording(false);
    $('#phase').innerHTML = '<span class="spinner"></span> 다음 문제로 넘어가겠습니다. 잠시만 기다려주세요…';
    try {
      if (audio) {
        const fd = new FormData();
        fd.append('question_id', current.id); fd.append('text', ans.value); fd.append('audio', audio, 'answer.webm');
        st = await HH.api(`/api/attempts/${attemptId}/answer-audio`, { method: 'POST', body: fd });
        if (st.stt_retry) {   // INT-04: 음성 변환 장애 → 감점 없이 같은 질문 다시
          await modal({ title: '음성을 글로 바꾸지 못했습니다', body: '시스템 문제로 답변 음성을 인식하지 못했습니다. 감점 없이 같은 질문에 다시 답해 주세요.', buttons: [{ label: '다시 답하기', value: true, primary: true }] });
          await goFullscreen();
          $('#done').disabled = true; ans.disabled = true; ans.value = '';
          $('#timer').textContent = current.time_limit;
          return present(current);
        }
      } else {
        st = await post(`/api/attempts/${attemptId}/answer`, { question_id: current.id, text: ans.value });
      }
      render();
    } catch (e) {
      st = await get(`/api/attempts/${attemptId}`);
      render();
    }
  }

  function resumeAfterWarning() {
    if (st.status === 'interview' && current) {      // 같은 질문, 새 답변 시간
      active = true;
      const ans = $('#ans'); if (ans) { ans.disabled = true; }
      if (wave) wave.listen(false);
      stopRecording(true);
      $('#done') && ($('#done').disabled = true);
      $('#timer') && ($('#timer').textContent = current.time_limit);
      present(current);
    } else if (st.status === 'coding' && codingCtx) {
      active = true; codingCtx.resumeTimer();
    }
  }

  /* 문제 지문을 문단으로 나눈다 — 한 덩어리로 붙어 있어 읽기 어려웠다 (2026-11-19 사용자 지시).
     [입력·출력]·[제한 사항]·[예시 설명] 같은 머리말 앞에서 끊고, 그 안에서는 '- '로 시작하는 항목도 줄로 나눈다. */
  function paras(text) {
    const t = String(text || '').replace(/[ 	]*\[([^\]]{1,20})\][ 	]*/g, '@@[$1]@@');
    return t.split('@@').map((blk) => blk.trim()).filter(Boolean).map((blk) => {
      const lines = blk.replace(/\s+-\s/g, '@LN@- ').split('@LN@').filter(Boolean);
      return lines.length > 1 ? '<p>' + lines.map(esc).join('<br>') + '</p>' : '<p>' + esc(blk) + '</p>';
    }).join('');
  }

  async function renderCoding() {
    stopVideo(); // 코딩 단계는 카메라 미리보기 없음 → 카메라 트랙을 끄고 마이크만 유지(2026-11-16 사용자 결정)
    const data = await get(`/api/attempts/${attemptId}/coding`);
    let idx = 0;
    const seen = new Set();
    const codes = Object.fromEntries(data.problems.map((p) => [p.problem_id, p.code]));
    const dirty = new Set();
    let internalClip = '';
    remaining = data.remaining;
    view.innerHTML = `<div class="wrap" style="max-width:1200px">
      <div class="between" style="margin-bottom:14px"><span class="logo"><img src="/images/headhunterAI_logo_reverse_crop.png" alt="headhunterAI"></span>
        <span class="row"><span class="pill neon">코딩테스트</span><span class="tabs lang-switch" role="group" aria-label="풀이 언어" id="langsw"><button type="button" class="on" data-lang="python">Python 3 ✓</button><button type="button" data-lang="c">C</button><button type="button" data-lang="sql">SQL</button></span><div class="timer" id="ctimer" style="width:76px;height:76px;font-size:18px">--:--</div></span></div>
      <div class="tabs" id="tabs">${data.problems.map((p, i) => `<button data-i="${i}">${i + 1}. ${esc(p.title)} <span class="small">(${p.level === 'basic' ? '기초' : '응용'})</span></button>`).join('')}</div>
      <div class="grid2" style="margin-top:14px;align-items:start;grid-template-columns:minmax(0,.8fr) minmax(0,1.2fr)">
        <div class="card col" id="pdesc"></div>
        <div class="col"><textarea class="editor code-editor" id="code" spellcheck="false"></textarea>
          <div class="run-head"><span class="small">실행 결과</span><button type="button" class="btn sm ghost" id="run">예제 실행</button></div>
          <div class="out" id="out">실행 결과가 여기에 표시됩니다. 실행만으로는 제출되지 않습니다.</div>
          <div class="row" style="margin-top:12px"><span class="sp"></span><button class="btn" id="next">다음 문제 →</button><button class="btn primary" id="submit">최종 제출</button></div></div>
      </div>
      <p class="small" style="margin-top:12px">편집기 밖에서 복사한 내용은 붙여넣을 수 없습니다. 코드는 30초마다 자동 저장됩니다.</p></div>`;
    const code = $('#code');
    const show = (i) => {
      if (data.problems[idx]) codes[data.problems[idx].problem_id] = code.value;
      idx = i;
      const p = data.problems[i];
      $$('#tabs button').forEach((b) => b.classList.toggle('on', +b.dataset.i === i));
      $('#pdesc').innerHTML = `<h2>${esc(p.title)}</h2>${paras(p.statement)}<div class="small">함수 이름: <span class="mono neon">${esc(p.func_name)}</span></div>
        <h3 style="margin-top:8px">입출력 예시</h3>${p.examples.map((e) => `<div class="out" style="min-height:0">입력: ${esc(JSON.stringify(e.input))}\n출력: ${esc(JSON.stringify(e.expected))}</div>`).join('')}`;
      code.value = codes[p.problem_id];
      seen.add(i);
      const last = i >= data.problems.length - 1;
      $('#next').hidden = last;
      $('#next').textContent = `다음 문제 (${i + 2}/${data.problems.length}) →`;
      // 아직 안 본 문제가 있으면 최종 제출을 강조하지 않는다(실수로 먼저 누르는 것 방지)
      const allSeen = seen.size >= data.problems.length;
      $('#submit').classList.toggle('primary', allSeen);
      $('#submit').classList.toggle('ghost', !allSeen);
    };
    $$('#tabs button').forEach((b) => (b.onclick = () => show(+b.dataset.i)));
    // 2026-11-19 사용자 지시: 탭만으로는 다음 문제가 있는 줄 몰라 1번만 풀고 최종 제출하는 사고가 남 → 명시적 '다음 문제' 버튼
    $('#next').onclick = () => show(Math.min(idx + 1, data.problems.length - 1));
    show(0);
    // 언어는 고를 수 있게 두되 실제로 채점되는 언어는 Python 3만 (2026-11-11 사용자 지시)
    const pickLang = (b) => {
      if (b.dataset.lang !== 'python') toast('현재 Python 3만 지원합니다.', true);
      $$('#langsw button').forEach((x) => x.classList.toggle('on', x.dataset.lang === 'python'));
    };
    $$('#langsw button').forEach((b) => (b.onclick = () => pickLang(b)));
    code.addEventListener('input', () => dirty.add(data.problems[idx].problem_id));
    code.addEventListener('keydown', (e) => {
      if (e.key === 'Tab') { e.preventDefault(); const s = code.selectionStart; code.setRangeText('    ', s, code.selectionEnd, 'end'); dirty.add(data.problems[idx].problem_id); }
    });
    const remember = () => { internalClip = code.value.substring(code.selectionStart, code.selectionEnd); };
    code.addEventListener('copy', remember); code.addEventListener('cut', remember);
    code.addEventListener('paste', (e) => {                // COD-07
      if (isTestApplicant()) return;   // 2026-11-19 사용자 지시: 채점이 맞는지 확인하려고 정답을 붙여넣어야 함(테스트 계정 전용)
      const text = (e.clipboardData || window.clipboardData).getData('text');
      if (text !== internalClip) {
        e.preventDefault(); toast('편집기 밖에서 복사한 내용은 붙여넣을 수 없습니다.', true);
        post(`/api/attempts/${attemptId}/events`, { type: 'paste_block', detail: { problem_id: data.problems[idx].problem_id } });
      }
    });
    const saveAll = async () => {
      codes[data.problems[idx].problem_id] = code.value;
      for (const pid of [...dirty]) {
        await post(`/api/attempts/${attemptId}/coding/${pid}/save`, { code: codes[pid] });
        dirty.delete(pid);
      }
    };
    const autosave = setInterval(() => { if (active) saveAll().catch(() => {}); }, 30000);
    $('#run').onclick = async () => {
      const p = data.problems[idx];
      $('#out').innerHTML = '<span class="spinner"></span> 실행 중…';
      try {
        const r = await post(`/api/attempts/${attemptId}/coding/${p.problem_id}/run`, { code: code.value });
        dirty.delete(p.problem_id);
        $('#out').textContent = r.status !== 'ok' ? `오류\n${r.stderr}` :
          r.cases.map((c, i) => `예제 ${i + 1}: ${c.ok ? '통과' : '실패'}${c.error ? ` · ${c.error}` : ` · 출력 ${c.got}`}`).join('\n') + (r.stdout ? `\n\n[print 출력]\n${r.stdout}` : '');
      } catch (e) { $('#out').textContent = e.message; }
    };
    const finalSubmit = async () => {
      active = false;
      try { await saveAll(); } catch (_) { /* last autosave is used */ }
      st = await post(`/api/attempts/${attemptId}/coding/submit`);
      render();
    };
    $('#submit').onclick = async () => {
      const unseen = data.problems.length - seen.size;
      const warn = unseen > 0
        ? `<b style="color:var(--neon)">아직 열어 보지 않은 문제가 ${unseen}개 있습니다.</b><br>지금 제출하면 그 문제들은 빈 코드로 채점됩니다.<br><br>`
        : '';
      const okGo = await modal({ title: '최종 제출할까요?', body: `${warn}제출하면 수정할 수 없고 응시가 완료됩니다. ${data.problems.length}문제 모두 현재 코드로 채점됩니다.`, buttons: [{ label: '취소', value: false }, { label: '최종 제출', value: true, primary: true }] });
      if (okGo) finalSubmit();
    };
    let tick;
    const runTimer = () => {
      clearInterval(tick);
      tick = setInterval(() => {
        remaining -= 1;
        const el = $('#ctimer');
        if (el) el.textContent = `${String(Math.floor(Math.max(0, remaining) / 60)).padStart(2, '0')}:${String(Math.max(0, remaining) % 60).padStart(2, '0')}`;
        if (remaining <= 0) { clearInterval(tick); toast('제한시간이 끝나 마지막 저장 코드로 자동 제출합니다.'); finalSubmit(); }
      }, 1000);
    };
    runTimer();
    codingCtx = { stop: () => { clearInterval(tick); clearInterval(autosave); }, resumeTimer: runTimer };
    active = true;
  }

  if (st.status === 'interview' || st.status === 'coding') await ensureCamera();
  render();
}

/* ───────── 완료·결과 ───────── */
/* 완료 화면·결과 화면 공통: 분석 상태 → (자동 확정 공고) 합불 → 리포트 메일 상태 + '결과 리포트 이메일로 받기'(auto 전용, EML-09) */
function reportBox(appId, box, opt = {}) {
  if (!box) return;
  let timer = null;
  const paint = (r) => {
    if (!document.body.contains(box)) { clearInterval(timer); return; }
    const analyzing = !r.evaluated;
    const auto = r.decision_mode === 'auto';
    const res = opt.noResult ? '' : r.result ? `<div class="between"><span>1차 결과</span><b class="${r.result === 'pass' ? 'neon' : ''}">${r.result === 'pass' ? '1차 합격' : '1차 불합격'}</b></div>`
      : (!analyzing && !auto ? '<p class="small">1차 결과는 기업 담당자가 확인한 뒤 확정되며, 확정되면 합불 결과를 이메일로 안내드립니다.</p>' : '');
    const em = r.email || {};
    const mail = !auto ? '' : analyzing ? '<p class="small">AI 분석 중입니다. 보통 몇 분 안에 끝납니다.</p>'
      : em.status === 'sent' ? `<p class="small">정량 리포트를 ${esc(em.to)}(으)로 보냈습니다.</p>`
        : em.status === 'failed' ? '<p class="small">리포트 메일을 보내지 못했습니다. 아래 버튼으로 다시 받아 보세요.</p>'
          : r.email_enabled ? '<p class="small">합불과 별도로, 항목별 점수와 개선점을 담은 정량 리포트를 이메일로 받을 수 있습니다.</p>'
            : '<p class="small">이메일 발송이 아직 설정되지 않았습니다.</p>';
    box.innerHTML = `${res}${mail}${auto && !analyzing && r.email_enabled ? `<div><button class="btn" id="mail-me">${opt.mailLabel || '결과 리포트 이메일로 받기'}</button></div>` : ''}`;
    const b = $('#mail-me', box);
    if (b) b.onclick = async () => {
      b.disabled = true;
      try { const x = await post(`/api/applications/${appId}/report/email`); toast(`${x.to}(으)로 리포트를 보냈습니다.`); load(); }
      catch (e) { toast(e.message, true); b.disabled = false; }
    };
    if (!analyzing && (r.result || !auto)) clearInterval(timer);
  };
  const load = async () => { try { paint(await get(`/api/applications/${appId}/report`)); } catch (_) { clearInterval(timer); } };
  load();
  timer = setInterval(load, 5000);
  onLeave(() => clearInterval(timer));
}

/* ───────── 무제한 코딩 연습 (Features '무제한 코딩 연습', 2026-11-05) ─────────
   공고·응시와 무관. 게시된 문제만, 횟수 제한 없음, 기록 저장 없음. 기업 코딩테스트(#/interview/<id>의 코딩 단계)와 별개 화면.
   화면 요소는 기존 코딩테스트 화면(.tabs · .tbl · .grid2 · .editor · .out · .btn)을 그대로 쓴다(새 디자인 요소 없음). */
const LEVEL_KO = { basic: '기초', applied: '응용' };

// 레벨: Junior = 초급(프로그래머스 Lv.0~1 수준, DB level 'basic') · Senior = 중·고급(Lv.2 이상, 'applied')
const PRACTICE_LEVELS = [
  { key: 'junior', db: 'basic', name: 'Junior', ko: '초급' },
  { key: 'senior', db: 'applied', name: 'Senior', ko: '중·고급' },
];
const levelOf = (db) => PRACTICE_LEVELS.find((l) => l.db === db) || PRACTICE_LEVELS[0];
// 연습 영역: 알고리즘(Python·C) / SQL(SQLD 유형, 준비 중). 2026-11-06 사용자 지시
const trackTabs = (on) => `<div class="tabs" style="margin:14px 0 0">
  <a class="btn sm ${on === 'algo' ? 'primary' : 'ghost'}" href="#/practice/coding/junior">알고리즘</a>
  <a class="btn sm ${on === 'sql' ? 'primary' : 'ghost'}" href="#/practice/sql">SQL</a></div>`;

// SQL 연습(SQLD 유형 4지선다, 2026-11-06): 과목별 목록 → 문제(표·SQL·보기) → 답 고르기 → 정답·해설. 기록 저장 없음
const SQL_SUBJECTS = [
  { key: 's1', no: 1, name: '제1과목', ko: '데이터 모델링의 이해' },
  { key: 's2', no: 2, name: '제2과목', ko: 'SQL 기본과 활용' },
];
const SQL_KIND_KO = { concept: '개념', result: '실행 결과', sql_pick: 'SQL 고르기' };
const sqlTable = (name, t) => `<div class="tbl-wrap" style="margin:18px 0">${name ? `<div class="small mono" style="margin-bottom:4px">${esc(name)}</div>` : ''}<table class="tbl"><thead><tr>${t.columns.map((c) => `<th>${esc(c)}</th>`).join('')}</tr></thead>
  <tbody>${t.rows.length ? t.rows.map((r) => `<tr>${r.map((v) => `<td class="mono">${v === null ? '<span class="small">NULL</span>' : esc(v)}</td>`).join('')}</tr>`).join('') : `<tr><td colspan="${t.columns.length}" class="small">결과 없음(0행)</td></tr>`}</tbody></table></div>`;

async function practiceSql(sKey) {
  setNav('');
  const sub = SQL_SUBJECTS.find((x) => x.key === (sKey ? `s${sKey}` : 's1'));
  if (!requireLogin(`/practice/sql/${sub.key}`)) return;
  const list = await get('/api/practice/sql');
  const rows = list.filter((p) => p.subject === sub.no);
  view.innerHTML = `<div class="wrap">
    <h1>무제한 코딩 연습</h1><p class="sub">SQLD 시험 유형의 4지선다 문제입니다. 답을 고르면 바로 정답과 해설을 보여 줍니다. 횟수 제한이 없고 기록은 저장하지 않습니다. SQL 결과는 PostgreSQL 16에서 실제로 실행해 계산했습니다.</p>
    ${trackTabs('sql')}
    <div class="practice-layout" style="margin-top:18px">
      <aside class="card col practice-side"><div class="small">과목</div>
        <div class="tabs" style="flex-direction:column">${SQL_SUBJECTS.map((x) => `<a class="${x.key === sub.key ? 'on' : ''}" href="#/practice/sql/${x.key}"><b>${x.name}</b> <span class="small">${x.ko} · ${list.filter((p) => p.subject === x.no).length}문제</span></a>`).join('')}</div>
      </aside>
      <section>
        <div class="between" style="margin-bottom:12px"><h2>${sub.name} <span class="small">${sub.ko}</span></h2><span class="small">${rows.length}문제</span></div>
        ${rows.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>#</th><th>문제</th><th>세부 항목</th><th>형식</th><th></th></tr></thead><tbody>
          ${rows.map((p, i) => `<tr><td class="num">${i + 1}</td><td><b>${esc(p.title)}</b></td><td class="small">${esc(p.item)}</td><td class="small">${SQL_KIND_KO[p.kind]}</td>
            <td class="num"><a class="btn sm" href="#/practice/sql/${p.id}">풀기</a></td></tr>`).join('')}
        </tbody></table></div>` : `<div class="empty">아직 게시된 ${sub.name} 문제가 없습니다.</div>`}
      </section>
    </div></div>`;
}

async function practiceSqlProblem(id) {
  setNav('');
  if (!requireLogin(`/practice/sql/${id}`)) return;
  const p = await get(`/api/practice/sql/${id}`);
  const sub = SQL_SUBJECTS.find((x) => x.no === p.subject);
  const choice = (c) => c.type === 'table' ? sqlTable('', c) : c.type === 'sql' ? `<div class="out mono sql-choice" style="min-height:0;margin:0">${esc(c.sql)}</div>` : `<span>${esc(c.text)}</span>`;
  view.innerHTML = `<div class="wrap sql-q" style="max-width:960px">
    <div class="between" style="margin-bottom:14px"><a class="btn sm ghost" href="#/practice/sql/${sub.key}">← ${sub.name} 문제 목록</a>
      <span class="row"><span class="pill neon">SQL</span><span class="pill dim">${sub.name} · ${esc(p.item)}</span></span></div>
    <div class="card col">
      <h2>${esc(p.title)}</h2><p>${lines(p.stem)}</p>
      ${Object.entries(p.tables || {}).map(([n, t]) => sqlTable(n, t)).join('')}
      ${p.query_sql ? `<div class="small">SQL</div><div class="out mono" style="min-height:0">${esc(p.query_sql)}</div>` : ''}
      <div class="col" role="radiogroup" aria-label="보기">${p.choices.map((c, i) => `<label class="check" style="align-items:flex-start"><input type="radio" name="ans" value="${i + 1}"><span style="flex:1;min-width:0"><b class="sql-no">${'①②③④'[i]}</b> ${choice(c)}</span></label>`).join('')}</div>
      <div class="row"><button class="btn primary" id="ans" disabled>정답 확인</button><span class="sp"></span><span class="small">답을 바꿔 몇 번이든 다시 확인할 수 있습니다.</span></div>
      <div class="out sql-expl" id="out" style="display:none"></div>
    </div></div>`;
  $$('input[name="ans"]').forEach((r) => (r.onchange = () => ($('#ans').disabled = false)));
  $('#ans').onclick = async () => {
    const pick = Number($('input[name="ans"]:checked').value);
    $('#ans').disabled = true;
    try {
      const r = await post(`/api/practice/sql/${id}/answer`, { choice: pick });
      $('#out').style.display = '';
      $('#out').textContent = `${r.correct ? '정답입니다.' : `오답입니다. 정답은 ${'①②③④'[r.answer - 1]}번입니다.`}\n\n[해설]\n${r.explanation}`;
    } catch (e) { toast(e.message, true); } finally { $('#ans').disabled = false; }
  };
}

async function practiceList(levelKey) {
  setNav('');
  const lv = PRACTICE_LEVELS.find((l) => l.key === levelKey);
  if (!lv) { go('#/practice/coding/junior'); return; }
  if (!requireLogin(`/practice/coding/${lv.key}`)) return;
  const list = await get('/api/practice/coding');
  const rows = list.filter((p) => p.level === lv.db);
  view.innerHTML = `<div class="wrap">
    <h1>무제한 코딩 연습</h1><p class="sub">공고 없이 코딩 문제를 원하는 만큼 풀어 볼 수 있습니다. 실행·제출 횟수 제한이 없고, 기록은 기업에 전달되지 않습니다. 알고리즘 문제는 지금 Python 3만 지원합니다(C언어 준비 중).</p>
    ${trackTabs('algo')}
    <div class="practice-layout" style="margin-top:18px">
      <aside class="card col practice-side"><div class="small">레벨</div>
        <div class="tabs" style="flex-direction:column">${PRACTICE_LEVELS.map((l) => `<a class="${l.key === lv.key ? 'on' : ''}" href="#/practice/coding/${l.key}"><b>${l.name}</b> <span class="small">${l.ko} · ${list.filter((p) => p.level === l.db).length}문제</span></a>`).join('')}</div>
      </aside>
      <section>
        <div class="between" style="margin-bottom:12px"><h2>${lv.name} <span class="small">${lv.ko}</span></h2><span class="small">${rows.length}문제</span></div>
        ${rows.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>#</th><th>문제</th><th></th></tr></thead><tbody>
          ${rows.map((p, i) => `<tr><td class="num">${i + 1}</td><td><b>${esc(p.title)}</b></td>
            <td class="num"><a class="btn sm" href="#/practice/coding/${p.id}">풀기</a></td></tr>`).join('')}
        </tbody></table></div>` : `<div class="empty">아직 게시된 ${lv.name} 문제가 없습니다.</div>`}
      </section>
    </div></div>`;
}

async function practiceProblem(id) {
  setNav('');
  if (!requireLogin(`/practice/coding/${id}`)) return;
  const p = await get(`/api/practice/coding/${id}`);
  view.innerHTML = `<div class="wrap" style="max-width:1200px">
    <div class="between" style="margin-bottom:14px"><a class="btn sm ghost" href="#/practice/coding/${levelOf(p.level).key}">← ${levelOf(p.level).name} 문제 목록</a>
      <span class="row"><span class="pill neon">알고리즘</span><span class="tabs lang-switch" role="group" aria-label="풀이 언어"><button type="button" class="on">Python 3</button><button type="button" disabled title="C언어는 준비 중입니다">C · 준비 중</button></span><span class="pill ${p.level === 'basic' ? 'neon' : 'dim'}">${levelOf(p.level).name} · ${levelOf(p.level).ko}</span></span></div>
    <div class="grid2" style="align-items:start;grid-template-columns:minmax(0,.8fr) minmax(0,1.2fr)">
      <div class="card col" id="pdesc"><h2>${esc(p.title)}</h2><p>${lines(p.statement)}</p><div class="small">함수 이름: <span class="mono neon">${esc(p.func_name)}</span></div>
        <h3 style="margin-top:8px">입출력 예시</h3>${p.examples.map((e) => `<div class="out" style="min-height:0">입력: ${esc(JSON.stringify(e.input))}\n출력: ${esc(JSON.stringify(e.expected))}</div>`).join('')}</div>
      <div class="col"><textarea class="editor" id="code" spellcheck="false"></textarea>
        <div class="row"><button class="btn" id="run">예제 실행</button><button class="btn ghost" id="reset">처음 코드로</button><span class="sp"></span><button class="btn primary" id="submit">제출(전체 채점)</button></div>
        <div class="out" id="out">예제 실행은 공개 예제만, 제출은 숨은 테스트까지 채점합니다. 몇 번이든 다시 할 수 있습니다.</div></div>
    </div></div>`;
  const code = $('#code');
  code.value = p.starter;
  code.addEventListener('keydown', (e) => {
    if (e.key === 'Tab') { e.preventDefault(); const s = code.selectionStart; code.setRangeText('    ', s, code.selectionEnd, 'end'); }
  });
  const busy = (on) => { $('#run').disabled = on; $('#submit').disabled = on; };
  $('#reset').onclick = () => { code.value = p.starter; };
  $('#run').onclick = async () => {
    busy(true); $('#out').innerHTML = '<span class="spinner"></span> 실행 중…';
    try {
      const r = await post(`/api/practice/coding/${id}/run`, { code: code.value });
      $('#out').textContent = r.status !== 'ok' ? `오류\n${r.stderr}` :
        r.cases.map((c, i) => `예제 ${i + 1}: ${c.ok ? '통과' : '실패'}${c.error ? ` · ${c.error}` : ` · 출력 ${c.got}`}`).join('\n') + (r.stdout ? `\n\n[print 출력]\n${r.stdout}` : '');
    } catch (e) { $('#out').textContent = e.message; } finally { busy(false); }
  };
  $('#submit').onclick = async () => {
    busy(true);
    $('#out').innerHTML = '<div class="grade-orb-wrap"><div class="grade-orb" aria-hidden="true"></div><span>headhunter 코치가 제출하신 코드를 채점하고 있어요</span></div>';
    try {
      // 채점이 바로 끝나도 모션은 최소 1.5초 보여 준다(사용자 지시)
      const [r] = await Promise.all([post(`/api/practice/coding/${id}/submit`, { code: code.value }), new Promise((ok) => setTimeout(ok, 1500))]);
      $('#out').textContent = r.status !== 'ok' ? `오류\n${r.stderr}` :
        `전체 ${r.passed} / ${r.total} 통과 (숨은 테스트 ${r.hidden_passed} / ${r.hidden_total})${r.passed === r.total ? ' — 모두 통과했습니다.' : ''}\n\n`
        + r.public.map((c, i) => `예제 ${i + 1}: ${c.ok ? '통과' : '실패'}${c.error ? ` · ${c.error}` : ` · 출력 ${c.got}`}`).join('\n');
    } catch (e) { $('#out').textContent = e.message; } finally { busy(false); }
  };
}

async function doneView(appId) { go('#/my'); }

async function resultView(appId) {
  if (!requireLogin()) return;
  setNav('my');
  const r = await get(`/api/applications/${appId}/result`);
  if (!r.published) {
    // 2026-11-18 사용자 지시: 업계 표준 안내 문구(서버가 회사·지원자 이름을 넣어 내려준다)
    const head = r.headline || '결과 공개 전입니다';
    const why = r.message || (r.decision_mode === 'hybrid' && r.analysis_status === 'done' ? '기업 담당자가 AI 판정을 확인한 뒤 공개되며, 확정되면 합불 결과를 이메일로도 안내드립니다.' : 'AI 분석이 끝나면 공개됩니다.');
    view.innerHTML = `<div class="wrap mid"><div class="card col"><h1>${esc(head)}</h1><p>${esc(why)}</p><div id="report-box" class="col"></div><a class="btn" href="#/my">마이페이지로</a></div></div>`;
    reportBox(appId, $('#report-box'));
    return;
  }
  const pass = r.result === 'pass';
  view.innerHTML = `<div class="wrap mid">
    <a class="btn sm ghost" href="#/my">← 마이페이지</a>
    <div class="card ${pass ? 'hl' : ''}" style="margin-top:14px">
      <div class="big" style="${pass ? '' : 'color:var(--white)'}">${pass ? '1차 합격' : '1차 불합격'}</div>
      ${r.message ? `<p style="margin:14px 0 0;line-height:1.9">${r.message.split(/(?<=[.!?])\s+/).map(esc).join('<br><br>')}</p>` : ''}</div>
    <!-- 강점·개선점은 화면에서 빼고 리포트 메일로만 보낸다 (2026-11-18 사용자 지시) -->
    <div id="report-box" class="col" style="margin-top:16px"></div></div>`;
  if (r.status !== '응시 취소') reportBox(appId, $('#report-box'), { noResult: true, mailLabel: 'headhunterAI 리포트를 이메일로 받기' });
}

/* ───────── boot ───────── */
window.addEventListener('hashchange', router);
(async () => {
  await HH.devbar((bar) => {
    const b = document.createElement('button');
    b.textContent = '내 지원 공고 결과 즉시 공개(개발용)';
    b.onclick = async () => {
      if (!me) return toast('로그인 후 사용할 수 있습니다.', true);
      const apps = await get('/api/me/applications');
      for (const a of apps) await post(`/api/dev/publish-now/${a.job_id}`);
      toast('결과 공개 시각을 지금으로 당겼습니다.'); router();
    };
    bar.insertBefore(b, bar.querySelector('.sp'));
    // 지원자 테스트 계정(이지원)만: 접수 취소 → 같은 공고에 다시 지원해 점검·면접을 반복 테스트
    const r = document.createElement('button');
    r.textContent = '접수 취소(테스트 계정)'; r.hidden = true; r.id = 'dev-reset';
    r.onclick = async () => {
      const yes = await HH.modal({ title: '접수 취소(테스트용)', body: '이 계정의 지원·응시 기록을 모두 지웁니다. 이력서 파일은 남습니다. 같은 공고에 다시 지원할 수 있게 됩니다.',
        buttons: [{ label: '닫기', value: false }, { label: '모두 취소', value: true, primary: true }] });
      if (!yes) return;
      try { const x = await post('/api/dev/reset-my-applications'); toast(`지원 ${x.deleted}건을 취소했습니다.`); go('#/jobs'); router(); } catch (e) { toast(e.message, true); }
    };
    bar.insertBefore(r, bar.querySelector('.sp'));
  });
  await loadMe();
  router();
})();
