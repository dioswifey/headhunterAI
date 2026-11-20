/* headhunterAI 기업 담당자 화면 — hash router SPA (PRD 3.3, 4.2, 4.9, 4.10) */
const { get, post, put, del, esc, $, $$, toast, modal, fmtYMD, fmtDateTime, score, ITEM_KO, CAT_KO, ATTEMPT_KO, ANALYSIS_KO, SECTION_KO } = HH;
const root = $('#root');
let me = null;
let roles = [];
const ITEMS = ['expertise', 'logic', 'relevance', 'delivery', 'fit'];
const WKEY = { expertise: 'w_expertise', logic: 'w_logic', relevance: 'w_relevance', delivery: 'w_delivery', fit: 'w_fit' };

function go(h) { location.hash = h; }
const T = (k, v) => HH.t(k, v);   // 언어 전환(2026-11-19)

async function loadMe() {
  me = await get('/api/auth/me');
  if (me && !['recruiter', 'operator'].includes(me.role)) me = null;
  $('#auth-area').innerHTML = me ? `<span class="who">${esc(me.name)} 님</span> <button class="btn sm ghost" id="logout">로그아웃</button>` : '';
  const lo = $('#logout');
  if (lo) lo.onclick = async () => { await post('/api/auth/logout'); me = null; await loadMe(); router(); };
}

/* ───────── 로그인 · 비밀번호 변경 (ACC-03, ACC-05) ───────── */
function loginView() {
  root.innerHTML = `<div class="wrap narrow" style="padding-top:8vh"><div class="card">
    <h1>${T('기업 담당자 로그인')}</h1><p class="sub">${T('기업 담당자 계정은 운영 측에서 사전 발급합니다.')}</p>
    <div class="err" id="err" hidden></div>
    <div class="field" style="margin-top:16px"><label>${T('이메일')}</label><input id="email" type="email" autocomplete="username"></div>
    <div class="field"><label>${T('비밀번호')}</label><input id="pw" type="password" autocomplete="current-password"></div>
    <button class="btn primary" id="go" style="width:100%">${T('로그인')}</button>
    <hr><div class="between"><span class="small">${T('지원자이신가요?')}</span><a class="btn sm" href="/applicant/#/login">${T('지원자 로그인')}</a></div>
  </div></div>`;
  const submit = async () => {
    try {
      await post('/api/auth/login', { email: $('#email').value, password: $('#pw').value, role: 'recruiter' });
      await loadMe(); router();
    } catch (e) { const el = $('#err'); el.textContent = e.message; el.hidden = false; }
  };
  $('#go').onclick = submit;
  $('#pw').onkeydown = (e) => { if (e.key === 'Enter') submit(); };
}

function changePwView() {
  root.innerHTML = `<div class="wrap narrow" style="padding-top:8vh"><div class="card">
    <h1>초기 비밀번호 변경</h1><p class="sub">첫 로그인입니다. 사용할 비밀번호로 바꿔 주세요.</p>
    <div class="err" id="err" hidden></div>
    <div class="field" style="margin-top:16px"><label>현재(초기) 비밀번호</label><input id="cur" type="password"></div>
    <div class="field"><label>새 비밀번호 (8자 이상)</label><input id="nw" type="password"></div>
    <button class="btn primary" id="go" style="width:100%">변경하고 시작하기</button></div></div>`;
  $('#go').onclick = async () => {
    try { await post('/api/auth/change-password', { current: $('#cur').value, new: $('#nw').value }); await loadMe(); toast('비밀번호를 변경했습니다.'); go('#/jobs'); router(); }
    catch (e) { const el = $('#err'); el.textContent = e.message; el.hidden = false; }
  };
}

/* ───────── shell ───────── */
const MENU = [['jobs', '공고 관리'], ['applicants', '지원자 목록'], ['shortlist', '다음 전형 대상자'], ['dashboard', '대시보드·통계'], ['alerts', '알림'], ['billing', '요금제']];

function shell(active) {
  const menu = me.role === 'operator' ? [...MENU, ['ops', '분석 확인 필요']] : MENU;
  root.innerHTML = `<div class="app"><aside class="side"><div class="cap">메뉴</div>
    ${menu.map(([k, l]) => `<a href="#/${k}" class="${k === active ? 'on' : ''}">${l}</a>`).join('')}</aside>
    <section class="main" id="main"></section></div>`;
  return $('#main');
}

const routes = [
  [/^\/?$/, () => go('#/jobs')],
  [/^\/jobs$/, jobsView],
  [/^\/jobs\/new$/, () => jobEdit(null)],
  [/^\/jobs\/(\d+)$/, jobEdit],
  [/^\/applicants$/, applicantsView],
  [/^\/applicants\/(\d+)$/, detailView],
  [/^\/compare$/, compareView],
  [/^\/shortlist$/, shortlistView],
  [/^\/dashboard$/, dashboardView],
  [/^\/alerts$/, alertsView],
  [/^\/ops$/, opsView],
  [/^\/billing$/, (params) => HH.billing(shell('billing'), params)],   // PRD v0.9 PAY-07~12 (회사 단위 요금제)
];

async function router() {
  if (!me) return loginView();
  if (me.must_change_password) return changePwView();
  if (!roles.length) roles = await get('/api/job-roles');
  const raw = location.hash.slice(1) || '/';
  const [path, qs] = raw.split('?');
  const params = new URLSearchParams(qs || '');
  for (const [re, fn] of routes) {
    const m = path.match(re);
    if (m) {
      window.scrollTo(0, 0);
      try { await fn(...m.slice(1), params); } catch (e) { const main = $('#main') || root; main.innerHTML = `<div class="err">${esc(e.message)}</div>`; }
      return;
    }
  }
  go('#/jobs');
}

/* ───────── 공고 관리 (JOB-01~07) ───────── */
const JOB_STATUS = { draft: ['임시저장', 'dim'], published: ['게시 중', 'on'], closed: ['마감', 'dim'] };

