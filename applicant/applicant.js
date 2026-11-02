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

function onLeave(fn) { cleanup.push(fn); }
function go(hash) { location.hash = hash; }
function setNav(key) { $$('#top nav a').forEach((a) => a.classList.toggle('on', a.dataset.nav === key)); }

// 지원자 테스트 계정(시드 이지원): 접수 취소 버튼이 보인다. 서버도 이 계정만 허용(config.TEST_APPLICANT_EMAIL)
const isTestApplicant = () => !!(me && me.email === 'applicant@headhunter.ai');
async function loadMe() {
  me = await get('/api/auth/me');
  if (me && me.role !== 'applicant') me = null;
  const area = $('#auth-area');
  area.innerHTML = me
    ? `<span class="who">${esc(me.name)} 님</span> <button class="btn sm ghost" id="logout">로그아웃</button>`
    : `<a class="btn sm ghost" href="#/login">로그인</a> <a class="btn sm primary" href="#/signup">회원가입</a>`;
  const reset = document.getElementById('dev-reset');
  if (reset) reset.hidden = !isTestApplicant();
  const lo = $('#logout');
  if (lo) lo.onclick = async () => { await post('/api/auth/logout'); me = null; await loadMe(); go('#/'); };
}

function requireLogin(next) {
  if (me) return true;
  go(`#/login?next=${encodeURIComponent(next || location.hash.slice(1))}`);
  return false;
}

// ACC-04: 허용된 내부 경로만 복귀
function safeNext(next) {
  return next && /^\/(jobs\/\d+(\/apply)?|my|apply\/\d+\/[a-z]+)$/.test(next) ? next : '/my';
}

const routes = [
  [/^\/?$/, landing],
  [/^\/jobs$/, jobs],
  [/^\/jobs\/(\d+)$/, jobDetail],
  [/^\/jobs\/(\d+)\/apply$/, applyJob],
  [/^\/login$/, login],
  [/^\/signup$/, signup],
  [/^\/my$/, mypage],
  [/^\/apply\/(\d+)\/consent$/, consentView],
  [/^\/apply\/(\d+)\/resume$/, resumeView],
  [/^\/apply\/(\d+)\/env$/, envView],
  [/^\/interview\/(\d+)$/, interviewView],
  [/^\/done\/(\d+)$/, doneView],
  [/^\/result\/(\d+)$/, resultView],
];

async function router() {
  cleanup.forEach((f) => { try { f(); } catch (_) { /* ignore */ } });
  cleanup = [];
  const raw = location.hash.slice(1) || '/';
  const [path, qs] = raw.split('?');
  const params = new URLSearchParams(qs || '');
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
  setNav('');
  view.innerHTML = `
  <div class="wrap">
    <section class="hero">
      <div>
        <h1>인재를 발견하는 새로운 기준,<br><span class="neon">headhunterAI</span></h1>
        <p class="lead">이력서를 올리면 AI가 내용을 읽고 나에게 맞춘 질문으로 1차 면접을 진행합니다. 기업은 같은 기준과 근거로 1차 결과를 받아 봅니다.</p>
        <div class="row">
          <a class="btn primary lg" href="#/jobs">지원자로 시작하기 →</a>
          <a class="btn lg" href="/admin/">기업 담당자 로그인</a>
        </div>
      </div>
      <img class="hero-img" src="/images/mainpage_image.png.png" alt="">
    </section>
  </div>`;
}

/* ───────── 공고 ───────── */
function jobCard(j) {
  const closed = j.status === 'closed';
  return `<button class="card click" data-job="${j.id}">
    <div class="between"><span class="pill ${closed ? 'dim' : 'neon'}">${closed ? '마감' : '접수 중'}</span>
      ${j.coding_enabled ? '<span class="pill">코딩테스트 포함</span>' : '<span class="pill dim">AI 면접만</span>'}</div>
    <h2 style="margin:12px 0 4px">${esc(j.title)}</h2>
    <div class="sub">${esc(j.company)} · ${esc(j.role_name)} · ${esc(j.location || '')}</div>
    <div class="small" style="margin-top:10px">접수 마감 ${fmtYMD(j.close_date)} · 결과 공개 ${fmtYMD(j.result_date)}</div>
  </button>`;
}