async function jobsView() {
  const main = shell('jobs');
  const list = await get('/api/admin/jobs');
  main.innerHTML = `<div class="between"><div><h1>공고 관리</h1><p class="sub">공고를 만들고 평가 기준을 설정합니다. 질문은 시스템이 자동으로 구성합니다.</p></div>
    <a class="btn primary" href="#/jobs/new">+ 새 공고</a></div>
    ${list.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>공고</th><th>상태</th><th>접수 기간</th><th>코딩테스트</th><th class="num">지원·응시</th><th>평가 기준</th></tr></thead><tbody>
    ${list.map((j) => `<tr class="click" data-id="${j.id}"><td><b>${esc(j.title)}</b><div class="small">${esc(j.role_name)}</div></td>
      <td><span class="pill ${JOB_STATUS[j.status][1]}">${JOB_STATUS[j.status][0]}</span></td>
      <td class="small">${fmtYMD(j.open_date)} ~ ${fmtYMD(j.close_date)}</td><td>${j.coding_enabled ? '사용' : '미사용'}</td>
      <td class="num">${j.applicants} · ${j.attempts}</td><td>${j.locked_at ? '<span class="pill">잠김</span>' : '<span class="small">변경 가능</span>'}</td></tr>`).join('')}
    </tbody></table></div>` : '<div class="empty">아직 공고가 없습니다.</div>'}`;
  $$('tr[data-id]').forEach((r) => (r.onclick = () => go(`#/jobs/${r.dataset.id}`)));
}

function jobForm(j) {
  const today = new Date().toISOString().slice(0, 10);
  const v = (k, d = '') => esc(j ? (j[k] ?? d) : d);
  return `<div class="card col">
    <div class="between"><h2>공고 내용</h2><span class="row"><button class="btn sm" id="ai-draft">① AI 공고 초안 만들기</button><button class="btn sm" id="import-job">② 기존 공고 가져오기</button></span></div>
    <p class="small">공고 내용은 기업이 정합니다. 직접 쓰거나, ① 몇 가지 답으로 AI 초안을 받거나, ② 이미 쓴 공고를 붙여 넣어 칸을 채운 뒤 검토·수정해 게시하세요.</p>
    <div class="grid2">
      <div class="field"><label>제목 *</label><input id="title" value="${v('title')}"></div>
      <div class="field"><label>직무 *</label><select id="role_code">${roles.map((r) => `<option value="${r.code}" ${r.status !== 'active' ? 'disabled' : ''} ${(j ? j.role_code : 'ai_ax') === r.code ? 'selected' : ''}>${esc(r.name)}${r.status !== 'active' ? ' (준비 중)' : ''}</option>`).join('')}</select></div>
    </div>
    <div class="field"><label>회사 소개</label><textarea id="company_intro">${v('company_intro')}</textarea></div>
    <div class="field"><label>주요 업무 *</label><textarea id="duties">${v('duties')}</textarea></div>
    <div class="field"><label>자격 요건 *</label><textarea id="requirements">${v('requirements')}</textarea></div>
    <div class="field"><label>우대 사항</label><textarea id="preferred">${v('preferred')}</textarea></div>
    <div class="grid4">
      <div class="field"><label>고용 형태</label><select id="employment_type">${['정규직', '계약직', '인턴'].map((t) => `<option ${j && j.employment_type === t ? 'selected' : ''}>${t}</option>`).join('')}</select></div>
      <div class="field"><label>근무지</label><input id="location" value="${v('location')}"></div>
      <div class="field"><label>모집 인원</label><input id="headcount" type="number" min="1" value="${v('headcount', 1)}"></div>
      <div></div>
      <div class="field"><label>접수 시작일</label><input id="open_date" type="date" value="${v('open_date', today)}"></div>
      <div class="field"><label>접수 마감일</label><input id="close_date" type="date" value="${v('close_date', today)}"></div>
    </div>
    <div id="warn"></div>
  </div>`;
}

function criteriaForm(c, locked) {
  const dis = locked ? 'disabled' : '';
  return `<div class="card col" id="crit">
    <div class="between"><h2>평가 기준</h2>${locked ? `<span class="pill">첫 응시 시작으로 잠김 · v${c.version}</span>` : `<span class="small">기준 버전 v${c.version}</span>`}</div>
    <h3>평가 항목 가중치 <span class="small">(5% 단위, 각 5% 이상, 합계 100%)</span></h3>
    ${ITEMS.map((it) => `<div class="row"><span style="width:150px">${ITEM_KO[it]}</span><input type="range" min="5" max="80" step="5" data-w="${it}" value="${c[WKEY[it]]}" ${dis} style="flex:1"><b class="neon" style="width:46px;text-align:right" data-wv="${it}">${c[WKEY[it]]}%</b></div>`).join('')}
    <div class="between"><span class="small">평가 항목과 루브릭(0~5점)은 고정입니다.</span><b id="wsum"></b></div>
    <hr style="margin:4px 0">
    <label class="check"><input type="checkbox" id="coding_enabled" ${c.coding_enabled ? 'checked' : ''} ${dis}><span><b>코딩테스트 사용</b><br><span class="small">Python 3문항(기초 2·응용 1), 60분. 문제는 문제 은행에서 자동 출제됩니다.</span></span></label>
    <div class="row"><span style="width:150px">1차 합격 기준</span><input class="input" id="pass_score" type="number" min="0" max="100" value="${c.pass_score}" ${dis} style="max-width:110px"> 점 이상 <span class="small">(AI 면접 점수만으로 판정, 코딩테스트는 아래 게이트로 별도 반영)</span></div>
    <hr style="margin:4px 0">
    <h3>코딩테스트 게이트 <span class="small">(AI 면접 점수와 섞지 않고 별도 적용)</span></h3>
    <label class="check"><input type="radio" name="coding_gate_mode" value="none" ${(c.coding_gate_mode || 'none') === 'none' ? 'checked' : ''} ${dis}><span><b>게이트 없음</b><br><span class="small">코딩테스트 결과가 합불에 영향을 주지 않습니다(코딩테스트 미사용 공고 포함).</span></span></label>
    <label class="check"><input type="radio" name="coding_gate_mode" value="hard" ${c.coding_gate_mode === 'hard' ? 'checked' : ''} ${dis}><span><b>하드</b><br><span class="small">코딩테스트 점수가 60점 미만이면 AI 면접 점수와 무관하게 불합격입니다.</span></span></label>
    <label class="check"><input type="radio" name="coding_gate_mode" value="soft" ${c.coding_gate_mode === 'soft' ? 'checked' : ''} ${dis}><span><b>소프트</b><br><span class="small">60점 미만이어도 AI 면접 점수가 85점 이상이면 합격 처리됩니다.</span></span></label>
    <hr style="margin:4px 0">
    <h3>합불 결정 방식</h3>
    <label class="check"><input type="radio" name="decision_mode" value="auto" ${(c.decision_mode || 'auto') === 'auto' ? 'checked' : ''} ${dis}><span><b>자동</b><br><span class="small">AI 판정을 바로 확정합니다. 지원자는 분석이 끝나면 바로 결과를 보고, 점수 리포트 메일도 받을 수 있습니다.</span></span></label>
    <label class="check"><input type="radio" name="decision_mode" value="hybrid" ${c.decision_mode === 'hybrid' ? 'checked' : ''} ${dis}><span><b>하이브리드</b><br><span class="small">담당자가 AI 판정을 보고 '허용' 또는 '판정 변경'을 한 번 누르면 확정됩니다. 확정과 동시에 지원자에게 합불 통보 메일이 자동 발송됩니다.</span></span></label>
    ${locked ? '<div class="note">해당 공고의 첫 응시가 시작되어 가중치·합격 기준·코딩테스트 사용 여부·게이트·합불 결정 방식을 바꿀 수 없습니다. 결과에는 적용 기준 버전이 기록됩니다.</div>'
      : '<button class="btn" id="save-crit">평가 기준 저장</button>'}
  </div>`;
}