async function jobs(params) {
  setNav('jobs');
  if (!roles.length) roles = await get('/api/job-roles');
  const role = params.get('role') || '';
  const q = params.get('q') || '';
  view.innerHTML = `<div class="wrap">
    <h1>채용 공고</h1><p class="sub">로그인 없이 공고를 둘러볼 수 있습니다. 현재 AI 면접은 AI/AX 엔지니어 직무만 운영합니다.</p>
    <div class="row" style="margin:18px 0">
      <select id="f-role"><option value="">전체 직무</option>${roles.map((r) => `<option value="${r.code}" ${r.status !== 'active' ? 'disabled' : ''} ${r.code === role ? 'selected' : ''}>${esc(r.name)}${r.status !== 'active' ? ' (준비 중)' : ''}</option>`).join('')}</select>
      <input class="input" id="f-q" placeholder="공고명·회사명 검색" value="${esc(q)}" style="max-width:280px">
      <button class="btn" id="f-go">검색</button>
    </div>
    <div class="row" style="margin-bottom:18px">${roles.map((r) => `<span class="pill ${r.status === 'active' ? 'neon' : 'dim'}">${esc(r.name)}${r.status === 'active' ? '' : ' · 준비 중'}</span>`).join('')}</div>
    <div class="grid3" id="list"><span class="spinner"></span></div></div>`;
  const qs = new URLSearchParams();
  if (role) qs.set('role', role);
  if (q) qs.set('q', q);
  const list = await get('/api/jobs?' + qs);
  $('#list').innerHTML = list.length ? list.map(jobCard).join('') : '<div class="empty" style="grid-column:1/-1">조건에 맞는 공고가 없습니다.</div>';
  $$('[data-job]').forEach((b) => (b.onclick = () => go(`#/jobs/${b.dataset.job}`)));
  $('#f-go').onclick = () => go(`#/jobs?role=${$('#f-role').value}&q=${encodeURIComponent($('#f-q').value)}`);
  $('#f-q').onkeydown = (e) => { if (e.key === 'Enter') $('#f-go').click(); };
}

const lines = (t) => esc(t || '').replace(/\n/g, '<br>');

async function jobDetail(id) {
  setNav('jobs');
  const j = await get(`/api/jobs/${id}`);
  const closed = j.status === 'closed';
  view.innerHTML = `<div class="wrap mid">
    <a class="btn sm ghost" href="#/jobs">← 공고 목록</a>
    <h1 style="margin-top:14px">${esc(j.title)}</h1>
    <div class="sub">${esc(j.company)} · ${esc(j.role_name)} · ${esc(j.employment_type)} · ${esc(j.location)} · 모집 ${j.headcount}명</div>
    <div class="grid3" style="margin:22px 0">
      <div class="card"><div class="small">접수 기간</div><b>${fmtYMD(j.open_date)} ~ ${fmtYMD(j.close_date)}</b></div>
      <div class="card"><div class="small">진행 방식 · 예상 소요</div><b>AI 면접 ${j.coding_enabled ? '+ 코딩테스트' : ''} · 약 ${j.expected_minutes}분</b></div>
      <div class="card"><div class="small">1차 결과 공개</div><b>${fmtYMD(j.result_date)} 10:00</b></div>
    </div>
    <div class="card col">
      <h3>회사 소개</h3><p class="sub">${lines(j.company_intro)}</p>
      <h3>주요 업무</h3><p class="sub">${lines(j.duties)}</p>
      <h3>자격 요건</h3><p class="sub">${lines(j.requirements)}</p>
      ${j.preferred ? `<h3>우대 사항</h3><p class="sub">${lines(j.preferred)}</p>` : ''}
    </div>
    <div class="note" style="margin:18px 0">지원 후 이력서를 올리면 AI가 내용을 읽어 맞춤 질문 2개를 만듭니다. AI 면접은 1회만 응시할 수 있고 중단 버튼이 없습니다.</div>
    <button class="btn primary lg" id="apply" ${closed ? 'disabled' : ''}>${closed ? '접수 마감' : '지원하기'}</button>
  </div>`;
  $('#apply').onclick = () => go(`#/jobs/${id}/apply`);
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
    <h1>지원자 로그인</h1><p class="sub">로그인 후 지원 절차로 돌아갑니다.</p>
    <div class="err" id="err" hidden></div>
    <div class="field" style="margin-top:16px"><label>이메일</label><input id="email" type="email" autocomplete="username"></div>
    <div class="field"><label>비밀번호</label><input id="pw" type="password" autocomplete="current-password"></div>
    <button class="btn primary" id="go" style="width:100%">로그인</button>
    <hr><div class="between"><span class="small">아직 회원이 아니신가요?</span><a class="btn sm" href="#/signup?next=${encodeURIComponent(next)}">지원자로 회원가입하기</a></div>
    <div class="small" style="margin-top:12px">기업 담당자는 <a href="/admin/">기업 담당자 로그인</a>을 이용해 주세요.</div>
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
    <h1>지원자 회원가입</h1><p class="sub">평가와 무관한 생년월일·성별·주소·사진은 받지 않습니다.</p>
    <div class="err" id="err" hidden></div>
    <div class="field" style="margin-top:16px"><label>이름 *</label><input id="name" autocomplete="name"></div>
    <div class="field"><label>이메일 (아이디) *</label><input id="email" type="email" autocomplete="email"></div>
    <div class="field"><label>비밀번호 * (8자 이상)</label><input id="pw" type="password" autocomplete="new-password"></div>
    <div class="field"><label>휴대전화번호 *</label><input id="phone" placeholder="010-0000-0000" autocomplete="tel"></div>
    <label class="check"><input type="checkbox" id="age14"><span>만 14세 이상입니다. <span class="small">(필수)</span></span></label>
    <label class="check"><input type="checkbox" id="terms"><span>이용약관에 동의합니다. <span class="small">(필수)</span></span></label>
    <label class="check"><input type="checkbox" id="privacy"><span>회원 서비스 제공을 위한 개인정보 수집·이용(이름·이메일·비밀번호·휴대전화번호, 탈퇴 시까지 보관)에 동의합니다. <span class="small">(필수)</span></span></label>
    <button class="btn primary" id="go" style="width:100%;margin-top:10px">가입하기</button>
    <p class="small" style="margin-top:10px">응시 데이터 수집 동의는 공고에 지원할 때 따로 받습니다.</p>
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
  return `<span class="small">결과 공개 ${fmtYMD(a.result_date)} 10:00</span>`;
}