async function jobEdit(id) {
  const main = shell('jobs');
  const j = id ? await get(`/api/admin/jobs/${id}`) : null;
  const c = j ? j.criteria : { version: 1, w_expertise: 30, w_logic: 25, w_relevance: 20, w_delivery: 10, w_fit: 15, coding_enabled: true, coding_gate_mode: 'none', pass_score: 70, decision_mode: 'auto' };
  const locked = !!(j && j.criteria.locked_at);
  main.innerHTML = `<div class="between"><div><a class="btn sm ghost" href="#/jobs">← 공고 목록</a><h1 style="margin-top:10px">${j ? esc(j.title) : '새 공고'}</h1>
    ${j ? `<span class="pill ${JOB_STATUS[j.status][1]}">${JOB_STATUS[j.status][0]}</span> <span class="small">지원자 ${j.applicants}명</span>` : ''}</div>
    <div class="row">${j && j.status !== 'closed' ? `<a class="btn" target="_blank" href="/applicant/#/jobs/${j.id}" ${j.status === 'draft' ? 'hidden' : ''}>지원자 화면 미리보기</a>` : ''}
      ${!j || j.status === 'draft' ? '<button class="btn" id="save">임시저장</button><button class="btn primary" id="publish">게시하기</button>' : ''}
      ${j && j.status === 'published' ? '<button class="btn" id="save">수정 저장</button><button class="btn" id="close">마감하기</button>' : ''}
      ${j && j.applicants === 0 ? '<button class="btn ghost" id="delete">삭제</button>' : ''}</div></div>
    <div class="grid2" style="align-items:start;grid-template-columns:minmax(0,1.3fr) minmax(0,1fr)">${jobForm(j)}${criteriaForm(c, locked)}</div>`;

  const sumW = () => ITEMS.reduce((s, it) => s + +$(`[data-w="${it}"]`).value, 0);
  const syncW = () => { ITEMS.forEach((it) => ($(`[data-wv="${it}"]`).textContent = $(`[data-w="${it}"]`).value + '%')); const s = sumW(); $('#wsum').innerHTML = `합계 <span class="${s === 100 ? 'neon' : ''}">${s}%</span>`; };
  $$('[data-w]').forEach((r) => (r.oninput = syncW));
  syncW();
  const coding = () => $$('[name="coding_gate_mode"]').forEach((r) => (r.disabled = locked || !$('#coding_enabled').checked));
  $('#coding_enabled').onchange = coding; coding();

  const body = () => Object.fromEntries(['title', 'role_code', 'company_intro', 'duties', 'requirements', 'preferred', 'employment_type', 'location', 'headcount', 'open_date', 'close_date']
    .map((k) => [k, k === 'headcount' ? +$('#' + k).value : $('#' + k).value]));
  const critBody = () => ({ ...Object.fromEntries(ITEMS.map((it) => [WKEY[it], +$(`[data-w="${it}"]`).value])),
    coding_enabled: $('#coding_enabled').checked, coding_gate_mode: ($('[name="coding_gate_mode"]:checked') || {}).value || 'none',
    pass_score: +$('#pass_score').value, decision_mode: ($('[name="decision_mode"]:checked') || {}).value || 'auto' });
  const showWarn = (w) => {
    $('#warn').innerHTML = w && w.length ? `<div class="note"><b>차별 소지 표현 확인</b> — 게시는 가능하지만 검토를 권장합니다: ${w.map((x) => `<span class="warn-u">${esc(x.text)}</span>`).join(', ')}</div>` : '';
  };
  async function saveJob() {
    if (sumW() !== 100 && !locked) throw new Error('가중치 합계를 100%로 맞춰 주세요.');
    let jid = id;
    if (!id) { const r = await post('/api/admin/jobs', body()); jid = r.id; showWarn(r.warnings); }
    else { const r = await put(`/api/admin/jobs/${id}`, body()); showWarn(r.warnings); }
    if (!locked) await put(`/api/admin/jobs/${jid}/criteria`, critBody());
    return jid;
  }
  const wrap = (fn) => async () => { try { await fn(); } catch (e) { toast(e.message, true); } };
  if ($('#save')) $('#save').onclick = wrap(async () => { const jid = await saveJob(); toast('저장했습니다.'); if (!id) go(`#/jobs/${jid}`); });
  if ($('#publish')) $('#publish').onclick = wrap(async () => {
    const jid = await saveJob();
    const okGo = await modal({ title: '공고를 게시할까요?', body: '게시하면 지원자가 볼 수 있습니다. 첫 응시가 시작되면 평가 기준이 잠깁니다.', buttons: [{ label: '취소', value: false }, { label: '게시', value: true, primary: true }] });
    if (!okGo) return go(`#/jobs/${jid}`);
    await post(`/api/admin/jobs/${jid}/publish`); toast('게시했습니다.'); id === null ? go(`#/jobs/${jid}`) : router();
  });
  if ($('#close')) $('#close').onclick = wrap(async () => {
    const okGo = await modal({ title: '공고를 마감할까요?', body: '마감하면 새 지원을 받지 않습니다. 결과는 마감 다음 날 10:00에 지원자에게 공개됩니다.', buttons: [{ label: '취소', value: false }, { label: '마감', value: true, primary: true }] });
    if (okGo) { await post(`/api/admin/jobs/${id}/close`); router(); }
  });
  if ($('#delete')) $('#delete').onclick = wrap(async () => {
    const okGo = await modal({ title: '공고를 삭제할까요?', body: '지원자가 없는 공고만 삭제할 수 있습니다.', buttons: [{ label: '취소', value: false }, { label: '삭제', value: true, primary: true }] });
    if (okGo) { await del(`/api/admin/jobs/${id}`); go('#/jobs'); }
  });
  if ($('#save-crit')) $('#save-crit').onclick = wrap(async () => {
    if (!id) throw new Error('공고를 먼저 저장해 주세요.');
    await put(`/api/admin/jobs/${id}/criteria`, critBody()); toast('평가 기준을 저장했습니다.'); router();
  });
  $('#ai-draft').onclick = () => aiDraft();
  $('#import-job').onclick = () => importJob();
}

// JOB-09 (PRD v0.8): 기존 공고 가져오기 — 원문을 칸에 옮겨 담기만 한다(내용을 지어내지 않음)
async function importJob() {
  const bg = document.createElement('div'); bg.className = 'modal-bg';
  bg.innerHTML = `<div class="modal"><h2>기존 공고 가져오기</h2><p class="small">다른 채용 사이트나 사내 문서에 이미 쓴 공고 본문을 붙여 넣거나 공고 링크를 넣으면, 제목·회사 소개·주요 업무·자격 요건·우대 사항·근무지 칸으로 나눠 채웁니다. 원문에 없는 내용은 넣지 않습니다.</p>
    <div class="field"><label>공고 본문 붙여넣기</label><textarea id="i-text" style="min-height:200px" placeholder="공고 전체를 복사해 붙여 넣으세요"></textarea></div>
    <div class="field"><label>또는 공고 링크 (본문이 비어 있을 때만 사용)</label><input id="i-url" placeholder="https://"></div>
    <div class="row" style="justify-content:flex-end"><button class="btn" id="i-cancel">취소</button><button class="btn primary" id="i-go">가져오기</button></div></div>`;
  document.body.appendChild(bg);
  $('#i-cancel').onclick = () => bg.remove();
  $('#i-go').onclick = async () => {
    $('#i-go').disabled = true; $('#i-go').innerHTML = '<span class="spinner"></span> 가져오는 중';
    try {
      const r = await post('/api/admin/jobs/import', { text: $('#i-text').value, url: $('#i-url').value });
      ['title', 'company_intro', 'duties', 'requirements', 'preferred', 'location'].forEach((k) => { if (r.fields[k] && $('#' + k)) $('#' + k).value = r.fields[k]; });
      bg.remove();
      toast(`공고 칸을 채웠습니다(${r.method === 'rule' ? '제목 줄로 분리' : '제목 줄 + AI 보조'}). ${r.warnings.join(' ')}`);
    } catch (e) { $('#i-go').disabled = false; $('#i-go').textContent = '가져오기'; toast(e.message, true); }
  };
}

async function aiDraft() {
  const bg = document.createElement('div'); bg.className = 'modal-bg';
  bg.innerHTML = `<div class="modal"><h2>AI 공고 초안</h2><p class="small">몇 가지 질문에 답하면 AI가 초안을 만듭니다. 결과는 반드시 검토·편집 후 게시하세요.</p>
    <div class="field"><label>경력 수준</label><select id="d-sen"><option>신입</option><option>경력 1~3년</option><option selected>경력 3~5년</option><option>경력 5년 이상</option></select></div>
    <div class="field"><label>주요 업무 방향</label><select id="d-focus"><option>LLM·RAG 서비스 개발</option><option>업무 자동화(AX) 에이전트</option><option>컴퓨터 비전 모델 개발</option><option>MLOps·모델 서빙</option></select></div>
    <div class="field"><label>기술 스택</label><input id="d-stack" value="Python, PyTorch, LangChain"></div>
    <div class="field"><label>근무지</label><input id="d-loc" value="서울"></div>
    <div class="field"><label>추가로 넣을 내용 (선택)</label><input id="d-extra"></div>
    <div class="row" style="justify-content:flex-end"><button class="btn" id="d-cancel">취소</button><button class="btn primary" id="d-go">초안 만들기</button></div></div>`;
  document.body.appendChild(bg);
  $('#d-cancel').onclick = () => bg.remove();
  $('#d-go').onclick = async () => {
    $('#d-go').disabled = true; $('#d-go').innerHTML = '<span class="spinner"></span> 생성 중';
    try {
      const d = await post('/api/admin/jobs/draft-ai', { seniority: $('#d-sen').value, focus: $('#d-focus').value, stack: $('#d-stack').value, location: $('#d-loc').value, extra: $('#d-extra').value });
      ['title', 'company_intro', 'duties', 'requirements', 'preferred'].forEach((k) => { if (d[k]) $('#' + k).value = d[k]; });
      if (!$('#location').value) $('#location').value = $('#d-loc').value;
      bg.remove(); toast('초안을 채웠습니다. 내용을 검토해 주세요.');
    } catch (e) { bg.remove(); toast(e.message, true); }
  };
}

/* ───────── 지원자 목록 (ADM-01) ───────── */
const RESULT_KO = { pass: '1차 합격', fail: '1차 불합격' };
const CODING_GATE_KO = { none: '게이트 없음(합불 미반영)', hard: '하드 게이트(60점 미만 무조건 불합격)', soft: '소프트 게이트(면접 85점 이상이면 보완)' };
// 코딩테스트 부분 점수(2026-11-20 사용자 지시): 문제별 통과율 70% 이상 1점 · 50% 이상 0.5점 · 그 미만 0점 → '1.5 / 3점'
const fmtPt = (p) => (Number.isInteger(+p) ? String(+p) : (+p).toFixed(1));
const codingPts = (r) => (r.coding_max ? `${fmtPt(r.coding_points)} / ${r.coding_max}점` : score(r.coding_score));
function resultPill(r, pending) {
  if (pending) return '<span class="pill neon">확인 대기</span>';
  return r ? `<span class="pill ${r === 'pass' ? 'on' : ''}">${RESULT_KO[r]}</span>` : '<span class="small">산출 전</span>';
}