async function mypage() {
  setNav('my');
  if (!requireLogin('/my')) return;
  const [apps, notes, resumes] = await Promise.all([get('/api/me/applications'), get('/api/me/notifications'), get('/api/me/resumes')]);
  view.innerHTML = `<div class="wrap">
    <h1>마이페이지</h1><p class="sub">${esc(me.name)} 님의 지원 현황입니다.</p>
    <div class="grid2" style="margin-top:20px;align-items:start">
      <div class="card" style="grid-column:1/-1"><div class="between"><h2>지원한 공고 · 응시할 면접</h2><a class="btn sm" href="#/jobs">공고 둘러보기</a></div>
        ${apps.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>공고</th><th>진행 상태</th><th>응시 상태</th><th></th></tr></thead><tbody>
        ${apps.map((a) => `<tr><td><b>${esc(a.title)}</b><div class="small">${esc(a.company)} · 지원일 ${fmtYMD(a.created_at)}${a.coding_enabled ? ' · 코딩테스트 포함' : ''}</div></td>
          <td>${a.attempt_id ? '<span class="pill dim">응시함</span>' : `<span class="pill neon">${STEP_KO[a.step]}</span>`}</td>
          <td>${a.attempt_status ? esc(ATTEMPT_KO[a.attempt_status]) : '-'}</td><td class="num">${appAction(a)}${isTestApplicant() ? ` <button class="btn sm ghost" data-devcancel="${a.id}" title="테스트 계정 전용">접수 취소</button>` : ''}</td></tr>`).join('')}
        </tbody></table></div>` : '<div class="empty">아직 지원한 공고가 없습니다.</div>'}
      </div>
      <div class="card"><h2>알림</h2>${notes.length ? notes.map((n) => `<div style="padding:8px 0;border-bottom:1px solid var(--line)"><b>${esc(n.title)}</b><div class="small">${esc(n.body)}</div></div>`).join('') : '<p class="small">새 알림이 없습니다.</p>'}</div>
      <div class="card"><h2>내 이력서</h2><p class="small">지원할 때 불러와 쓸 수 있습니다. 지원에 제출한 이력서는 별도로 보관됩니다.</p>
        ${resumes.length ? resumes.map((r) => `<div class="between" style="padding:8px 0;border-bottom:1px solid var(--line)"><span>${esc(r.title)} <span class="small">${fmtYMD(r.updated_at)}</span></span><button class="btn sm ghost" data-delres="${r.id}">삭제</button></div>`).join('') : '<p class="small">등록한 이력서가 없습니다. 지원 과정에서 저장할 수 있습니다.</p>'}</div>
    </div></div>`;
  $$('[data-cont]').forEach((b) => (b.onclick = async () => continueApplication(await get(`/api/applications/${b.dataset.cont}`))));
  $$('[data-devcancel]').forEach((b) => (b.onclick = async () => {
    const yes = await modal({ title: '접수 취소(테스트용)', body: '이 지원 건의 응시·답변·평가 기록을 모두 지웁니다. 같은 공고에 다시 지원할 수 있게 됩니다.',
      buttons: [{ label: '닫기', value: false }, { label: '접수 취소', value: true, primary: true }] });
    if (!yes) return;
    try { await post(`/api/dev/reset-my-applications?app_id=${b.dataset.devcancel}`); toast('접수를 취소했습니다.'); mypage(); } catch (e) { toast(e.message, true); }
  }));
  $$('[data-delres]').forEach((b) => (b.onclick = async () => { await del(`/api/me/resumes/${b.dataset.delres}`); mypage(); }));
  if (notes.some((n) => !n.read_at)) post('/api/me/notifications/read');
}

/* ───────── 지원 단계 공통 ───────── */
function stepsBar(now) {
  const s = [['consent', '동의'], ['resume', '이력서'], ['env', '환경 점검'], ['interview', 'AI 면접'], ['done', '응시 완료']];
  const idx = s.findIndex((x) => x[0] === now);
  return `<div class="steps">${s.map((x, i) => `<span class="${i < idx ? 'done' : i === idx ? 'now' : ''}">${i + 1}. ${x[1]}</span>`).join('')}</div>`;
}

/* ───────── 동의 (APP-03) ───────── */
const CONSENT_TEXT = [
  ['data', '응시 데이터 수집·이용', '이력서 원본과 OCR 인식 결과, 답변 음성·텍스트, 카메라 녹화, 코드, 장애·이탈 기록을 1차 평가에 사용합니다.'],
  ['ai', 'AI 질문 생성·자동 평가', '이력서 내용에 근거해 AI가 질문을 만들고, AI 평가와 정해진 규칙으로 1차 합격·불합격이 자동 산출됩니다. 결과에 대해 설명을 요구할 수 있습니다.'],
  ['share', '지원 기업에 결과 제공', '평가 결과와 근거를 지원한 기업의 채용 담당자에게 제공합니다.'],
  ['retention', '보관 기간', '응시 데이터는 결과 공개일부터 7일, 카메라 녹화는 응시 종료부터 3일 보관 후 자동 삭제됩니다.'],
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
    <div class="note" style="margin:14px 0">필수 항목에 모두 동의해야 다음 단계로 갈 수 있습니다. 동의를 철회하면 진행 중인 응시는 취소되고 데이터는 즉시 삭제됩니다. (동의 문구 버전은 기록됩니다)</div>
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

function bindForm(state) {
  $$('[data-add]').forEach((b) => (b.onclick = () => { state.data = readForm(state.data); state.data[b.dataset.add].push({}); renderEditor(state); }));
  $$('[data-rm]').forEach((b) => (b.onclick = () => { state.data = readForm(state.data); const [s, i] = b.dataset.rm.split(':'); state.data[s].splice(+i, 1); renderEditor(state); }));
}

function renderEditor(state) {
  const left = state.extraction ? originalPane(state.extraction) : '';
  $('#editor').innerHTML = `${state.extraction && state.extraction.status === 'failed'
    ? '<div class="err" style="margin-bottom:14px">파일에서 내용을 충분히 읽지 못했습니다. 직접 입력해 주세요. 인식된 부분은 미리 채워 두었습니다.</div>' : ''}
    ${state.extraction && state.extraction.status === 'review' ? '<div class="note" style="margin-bottom:14px">원문과 인식 결과를 비교해 틀린 부분을 고쳐 주세요. 점선으로 표시된 항목은 인식 신뢰도가 낮은 쪽에서 나온 것입니다. AI 질문은 확인을 마친 내용만 사용합니다.</div>' : ''}
    <div class="${left ? 'split' : ''}">${left}<div>${formHtml(state.data)}</div></div>
    <div class="card" style="margin-top:16px"><label class="check"><input type="checkbox" id="save"><span>이 내용을 마이페이지 '내 이력서'에도 저장</span></label>
      <div class="row" style="margin-top:8px"><button class="btn primary lg" id="confirm">내용 확인 완료</button><span class="small">확인 완료한 이력서는 지원 시점 기준으로 고정 보관됩니다.</span></div></div>`;
  bindForm(state);
  $('#confirm').onclick = async () => {
    const data = readForm(state.data);
    try {
      await post(`/api/applications/${state.appId}/resume`, { data, extraction_id: state.extraction ? state.extraction.id : null, save_title: $('#save').checked ? `${data.basic.name || '이력서'} (${new Date().toLocaleDateString('ko-KR')})` : null });
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
      $('#progress').innerHTML = `<div class="card"><div class="between"><b><span class="spinner"></span> ${STAGE_KO[x.stage] || '처리 중'}</b><span class="small">${x.progress}%</span></div>
        <div class="bar" style="margin-top:10px"><i style="width:${x.progress}%"></i></div>
        <p class="small" style="margin-top:8px">텍스트 추출 → 문자 인식 → 항목 정리 순서로 진행됩니다. 모든 처리는 사내 서버에서만 이뤄집니다.</p>
        <div class="row" style="margin-top:10px"><button class="btn sm" id="skip-wait">기다리지 않고 다음 단계로</button>
          <span class="small">저장해 둔 이력서가 있으면 그 내용으로 바로 환경 점검으로 넘어가고, 없으면 직접 작성 화면을 엽니다.</span></div></div>`;
      $('#skip-wait').onclick = () => { stopped = true; skipWait(state); };
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
        <div class="card"><div class="between"><h3>③ 스피커</h3><span class="pill dim" id="s-speaker">대기</span></div><p class="small">면접 때 쓸 헤드폰·스피커를 고르고 테스트음을 재생하세요. 소리가 끝까지 들렸을 때만 '들립니다'를 누르세요. 면접 질문 음성은 Windows 기본 출력 장치로 나오므로, 헤드폰을 쓰려면 Windows에서도 헤드폰을 기본 장치로 지정해 두세요.</p>
          <div class="row" style="margin:8px 0" id="spk-row"><select id="spk-sel" style="flex:1;min-width:0"><option value="">기본 출력 장치</option></select></div>
          <div class="row"><button class="btn sm" id="tone">테스트음 재생</button><button class="btn sm" id="heard" disabled>들립니다</button><button class="btn sm ghost" id="not-heard" disabled>안 들립니다</button></div>
          <p class="small" id="spk-msg" style="display:none"></p></div>
        <div class="card"><div class="between"><h3>④ 네트워크 · ⑤ AI 서버</h3><span class="pill dim" id="s-net">확인 중</span></div><p class="small" id="net-msg">서버 응답을 확인하고 있습니다.</p></div>
      </div>
      <div class="col">
        <div class="card hl"><h2>시작 전 안내</h2>
          <dl class="kv"><dt>진행 방식</dt><dd>AI 면접${coding ? ' → 코딩테스트(Python, 2문항 60분)' : ''}</dd>
          <dt>질문 수</dt><dd>기본 질문 10개(인성 2·이력서 기반 2·직무 4·기업 2) + 꼬리질문 최대 4개</dd>
          <dt>답변 시간</dt><dd>기본 질문 90초, 꼬리질문 60초. 질문 음성이 끝나면 3-2-1 후 시작</dd>
          <dt>예상 소요</dt><dd>약 ${25 + (coding ? 60 : 0)}분</dd>
          <dt>중단</dt><dd>중단·일시정지 버튼이 없습니다. 시작하면 끝까지 진행합니다.</dd>
          <dt>장애</dt><dd>네트워크·기기 장애로 끊기면 다시 접속해 끊긴 질문부터 이어갑니다. 탭 닫기·새로고침은 응시 종료로 처리됩니다.</dd>
          <dt>화면 이탈</dt><dd>다른 탭·창으로 전환, 2초 이상 포커스 이탈, 전체 화면 해제 시 1회 경고, 2회째 응시가 취소됩니다.</dd></dl>
          <label class="check" style="margin-top:10px"><input type="checkbox" id="rules"><b>위 응시 규칙을 확인했습니다</b></label>
          <p class="small" id="pregen">맞춤 질문은 점검을 통과하면 준비를 시작합니다.</p>
          <div class="note">개발 환경 안내: 음성 인식(STT) 엔진이 연결되지 않아 답변은 입력창에 작성합니다. 질문 음성은 브라우저 내장 한국어 음성으로 재생됩니다.</div>
          <button class="btn primary lg" id="start" disabled style="width:100%;margin-top:12px">전체 화면으로 면접 시작</button>
          <p class="small" id="start-why" style="margin-top:8px"></p>
        </div>
      </div>
    </div></div>`;
  const mark = (k, pass, label) => { ok[k] = pass; const el = $(`#s-${k}`); if (el) { el.textContent = label || (pass ? '통과' : '실패'); el.className = 'pill ' + (pass ? 'on' : 'dim'); } refresh(); };
  let envPosted = false;
  async function refresh() {
    const all = Object.values(ok).every(Boolean);
    if (all && !envPosted) {
      envPosted = true;
      try { await post(`/api/applications/${appId}/env`, { ...ok, cam: camReal }); watchPregen(); } catch (e) { envPosted = false; toast(e.message, true); }
    }
    $('#start').disabled = !(all && envPosted && $('#rules').checked);
    // 버튼이 왜 안 눌리는지 바로 보이게: 남은 항목을 적어 준다
    const names = { mic: '① 마이크', cam: '② 카메라', speaker: '③ 스피커', network: '④ 네트워크', ai: '⑤ AI 서버' };
    const left = Object.keys(names).filter((k) => !ok[k]).map((k) => names[k]);
    if (!$('#rules').checked) left.push('응시 규칙 확인');
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
    // 시스템이 알려 주는 장치를 거르지 않고 그대로 모두 보여 준다(Windows '기본값 -'·'커뮤니케이션 -' 항목 포함)
    const put = (sel, kind, cur, base) => {
      const opts = list.filter((d) => d.kind === kind && d.deviceId);
      const hasDefault = opts.some((d) => d.deviceId === 'default');
      const want = cur || (hasDefault ? 'default' : '');
      $(sel).innerHTML = (hasDefault ? '' : `<option value="">${base}</option>`)
        + opts.map((d, i) => `<option value="${esc(d.deviceId)}"${d.deviceId === want ? ' selected' : ''}>${esc(d.label || `${base.slice(3)} ${i + 1}`)}</option>`).join('');
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
    } else { // 임시(2026-10-29, 사용자 결정): 카메라가 없어도 통과. 원래 APP-06은 카메라 필수
      camReal = false; mark('cam', true, '없음 · 생략');
      $('#cam').textContent = '카메라 없이 진행합니다';
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
      const a = await get(`/api/applications/${appId}`);
      const label = { running: '<span class="spinner"></span> 이력서를 바탕으로 맞춤 질문을 준비하고 있습니다. 준비 중에도 시작할 수 있습니다.', ready: '✓ 맞춤 질문 준비가 끝났습니다.', failed: '맞춤 질문 준비에 실패해 기본 질문 형식으로 진행합니다.', none: '' }[a.pregen_status];
      const el = $('#pregen'); if (el) el.innerHTML = label;
      if (a.pregen_status === 'running' || a.pregen_status === 'none') setTimeout(t, 2000);
    };
    t();
  }

  $('#start').onclick = async () => {
    try { await document.documentElement.requestFullscreen(); } catch (_) { /* fullscreen not allowed */ }
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
  onLeave(() => { $('#top').hidden = false; });
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
      try { await document.documentElement.requestFullscreen(); } catch (_) { /* ignore */ }
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
  document.addEventListener('visibilitychange', onVis);
  window.addEventListener('blur', onBlur);
  window.addEventListener('focus', onFocus);
  document.addEventListener('fullscreenchange', onFs);
  window.addEventListener('offline', onOffline);
  window.addEventListener('online', onOnline);
  onLeave(() => {
    document.removeEventListener('visibilitychange', onVis); window.removeEventListener('blur', onBlur); window.removeEventListener('focus', onFocus);
    document.removeEventListener('fullscreenchange', onFs); window.removeEventListener('offline', onOffline); window.removeEventListener('online', onOnline);
    clearInterval(qTimer); clearTimeout(blurTimer); speechSynthesis.cancel();
    if (codingCtx) codingCtx.stop();
    if (wave) { wave.stop(); wave = null; }
  });

  async function ensureCamera() {
    if (media.stream && media.stream.active) return media.stream;
    const r = await openMedia();
    media.stream = r.stream;
    return r.mic ? null : media.stream; // 카메라는 임시로 선택(env 점검 참고)
  }
  function attachCam() {
    const box = $('#cam');
    if (!box || !media.stream) return;
    if (!media.stream.getVideoTracks().length) { box.textContent = '카메라 없음'; return; }
    const v = document.createElement('video'); v.autoplay = true; v.muted = true; v.playsInline = true; v.srcObject = media.stream;
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
    if (st.status === 'interrupted') return renderInterrupted();
    if (['completed', 'terminated', 'cancelled'].includes(st.status)) return renderEnded();
    if (st.status === 'interview') return renderInterview();
    if (st.status === 'coding') return renderCoding();
  }

  function renderEnded() {
    active = false;
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    stopMedia();
    const pub = st.publish_at ? new Date(st.publish_at) : null;
    const when = pub ? `${pub.getMonth() + 1}월 ${pub.getDate()}일` : '';
    const msg = st.status === 'cancelled'
      ? ['응시가 취소되었습니다', '화면 이탈이 2회 감지되어 규칙에 따라 응시가 취소되었습니다. 이 지원 건은 다시 응시할 수 없습니다.']
      : st.status === 'terminated'
        ? ['응시가 종료되었습니다', `면접 화면을 직접 닫거나 새로고침해 응시가 종료되었습니다. 답변한 문항까지 평가되며, 결과는 ${when} 공개됩니다.`]
        : ['응시가 완료되었습니다', `결과는 ${when} 공개됩니다. 공개되면 알림으로 알려 드립니다.`];
    view.innerHTML = `<div class="wrap narrow" style="padding-top:10vh">${stepsBar('done')}<div class="card ${st.status === 'completed' ? 'hl' : ''} col">
      <h1>${msg[0]}</h1><p>${msg[1]}</p><a class="btn primary" href="#/my">마이페이지로</a></div></div>`;
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
      const okDev = !!s;
      $('#re-dev').textContent = okDev ? '마이크 정상' : '마이크 권한이 필요합니다';
      $('#re-dev').className = 'pill ' + (okDev ? 'on' : 'dim');
      $('#resume').disabled = !okDev;
    });
    $('#resume').onclick = async () => {
      try { await document.documentElement.requestFullscreen(); } catch (_) { /* ignore */ }
      st = await post(`/api/attempts/${attemptId}/resume`);
      render();
    };
  }

  function renderInterview() {
    const q = st.question;
    if (!q) return;
    current = q;
    view.innerHTML = `<div class="wrap" style="max-width:1000px">
      <div class="between" style="margin-bottom:14px"><span class="logo">headhunter<span class="accent">AI</span></span>
        <span class="row"><span class="pill neon">${q.seq} / ${st.total}${q.followup ? ' · 꼬리질문' : ''}</span><span class="pill dim">${HH.CAT_KO[q.category] || ''}</span>
        ${st.exit_count ? `<span class="pill">화면 이탈 ${st.exit_count}회</span>` : ''}</span></div>
      <div class="stage">
        <div class="between" style="align-items:flex-start;gap:20px">
          <div style="flex:1;min-width:260px" class="col"><div class="small">${q.followup ? '꼬리질문' : `질문 ${q.seq}`}</div><div class="qbox" id="qtext">${esc(q.text)}</div></div>
          <div class="col" style="justify-items:center"><div class="cam" id="cam">카메라</div><div class="timer idle" id="timer">${q.time_limit}</div></div>
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
    $('#ans').addEventListener('paste', (e) => e.preventDefault());
    active = true;
    present(q);
  }

  function present(q) {
    clearInterval(qTimer);
    speechSynthesis.cancel();
    const phase = $('#phase');
    const afterSpeech = () => countdown(3, () => startAnswer(q.time_limit));
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
      if (phase) phase.textContent = '질문을 읽어 드리고 있습니다…';
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
    $('#phase').innerHTML = '<span class="spinner"></span> 답변을 저장하고 다음 질문을 준비하고 있습니다…';
    try {
      st = await post(`/api/attempts/${attemptId}/answer`, { question_id: current.id, text: ans.value });
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
      $('#done') && ($('#done').disabled = true);
      $('#timer') && ($('#timer').textContent = current.time_limit);
      present(current);
    } else if (st.status === 'coding' && codingCtx) {
      active = true; codingCtx.resumeTimer();
    }
  }

  async function renderCoding() {
    const data = await get(`/api/attempts/${attemptId}/coding`);
    let idx = 0;
    const codes = Object.fromEntries(data.problems.map((p) => [p.problem_id, p.code]));
    const dirty = new Set();
    let internalClip = '';
    remaining = data.remaining;
    view.innerHTML = `<div class="wrap" style="max-width:1200px">
      <div class="between" style="margin-bottom:14px"><span class="logo">headhunter<span class="accent">AI</span></span>
        <span class="row"><span class="pill neon">코딩테스트 · Python 3</span><span class="pill" id="saved">자동 저장 대기</span><div class="timer" id="ctimer" style="width:76px;height:76px;font-size:18px">--:--</div></span></div>
      <div class="tabs" id="tabs">${data.problems.map((p, i) => `<button data-i="${i}">${i + 1}. ${esc(p.title)} <span class="small">(${p.level === 'basic' ? '기초' : '응용'})</span></button>`).join('')}</div>
      <div class="grid2" style="margin-top:14px;align-items:start;grid-template-columns:minmax(0,.8fr) minmax(0,1.2fr)">
        <div class="card col" id="pdesc"></div>
        <div class="col"><textarea class="editor" id="code" spellcheck="false"></textarea>
          <div class="row"><button class="btn" id="run">예제 실행</button><span class="sp"></span><button class="btn primary" id="submit">최종 제출</button></div>
          <div class="out" id="out">실행 결과가 여기에 표시됩니다. 실행만으로는 제출되지 않습니다.</div></div>
      </div>
      <p class="small" style="margin-top:12px">AI 도우미는 제공되지 않습니다. 편집기 밖에서 복사한 내용은 붙여넣을 수 없습니다. 코드는 30초마다 자동 저장됩니다.</p></div>`;
    const code = $('#code');
    const show = (i) => {
      if (data.problems[idx]) codes[data.problems[idx].problem_id] = code.value;
      idx = i;
      const p = data.problems[i];
      $$('#tabs button').forEach((b) => b.classList.toggle('on', +b.dataset.i === i));
      $('#pdesc').innerHTML = `<h2>${esc(p.title)}</h2><p>${esc(p.statement)}</p><div class="small">함수 이름: <span class="mono neon">${esc(p.func_name)}</span></div>
        <h3 style="margin-top:8px">입출력 예시</h3>${p.examples.map((e) => `<div class="out" style="min-height:0">입력: ${esc(JSON.stringify(e.input))}\n출력: ${esc(JSON.stringify(e.expected))}</div>`).join('')}`;
      code.value = codes[p.problem_id];
    };
    $$('#tabs button').forEach((b) => (b.onclick = () => show(+b.dataset.i)));
    show(0);
    code.addEventListener('input', () => dirty.add(data.problems[idx].problem_id));
    code.addEventListener('keydown', (e) => {
      if (e.key === 'Tab') { e.preventDefault(); const s = code.selectionStart; code.setRangeText('    ', s, code.selectionEnd, 'end'); dirty.add(data.problems[idx].problem_id); }
    });
    const remember = () => { internalClip = code.value.substring(code.selectionStart, code.selectionEnd); };
    code.addEventListener('copy', remember); code.addEventListener('cut', remember);
    code.addEventListener('paste', (e) => {                // COD-07
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
      const el = $('#saved'); if (el) el.textContent = `자동 저장 ${new Date().toLocaleTimeString('ko-KR')}`;
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
      const okGo = await modal({ title: '최종 제출할까요?', body: '제출하면 수정할 수 없고 응시가 완료됩니다. 두 문제 모두 현재 코드로 채점됩니다.', buttons: [{ label: '취소', value: false }, { label: '최종 제출', value: true, primary: true }] });
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
async function doneView(appId) { go('#/my'); }

async function resultView(appId) {
  if (!requireLogin()) return;
  setNav('my');
  const r = await get(`/api/applications/${appId}/result`);
  if (!r.published) {
    view.innerHTML = `<div class="wrap narrow"><div class="card col"><h1>결과 공개 전입니다</h1><p>1차 결과는 ${fmtDate(r.publish_at)} 10:00에 공개됩니다.</p><a class="btn" href="#/my">마이페이지로</a></div></div>`;
    return;
  }
  const pass = r.result === 'pass';
  view.innerHTML = `<div class="wrap mid">
    <a class="btn sm ghost" href="#/my">← 마이페이지</a>
    <div class="card ${pass ? 'hl' : ''}" style="margin-top:14px"><div class="small">1차 결과 · ${esc(r.status)}</div>
      <div class="big" style="margin:8px 0;${pass ? '' : 'color:var(--white)'}">${pass ? '1차 합격' : '1차 불합격'}</div>
      ${r.reason ? `<p>${esc(r.reason)}</p>` : ''}<p class="small">열람 기한: ${fmtDate(r.expires_at)}까지</p></div>
    ${(r.strengths || []).length || (r.improvements || []).length ? `<div class="grid2" style="margin-top:16px;align-items:start">
      <div class="card"><h2>강점</h2>${(r.strengths || []).map((s, i) => `<p><b class="neon">${i + 1}.</b> ${esc(s)}</p>`).join('')}</div>
      <div class="card"><h2>개선점</h2>${(r.improvements || []).map((s, i) => `<p><b>${i + 1}.</b> ${esc(s)}</p>`).join('')}</div></div>` : ''}
    ${r.notice ? `<div class="note" style="margin-top:16px">${esc(r.notice)}</div>` : ''}</div>`;
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