async function applicantsView(params) {
  const main = shell('applicants');
  const jobs = await get('/api/admin/jobs');
  const f = { job_id: params.get('job_id') || '', result: params.get('result') || '', status: params.get('status') || '' };
  const qs = new URLSearchParams(Object.entries(f).filter(([, v]) => v));
  const rows = await get('/api/admin/applicants?' + qs);
  main.innerHTML = `<div class="between"><div><h1>지원자 목록</h1><p class="sub">1차 결과는 AI가 산출합니다. 반자동 공고는 담당자가 확인해야 확정됩니다. 2~3명을 골라 비교할 수 있습니다(분석 완료된 지원자만).</p></div>
      <div class="row"><button class="btn ghost" id="del-sel" disabled>선택 삭제</button><button class="btn ghost" id="del-all" ${rows.length ? '' : 'disabled'}>목록 전체 삭제</button><button class="btn" id="cmp" disabled>선택한 지원자 비교</button></div></div>
    <div class="row">
      <select id="f-job"><option value="">전체 공고</option>${jobs.map((j) => `<option value="${j.id}" ${String(j.id) === f.job_id ? 'selected' : ''}>${esc(j.title)}</option>`).join('')}</select>
      <select id="f-result"><option value="">전체 결과</option><option value="pass" ${f.result === 'pass' ? 'selected' : ''}>1차 합격</option><option value="fail" ${f.result === 'fail' ? 'selected' : ''}>1차 불합격</option><option value="pending" ${f.result === 'pending' ? 'selected' : ''}>확인 대기</option></select>
      <select id="f-status"><option value="">전체 응시 상태</option>${Object.entries(ATTEMPT_KO).map(([k, v]) => `<option value="${k}" ${f.status === k ? 'selected' : ''}>${v}</option>`).join('')}</select>
    </div>
    ${rows.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th><input type="checkbox" id="pick-all" aria-label="전체 선택"></th><th>지원자</th><th>공고</th><th>응시 상태</th><th>분석</th><th class="num">AI 면접</th><th class="num">코딩</th><th class="num">종합</th><th>1차 결과</th><th>장애·이탈</th></tr></thead><tbody>
    ${rows.map((r) => { const done = r.analysis_status === 'done'; return `<tr class="click" data-id="${r.application_id}">
      <td><input type="checkbox" data-pick="${r.application_id}" data-done="${done ? 1 : 0}" aria-label="선택"></td>
      <td><b>${esc(r.name)}</b><div class="small">지원 ${fmtYMD(r.applied_at)}</div></td><td class="small">${esc(r.job_title)}</td>
      <td>${r.attempt_status ? esc(ATTEMPT_KO[r.attempt_status]) : `<span class="small">${esc(HH.STEP_KO[r.step])}</span>`}</td>
      <td>${r.analysis_status ? `<span class="pill ${r.analysis_status === 'needs_check' ? 'neon' : 'dim'}">${ANALYSIS_KO[r.analysis_status]}</span>` : '-'}</td>
      <td class="num">${done ? score(r.interview_score) : '-'}</td><td class="num">${done && r.coding_enabled ? codingPts(r) : '-'}</td>
      <td class="num"><b>${done ? score(r.total_score) : '-'}</b></td><td>${done ? resultPill(r.result, r.needs_decision) : '-'}</td>
      <td class="small">${r.interrupt_count ? `장애 ${r.interrupt_count}` : ''} ${r.exit_count ? `이탈 ${r.exit_count}` : ''}</td></tr>`; }).join('')}
    </tbody></table></div>` : '<div class="empty">조건에 맞는 지원자가 없습니다.</div>'}`;
  const nav = () => go(`#/applicants?job_id=${$('#f-job').value}&result=${$('#f-result').value}&status=${$('#f-status').value}`);
  ['#f-job', '#f-result', '#f-status'].forEach((s) => ($(s).onchange = nav));
  $$('tr[data-id]').forEach((tr) => (tr.onclick = (e) => { if (e.target.dataset.pick === undefined) go(`#/applicants/${tr.dataset.id}`); }));
  const picked = () => $$('[data-pick]:checked');
  const picks = () => picked().map((c) => c.dataset.pick);
  const sync = () => {
    const p = picked(), all = $$('[data-pick]');
    $('#del-sel').disabled = !p.length;
    $('#del-sel').textContent = p.length ? `선택 삭제 (${p.length})` : '선택 삭제';
    $('#cmp').disabled = !(p.length >= 2 && p.length <= 3 && p.every((c) => c.dataset.done === '1'));
    if ($('#pick-all')) { $('#pick-all').checked = all.length > 0 && p.length === all.length; $('#pick-all').indeterminate = p.length > 0 && p.length < all.length; }
  };
  $$('[data-pick]').forEach((c) => { c.onclick = (e) => e.stopPropagation(); c.onchange = sync; });
  if ($('#pick-all')) $('#pick-all').onchange = (e) => { $$('[data-pick]').forEach((c) => (c.checked = e.target.checked)); sync(); };
  $('#cmp').onclick = () => {
    if (picked().some((c) => c.dataset.done !== '1')) return toast('비교는 분석 완료된 지원자 2~3명만 할 수 있습니다.', true);
    go(`#/compare?ids=${picks().join(',')}`);
  };
  const remove = async (ids, label) => {
    const okGo = await modal({ title: `${label} ${ids.length}명을 삭제할까요?`, body: '지원서·응시 기록·답변·평가 결과가 모두 지워지며 되돌릴 수 없습니다.', buttons: [{ label: '취소', value: false }, { label: '삭제', value: true, primary: true }] });
    if (!okGo) return;
    try { const r = await post('/api/admin/applicants/delete', { ids: ids.map(Number) }); toast(`${r.deleted}명을 삭제했습니다.`); applicantsView(params); }
    catch (err) { toast(err.message, true); }
  };
  $('#del-sel').onclick = () => remove(picks(), '선택한 지원자');
  $('#del-all').onclick = () => remove($$('[data-pick]').map((c) => c.dataset.pick), '지금 목록의 지원자');
}

/* ───────── 지원자 상세 · 리포트 (RPT-01, ADM-02, ADM-05) ───────── */
function bar(v) { return `<div class="bar"><i style="width:${Math.max(0, Math.min(100, v || 0))}%"></i></div>`; }

function resumeHtml(d, edits, file) {
  if (!d) return '<p class="small">제출한 이력서가 없습니다.</p>';
  const edited = new Set(edits || []);
  const sec = (k, fmt) => (d[k] || []).length ? `<h3 style="margin-top:12px">${SECTION_KO[k]}</h3>${d[k].map((it, i) => `<div class="item" style="margin-bottom:6px">
      <div class="between"><b>${fmt(it)}</b><span class="small">${edited.has(`${k}[${i}]`) ? '<span class="pill neon">지원자 수정</span>' : it.origin === 'auto' ? '자동 인식' : it.origin === 'manual' ? '직접 작성' : ''}</span></div>
      ${it.detail ? `<div class="sub">${esc(it.detail)}</div>` : ''}${(it.tech || []).length ? `<div class="small">기술: ${esc(it.tech.join(', '))}</div>` : ''}</div>`).join('')}` : '';
  return `${file ? `<div class="note" style="margin-bottom:10px">원본: <a href="/api/admin/files/${file.file_id}" target="_blank">${esc(file.filename)}</a> · ${file.pages.map((p) => `${p.page}쪽 ${p.method === 'ocr' ? `OCR(신뢰도 ${p.confidence}%)` : '텍스트 추출'}`).join(', ')} · ${esc(file.engine || '')}</div>` : '<div class="note" style="margin-bottom:10px">직접 작성한 이력서입니다.</div>'}
    <dl class="kv"><dt>성명</dt><dd>${esc(d.basic && d.basic.name)}</dd><dt>기술</dt><dd>${esc((d.skills || []).join(', ')) || '-'}</dd></dl>
    ${sec('education', (it) => esc([it.school, it.major, it.degree, it.period].filter(Boolean).join(' · ')))}
    ${sec('career', (it) => esc([it.company, it.role, it.period].filter(Boolean).join(' · ')))}
    ${sec('projects', (it) => esc([it.name, it.role, it.period].filter(Boolean).join(' · ')))}
    ${sec('certificates', (it) => esc([it.name, it.date].filter(Boolean).join(' · ')))}`;
}

function scoreLines(scores) {
  return scores.map((s) => `<div class="row" style="gap:8px;align-items:flex-start"><span class="pill ${s.score >= 4 ? 'neon' : 'dim'}">${ITEM_KO[s.eval_item]} ${s.score}</span>
    <span class="small" style="flex:1">${esc(s.rationale || '')}${s.quote ? `<div class="quote" style="margin-top:4px">“${esc(s.quote)}”</div>` : ''}</span></div>`).join('');
}

const EVENT_KO = { started: '응시 시작', interrupted: '장애 중단(이어가기 승인)', resumed: '이어가기 재개', exit: '화면 이탈', user_exit: '종료 조작 감지', paste_block: '붙여넣기 차단', completed: '응시 완료', terminated: '응시 종료', cancelled: '응시 취소' };

async function detailView(id) {
  const main = shell('applicants');
  const d = await get(`/api/admin/applicants/${id}`);
  const e = d.evaluation;
  const crit = e ? e.criteria_snapshot : null;
  const qs = d.questions || [];
  const base = qs.filter((q) => !q.parent_id);
  const fus = (q) => qs.filter((x) => x.parent_id === q.id);
  main.innerHTML = `<div><a class="btn sm ghost" href="#/applicants">← 지원자 목록</a></div>
    <div class="between"><div><h1>${esc(d.name)}</h1><div class="sub">${esc(d.job_title)} · ${esc(d.email)} · ${esc(d.phone || '')}</div></div>
      <div class="row">${d.attempt_status ? `<span class="pill">${esc(ATTEMPT_KO[d.attempt_status])}</span>` : ''}${d.analysis_status ? `<span class="pill dim">${ANALYSIS_KO[d.analysis_status]}</span>` : ''}</div></div>
    ${e ? `<div class="grid3">
      ${decisionCard(d, e)}
      <div class="card"><div class="small">AI 면접 점수</div><div class="big">${score(e.interview_score)}</div><div class="small">합격 기준 ${crit.pass_score}점</div></div>
      <div class="card"><div class="small">코딩테스트 점수</div><div class="big">${crit.coding_enabled ? codingPts({ ...d, coding_score: e.coding_score }) : '-'}</div><div class="small">${crit.coding_enabled ? CODING_GATE_KO[crit.coding_gate_mode] : '미사용 공고'}</div></div>
    </div>
    <div class="grid2" style="align-items:start">
      <div class="card"><h2>항목별 점수</h2>${ITEMS.map((it) => `<div class="row" style="margin:6px 0"><span style="width:140px">${ITEM_KO[it]} <span class="small">${crit[WKEY[it]]}%</span></span><div style="flex:1">${bar(e.item_scores[it])}</div><b style="width:48px;text-align:right">${score(e.item_scores[it])}</b></div>`).join('')}
        <p class="small" style="margin-top:8px">적용 기준 v${crit.version} · 모델 ${esc(e.versions.model)} · 프롬프트 ${esc(e.versions.prompt)} · 루브릭 ${esc(e.versions.rubric)} · OCR ${esc(e.versions.ocr)}</p></div>
      <div class="card"><h2>총평</h2><p>${esc(e.summary)}</p>
        <div class="grid2"><div><h3>강점</h3>${(e.strengths || []).map((s, i) => `<p class="small"><b class="neon">${i + 1}.</b> ${esc(s)}</p>`).join('') || '<p class="small">-</p>'}</div>
        <div><h3>개선점</h3>${(e.improvements || []).map((s, i) => `<p class="small"><b>${i + 1}.</b> ${esc(s)}</p>`).join('') || '<p class="small">-</p>'}</div></div></div>
    </div>` : `<div class="note">${d.attempt_id ? '분석이 끝나면 점수와 근거가 표시됩니다.' : '아직 응시하지 않았습니다.'}</div>`}
    ${base.length ? `<div class="card col"><h2>질문별 답변과 평가</h2>
      ${base.map((q) => `<div class="qa"><div class="between"><b>Q${q.seq}. ${esc(q.text)}</b><span class="row"><span class="pill dim">${CAT_KO[q.category]}</span>${q.gen_method === 'template' ? '<span class="pill dim">템플릿 질문</span>' : ''}</span></div>
        ${q.resume_ref && q.resume_ref.label ? `<div class="small">질문 근거: 이력서 ${SECTION_KO[q.resume_ref.section] || ''} '${esc(q.resume_ref.label)}'</div>` : ''}
        ${q.resume_ref && q.resume_ref.fallback_reason ? `<div class="small">대체 사유: ${esc(q.resume_ref.fallback_reason)}</div>` : ''}
        <div class="sub">${q.answer_text ? esc(q.answer_text) : '<i>미응답</i>'}</div>
        ${fus(q).map((f) => `<div class="fu"><b class="small neon">꼬리질문${q.prescore && q.prescore.followup_reason ? ` (${esc(q.prescore.followup_reason)})` : ''}</b><div><b>${esc(f.text)}</b></div><div class="sub">${f.answer_text ? esc(f.answer_text) : '<i>미응답</i>'}</div></div>`).join('')}
        ${scoreLines(q.scores || [])}</div>`).join('')}</div>` : ''}
    ${(d.coding || []).length ? `<div class="card col"><h2>코딩테스트</h2>${d.coding.map((c) => `<div><div class="between"><b>${esc(c.title)} <span class="small">(${c.level === 'basic' ? '기초' : '응용'})</span></b><span class="small">통과 ${c.passed ?? '-'} / ${c.total ?? '-'}${c.points != null ? ` · ${fmtPt(c.points)}점` : ''}${c.paste_blocked ? ` · 붙여넣기 차단 ${c.paste_blocked}회` : ''}</span></div><pre class="out">${esc(c.code)}</pre></div>`).join('')}
      ${e && e.code_feedback ? `<div class="note"><b>AI 코드 피드백</b> (점수 미반영) — ${esc(e.code_feedback)}</div>` : ''}</div>` : ''}
    <div class="grid2" style="align-items:start">
      <div class="card"><h2>이력서</h2>${resumeHtml(d.resume, d.resume_edits, d.resume_file)}</div>
      <div class="col">
        <div class="card"><h2>장애·이탈 기록</h2>${(d.events || []).length ? d.events.map((ev) => `<div class="between small" style="padding:4px 0;border-bottom:1px solid var(--line)"><span>${esc(EVENT_KO[ev.type] || ev.type)}${ev.detail && ev.detail.kind ? ` · ${esc(ev.detail.kind)}` : ''}</span><span>${fmtDateTime(ev.created_at)}</span></div>`).join('') : '<p class="small">기록이 없습니다.</p>'}
          <p class="small" style="margin-top:8px">장애·이탈 기록은 점수에 반영되지 않습니다.</p></div>
        <div class="card"><h2>내부 메모</h2><p class="small">같은 기업 담당자만 볼 수 있고 지원자에게 공개되지 않습니다.</p>
          <textarea class="input" id="memo" placeholder="메모 입력"></textarea><button class="btn sm" id="add-memo" style="margin-top:8px">메모 추가</button>
          ${(d.memos || []).map((m) => `<div style="padding:8px 0;border-bottom:1px solid var(--line)"><div class="small">${esc(m.author)} · ${fmtDateTime(m.created_at)}</div>${esc(m.body)}</div>`).join('')}</div>
      </div></div>`;
  if (e) bindDecision(id, e);
  $('#add-memo').onclick = async () => { try { await post(`/api/admin/applicants/${id}/memos`, { body: $('#memo').value }); router(); } catch (err) { toast(err.message, true); } };
}

/* 합불 확정 카드: 자동 = AI 판정 그대로 확정 표시, 하이브리드 = 확인 대기면 확정/변경 버튼 */
function decisionCard(d, e) {
  const fin = e.final_result;
  const hybrid = d.decision_mode === 'hybrid';
  const changed = fin && fin !== e.result;
  const head = fin ? (hybrid ? (changed ? '1차 결과 (담당자가 판정 변경)' : '1차 결과 (담당자 허용)') : '1차 결과 (AI 자동 확정)') : '1차 결과 · 담당자 확인 대기';
  const shown = fin || e.result;
  return `<div class="card ${shown === 'pass' ? 'hl' : ''}"><div class="small">${head}</div>
    <div class="big" style="${shown === 'pass' ? '' : 'color:var(--white)'}">${fin ? '' : 'AI '}${RESULT_KO[shown]}</div>
    <div class="small">${esc(e.reason || '')}</div>
    ${changed ? `<div class="small" style="margin-top:6px">AI 판정 ${RESULT_KO[e.result]} → 담당자 ${RESULT_KO[fin]}</div>` : ''}
    ${fin && hybrid && e.decided_at ? `<div class="small">확정 ${fmtDateTime(e.decided_at)} · 합불 통보 메일 발송됨</div>` : ''}
    ${!fin && hybrid ? `<div class="row" style="margin-top:10px"><button class="btn sm primary" id="dec-ok">허용</button><button class="btn sm" id="dec-change">판정 변경 (${RESULT_KO[e.result === 'pass' ? 'fail' : 'pass']}으로)</button></div>` : ''}</div>`;
}

function bindDecision(id, e) {
  if (!$('#dec-ok')) return;
  // 반자동: 원클릭. 허용 = AI 판정 그대로 확정, 판정 변경 = 반대 판정으로 확정 (점수는 그대로)
  const send = (action, btn) => async () => {
    $$('#dec-ok, #dec-change').forEach((b) => (b.disabled = true));
    try { const r = await post(`/api/admin/applicants/${id}/decision`, { action }); toast(`${RESULT_KO[r.result]}(으)로 확정했습니다.`); router(); }
    catch (err) { toast(err.message, true); $$('#dec-ok, #dec-change').forEach((b) => (b.disabled = false)); }
  };
  $('#dec-ok').onclick = send('confirm');
  $('#dec-change').onclick = send('override');
}

/* ───────── 비교 모드 (ADM-03) ───────── */
async function compareView(params) {
  const main = shell('applicants');
  const rows = await get(`/api/admin/compare?ids=${params.get('ids') || ''}`);
  main.innerHTML = `<div><a class="btn sm ghost" href="#/applicants">← 지원자 목록</a></div><h1>지원자 비교</h1><p class="sub">참고용 비교이며 결과는 바뀌지 않습니다.</p>
    <div class="grid${rows.length}" style="align-items:start">${rows.map((r) => `<div class="card col"><h2>${esc(r.name)}</h2><div class="small">${esc(r.job_title)}</div>
      <div class="between"><span>종합 총점</span><b class="neon">${score(r.total_score)}</b></div>${resultPill(r.result, r.needs_decision)}
      <dl class="kv"><dt>AI 면접</dt><dd>${score(r.interview_score)}</dd><dt>코딩테스트</dt><dd>${r.coding_enabled ? codingPts(r) : '-'}</dd></dl>
      ${r.item_scores ? ITEMS.map((it) => `<div><div class="between small"><span>${ITEM_KO[it]}</span><span>${score(r.item_scores[it])}</span></div>${bar(r.item_scores[it])}</div>`).join('') : ''}
      <h3>강점</h3>${(r.strengths || []).map((s) => `<p class="small">· ${esc(s)}</p>`).join('')}
      <h3>개선점</h3>${(r.improvements || []).map((s) => `<p class="small">· ${esc(s)}</p>`).join('')}</div>`).join('')}</div>`;
}

/* ───────── 다음 전형 대상자 (ADM-06) ───────── */
async function shortlistView(params) {
  const main = shell('shortlist');
  const jobs = await get('/api/admin/jobs');
  const jid = params.get('job_id') || '';
  const rows = await get(`/api/admin/shortlist${jid ? `?job_id=${jid}` : ''}`);
  main.innerHTML = `<h1>다음 전형 대상자</h1><p class="sub">1차 합격자 목록입니다. 응시 데이터는 결과 공개일로부터 7일 후 삭제되므로 그 안에 확인해 주세요. 2차 일정 관리·통보 기능은 없습니다.</p>
    <div class="row"><select id="f-job"><option value="">전체 공고</option>${jobs.map((j) => `<option value="${j.id}" ${String(j.id) === jid ? 'selected' : ''}>${esc(j.title)}</option>`).join('')}</select></div>
    ${rows.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>이름</th><th>공고</th><th class="num">종합 총점</th><th>이메일</th><th>연락처</th><th>삭제 예정</th></tr></thead><tbody>
    ${rows.map((r) => `<tr class="click" data-id="${r.application_id}"><td><b>${esc(r.name)}</b></td><td class="small">${esc(r.job_title)}</td><td class="num neon">${score(r.total_score)}</td><td>${esc(r.email)}</td><td>${esc(r.phone || '')}</td><td class="small">${fmtYMD(r.delete_at)}</td></tr>`).join('')}
    </tbody></table></div>` : '<div class="empty">1차 합격자가 없습니다.</div>'}`;
  $('#f-job').onchange = () => go(`#/shortlist?job_id=${$('#f-job').value}`);
  $$('tr[data-id]').forEach((tr) => (tr.onclick = () => go(`#/applicants/${tr.dataset.id}`)));
}

/* ───────── 대시보드·통계 (ADM-07) ───────── */
async function dashboardView() {
  const main = shell('dashboard');
  const s = await get('/api/admin/stats');
  const total = s.monthly.reduce((a, m) => a + m.n, 0);
  const maxM = Math.max(1, ...s.monthly.map((m) => m.n));
  main.innerHTML = `<h1>대시보드·통계</h1><p class="sub">개인 식별 정보 없이 집계한 값입니다.</p>
    <div class="grid4">
      <div class="card"><div class="small">누적 응시</div><div class="big">${total}</div></div>
      <div class="card"><div class="small">장애 이어가기 발생</div><div class="big">${s.counts.interrupted}</div></div>
      <div class="card"><div class="small">부정행위 취소</div><div class="big">${s.counts.cancelled}</div></div>
      <div class="card"><div class="small">이력서 OCR 처리 · 인식 실패</div><div class="big">${s.counts.ocr_used}<span class="small"> 건 · 실패 ${s.counts.ocr_failed}</span></div></div>
    </div>
    <div class="grid2" style="align-items:start">
      <div class="card"><h2>월별 응시자 수</h2>${s.monthly.length ? s.monthly.map((m) => `<div class="row" style="margin:6px 0"><span style="width:80px">${m.month}</span><div style="flex:1">${bar(m.n / maxM * 100)}</div><b>${m.n}</b></div>`).join('') : '<p class="small">데이터가 없습니다.</p>'}</div>
      <div class="card"><h2>공고별 1차 합격률 · 평균 점수</h2><table class="tbl"><thead><tr><th>공고</th><th class="num">평가 완료</th><th class="num">합격률</th><th class="num">평균 총점</th></tr></thead><tbody>
        ${s.per_job.map((j) => `<tr><td class="small">${esc(j.title)}</td><td class="num">${j.evaluated}</td><td class="num">${j.evaluated ? Math.round(j.passed / j.evaluated * 100) + '%' : '-'}</td><td class="num">${score(j.avg_total)}</td></tr>`).join('')}</tbody></table></div>
    </div>`;
}

async function alertsView() {
  const main = shell('alerts');
  const list = await get('/api/admin/notifications');
  main.innerHTML = `<h1>알림</h1>${list.length ? `<div class="card">${list.map((n) => `<div style="padding:10px 0;border-bottom:1px solid var(--line)"><div class="between"><b>${esc(n.title)}</b><span class="small">${fmtDateTime(n.created_at)}</span></div><div class="small">${esc(n.body)}</div></div>`).join('')}</div>` : '<div class="empty">알림이 없습니다.</div>'}`;
}

/* ───────── 운영: 분석 확인 필요 (EVL-08) ───────── */
async function opsView() {
  const main = shell('ops');
  const list = await get('/api/ops/analysis');
  main.innerHTML = `<h1>분석 확인 필요</h1><p class="sub">자동 재시도 후에도 실패한 분석 작업입니다. 재처리하면 같은 응시 자료로 결과를 다시 만듭니다.</p>
    ${list.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>응시 ID</th><th>상태</th><th class="num">시도</th><th>마지막 오류</th><th></th></tr></thead><tbody>
    ${list.map((j) => `<tr><td>#${j.attempt_id}</td><td>${esc(j.status)}</td><td class="num">${j.tries}</td><td class="small">${esc(j.last_error || '')}</td><td><button class="btn sm" data-retry="${j.attempt_id}">재처리</button></td></tr>`).join('')}</tbody></table></div>` : '<div class="empty">처리 대기 중인 작업이 없습니다.</div>'}`;
  $$('[data-retry]').forEach((b) => (b.onclick = async () => { await post(`/api/ops/analysis/${b.dataset.retry}/retry`); toast('재처리를 등록했습니다.'); router(); }));
}

window.addEventListener('hashchange', router);
(async () => { await HH.devbar(); await loadMe(); router(); })();
