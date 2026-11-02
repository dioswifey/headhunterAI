/* headhunterAI 공통 첫 페이지 — 해시 라우팅(#/ · #/features · #/about · #/pricing) + KO/EN 문구 전환.
   메뉴 이름(Features · About Us · Pricing)은 두 언어 모두 영어로 두고, 안의 내용만 바꾼다. */
const { esc } = HH;
const view = document.getElementById('view');

const T = {
  ko: {
    heroTitle: '인재를 발견하는 새로운 기준,',
    heroLead: '이력서를 올리면 AI가 내용을 읽고 나에게 맞춘 질문으로 1차 면접을 진행합니다. 기업은 같은 기준과 근거로 1차 결과를 받아 봅니다.',
    startApplicant: '지원자로 시작하기 →',
    loginCompany: '기업 담당자 로그인',
    soon: '준비 중',
    featuresTitle: ['이력서부터 1차 면접까지,', 'AI가 한 흐름으로'],
    featuresLead: '지원자는 내 이력서에 맞춘 면접을, 기업은 같은 기준으로 정리된 1차 결과를 받습니다.',
    ddCta: ['headhunterAI 시작하기', '공고를 둘러보고 바로 지원할 수 있습니다 →'],
    features: [
      { id: 'interview', ic: 'Q', name: 'AI 1차 면접', short: '이력서와 공고에 맞춘 질문 10개와 꼬리질문',
        body: '인성 2 · 이력서 기반 2 · 직무 4 · 기업 2, 모두 10문항으로 진행합니다. 답변에 따라 꼬리질문이 질문당 1개, 전체 4개까지 이어집니다.',
        points: ['응시는 지원 건당 1회', '연결이 끊기면 이어서 진행', '시작 전에 마이크·스피커·카메라 점검'] },
      { id: 'resume', ic: 'R', name: '이력서 자동 인식', short: 'PDF·스캔본도 읽고, 확인한 내용만 사용',
        body: '텍스트 PDF는 그대로, 스캔본·사진은 OCR로 읽어 경력·프로젝트·학력을 정리합니다. 지원자가 직접 확인하고 고친 내용만 질문과 평가에 쓰입니다.',
        points: ['원문과 대조해 없는 내용은 제외', '인식이 어려우면 직접 작성으로 전환'] },
      { id: 'report', ic: 'E', name: 'AI 평가 리포트', short: '항목별 점수와 답변 속 근거 문장',
        body: 'AI는 항목별 0~5점과 답변에서 그대로 인용한 근거만 냅니다. 점수 합산과 합격·불합격 판정은 정해진 규칙으로 계산합니다.',
        points: ['답변에 없는 인용은 인정하지 않음', '지원자에게는 합불·강점·개선점 공개', '담당자도 결과를 임의로 수정할 수 없음'] },
      { id: 'coding', ic: '{}', name: '맞춤 코딩테스트', short: '기업 섹터에 맞춰 문제 유형과 언어 선택', soon: true,
        body: '기업은 공고마다 출제 유형(알고리즘·그리디·DP, SQL, 저지연 C 등)과 언어, 난이도를 고르고, 문제는 운영 측 문제 은행에서 나옵니다. 지금은 Python 기본 코딩테스트를 제공합니다.',
        points: ['외부와 차단된 실행 환경에서 채점', '테스트케이스 통과율로 점수 계산'] },
      { id: 'practice', ic: 'P', name: '모의면접 · 코딩 연습', short: 'IT 직군 모의면접과 언어별 코딩 연습', soon: true,
        body: '공고 없이 직무를 골라 실전과 같은 방식으로 모의면접을 보고, Python·C·SQL 중 언어를 골라 코딩 문제를 연습합니다.',
        points: ['직무: AI/AX 엔지니어 · 백엔드 엔지니어 · 데이터 분석가', '연습 결과는 나만 보고 기업에 전달되지 않음'] },
    ],
    aboutTitle: ['면접의 첫 단계를', '더 투명하게'],
    aboutLead: 'headhunterAI는 1차 면접을 AI로 진행하되, 판단 과정을 지원자와 기업이 모두 이해할 수 있게 만드는 서비스입니다.',
    aboutWhyH: '만든 이유',
    aboutWhy: '1차 면접은 지원자가 많을수록 기준이 흔들리기 쉽고, 지원자는 왜 떨어졌는지 알기 어렵습니다. 같은 구조의 질문, 답변에 근거한 평가, 정해진 규칙에 따른 판정으로 이 단계를 정리하고자 합니다.',
    aboutPrinciplesH: '일하는 방식',
    aboutPrinciples: [
      { ic: '1', h: 'AI는 점수와 근거만', p: 'AI는 항목별 점수와 답변 인용을 내고, 합산·합불·상태 변경은 코드의 규칙이 처리합니다.' },
      { ic: '2', h: '확인한 이력서만', p: '이력서는 지원자가 직접 확인한 내용만 질문과 평가에 씁니다. 나이·가족·학벌 같은 질문은 차단합니다.' },
      { ic: '3', h: '결과는 같은 시각에', p: '결과는 마감 다음 날 오전 10시에 함께 공개합니다. 지원자에게는 합불과 강점·개선점을 알려 드립니다.' },
    ],
    aboutTechH: '기술 구성',
    aboutTech: [
      { ic: 'L', h: '자체 서버의 언어 모델', p: '면접 질문과 평가는 서비스 서버에서 직접 운영하는 오픈 모델(Qwen3)로 처리하며, 외부 AI API로 이력서·답변을 보내지 않습니다.' },
      { ic: 'O', h: '이력서 인식', p: 'PDF 텍스트 추출과 Tesseract OCR(한국어·영어)을 함께 씁니다.' },
      { ic: 'S', h: '격리된 코드 실행', p: '코딩테스트 코드는 네트워크가 차단된 별도 컨테이너에서 시간·메모리 제한을 두고 실행합니다.' },
    ],
    pricingTitle: ['Simple pricing.', '필요한 만큼만'],
    pricingLead: '주 10회 미만은 무료로 쓰고, 더 자주 쓰거나 IT 직군 모의면접·맞춤 코딩테스트가 필요하면 Advanced를 고르세요.',
    segApplicant: '지원자', segCompany: '기업',
    perMonth: '/월', free: '무료',
    plans: {
      applicant: [
        { name: 'Basic', tag: 'FREE', desc: '공고에 지원해 AI 1차 면접을 봅니다.', price: 0, sub: '주 10회 미만 이용',
          items: ['공고 지원 · AI 1차 면접', '이력서 자동 인식', '결과 공개(합불 · 강점 · 개선점)'], cta: '무료로 시작하기', href: '/applicant/#/jobs' },
        { name: 'Advanced', tag: 'RECOMMENDED', rec: true, desc: '실전 전에 IT 직군 모의면접과 코딩 연습을 충분히 합니다.', price: 9900, sub: '주 10회 이상 이용',
          items: ['Basic의 모든 기능', 'IT 직군 모의면접 (AI/AX · 백엔드 · 데이터 분석)', '언어 선택 코딩 연습 (Python · C · SQL)', '연습 기록 모아 보기'] },
      ],
      company: [
        { name: 'Basic', tag: 'FREE', desc: '공고를 올리고 AI 1차 면접 결과를 받아 봅니다.', price: 0, sub: '주 지원자 면접 10건 미만',
          items: ['공고 작성 · 게시 · 마감', 'AI 1차 면접 · 평가 리포트', 'Python 기본 코딩테스트'], cta: '기업 담당자 로그인', href: '/admin/' },
        { name: 'Advanced', tag: 'RECOMMENDED', rec: true, desc: '채용 규모가 크거나 섹터에 맞는 코딩테스트가 필요한 기업용입니다.', price: 89900, sub: '주 지원자 면접 10건 이상',
          items: ['Basic의 모든 기능', '맞춤 코딩테스트 (유형 · 언어 · 난이도 선택)', '알고리즘 · SQL · 저지연 C 등 섹터별 유형', 'IT 직군 확장 (백엔드 · 데이터 분석)'] },
      ],
    },
    payReady: '결제 준비 중',
    pricingNote: 'Advanced 결제(네이버페이 · 토스페이먼츠)는 준비 중입니다. 표시 가격은 출시 전 바뀔 수 있습니다.',
  },
  en: {
    heroTitle: 'A new standard for discovering talent,',
    heroLead: 'Upload your résumé and the AI reads it, then runs a first-round interview with questions made for you. Companies get first-round results built on the same criteria and evidence.',
    startApplicant: 'Start as an applicant →',
    loginCompany: 'Recruiter sign-in',
    soon: 'Coming soon',
    featuresTitle: ['From résumé to first interview,', 'in one AI flow'],
    featuresLead: 'Applicants get an interview fitted to their résumé. Companies get first-round results organised under one standard.',
    ddCta: ['Get started', 'Browse open roles and apply right away →'],
    features: [
      { id: 'interview', ic: 'Q', name: 'AI first-round interview', short: '10 questions fitted to your résumé and the role, plus follow-ups',
        body: 'Ten questions: 2 personality · 2 résumé-based · 4 role · 2 company. Follow-up questions are added from your answers, up to 1 per question and 4 in total.',
        points: ['One attempt per application', 'Resume where you left off after a disconnect', 'Mic, speaker and camera check before you start'] },
      { id: 'resume', ic: 'R', name: 'Résumé recognition', short: 'Reads PDFs and scans; only what you confirm is used',
        body: 'Text PDFs are read directly and scans or photos through OCR, then organised into experience, projects and education. Only the content you review and confirm is used for questions and evaluation.',
        points: ['Anything not found in the original is dropped', 'Falls back to manual entry when recognition fails'] },
      { id: 'report', ic: 'E', name: 'AI evaluation report', short: 'Per-item scores with quoted evidence from answers',
        body: 'The AI only gives 0–5 scores per item and quotes taken verbatim from the answer. Totals and pass/fail are calculated by fixed rules.',
        points: ['Quotes not found in the answer are rejected', 'Applicants see pass/fail, strengths and areas to improve', 'Recruiters cannot edit results'] },
      { id: 'coding', ic: '{}', name: 'Custom coding test', short: 'Pick problem types and languages for your sector', soon: true,
        body: 'For each job post, companies choose problem types (algorithms such as greedy and DP, SQL, low-latency C and more), language and difficulty; problems come from our curated bank. Today a basic Python coding test is available.',
        points: ['Graded in a network-isolated sandbox', 'Score = share of test cases passed'] },
      { id: 'practice', ic: 'P', name: 'Mock interviews & coding practice', short: 'IT mock interviews and coding practice by language', soon: true,
        body: 'Pick a role and take a mock interview that works like the real one, or practise coding problems in Python, C or SQL — no job post needed.',
        points: ['Roles: AI/AX engineer · Backend engineer · Data analyst', 'Practice results are private and never shared with companies'] },
    ],
    aboutTitle: ['Making the first interview', 'more transparent'],
    aboutLead: 'headhunterAI runs first-round interviews with AI while keeping the reasoning understandable for both applicants and companies.',
    aboutWhyH: 'Why we built it',
    aboutWhy: 'With many applicants, first-round standards drift, and applicants rarely learn why they were turned down. We structure this step with the same question format, evaluation grounded in answers, and decisions made by fixed rules.',
    aboutPrinciplesH: 'How we work',
    aboutPrinciples: [
      { ic: '1', h: 'AI scores, rules decide', p: 'The AI provides per-item scores and quotes; totals, pass/fail and status changes are handled by rules in code.' },
      { ic: '2', h: 'Only confirmed résumés', p: 'Only résumé content the applicant has confirmed is used. Questions about age, family or school background are blocked.' },
      { ic: '3', h: 'Results at the same time', p: 'Results are released together at 10:00 the day after the deadline, with pass/fail, strengths and areas to improve.' },
    ],
    aboutTechH: 'Under the hood',
    aboutTech: [
      { ic: 'L', h: 'Self-hosted language model', p: 'Questions and evaluation run on an open model (Qwen3) operated on our own server; résumés and answers are not sent to external AI APIs.' },
      { ic: 'O', h: 'Résumé recognition', p: 'PDF text extraction combined with Tesseract OCR (Korean and English).' },
      { ic: 'S', h: 'Isolated code execution', p: 'Coding-test code runs in a separate network-blocked container with time and memory limits.' },
    ],
    pricingTitle: ['Simple pricing.', 'Pay for what you use'],
    pricingLead: 'Free for fewer than 10 uses a week. Choose Advanced if you use it more often or need IT mock interviews and custom coding tests.',
    segApplicant: 'Applicants', segCompany: 'Companies',
    perMonth: '/mo', free: 'Free',
    plans: {
      applicant: [
        { name: 'Basic', tag: 'FREE', desc: 'Apply to job posts and take the AI first-round interview.', price: 0, sub: 'Fewer than 10 uses a week',
          items: ['Apply · AI first-round interview', 'Résumé recognition', 'Results (pass/fail · strengths · improvements)'], cta: 'Start for free', href: '/applicant/#/jobs' },
        { name: 'Advanced', tag: 'RECOMMENDED', rec: true, desc: 'Practise IT mock interviews and coding before the real thing.', price: 9900, sub: '10 or more uses a week',
          items: ['Everything in Basic', 'IT mock interviews (AI/AX · Backend · Data analysis)', 'Coding practice by language (Python · C · SQL)', 'Practice history in one place'] },
      ],
      company: [
        { name: 'Basic', tag: 'FREE', desc: 'Post jobs and receive AI first-round results.', price: 0, sub: 'Fewer than 10 interviews a week',
          items: ['Create · publish · close job posts', 'AI first-round interview · evaluation report', 'Basic Python coding test'], cta: 'Recruiter sign-in', href: '/admin/' },
        { name: 'Advanced', tag: 'RECOMMENDED', rec: true, desc: 'For larger hiring volumes or sector-specific coding tests.', price: 89900, sub: '10 or more interviews a week',
          items: ['Everything in Basic', 'Custom coding test (type · language · difficulty)', 'Sector types: algorithms · SQL · low-latency C', 'More IT roles (Backend · Data analysis)'] },
      ],
    },
    payReady: 'Payments coming soon',
    pricingNote: 'Advanced payments (Naver Pay · Toss Payments) are coming soon. Prices may change before launch.',
  },
};

let lang = 'ko';
try { if (localStorage.getItem('hh.lang') === 'en') lang = 'en'; } catch (e) { /* 저장소를 못 쓰면 한국어 기본 */ }
let audience = 'applicant';
const t = () => T[lang];

function won(n) {
  if (!n) return t().free;
  return lang === 'en' ? `₩${n.toLocaleString('en-US')}` : `${n.toLocaleString('ko-KR')}원`;
}

function setLang(l) {
  lang = l;
  try { localStorage.setItem('hh.lang', l); } catch (e) { /* 무시 */ }
  render();
}

function dropdown() {
  const L = t();
  document.getElementById('dd-features').innerHTML = L.features.map(f => `
    <a class="dd-item" href="#/features/${f.id}"><span class="ic">${esc(f.ic)}</span>
      <div><b>${esc(f.name)}${f.soon ? `<em class="soon">${esc(L.soon)}</em>` : ''}</b><span>${esc(f.short)}</span></div></a>`).join('') + `
    <a class="dd-item" href="/applicant/#/jobs"><span class="ic">→</span>
      <div><b>${esc(L.ddCta[0])}</b><span>${esc(L.ddCta[1])}</span></div></a>`;
}

function home() {
  const L = t();
  return `
  <div class="wrap">
    <section class="hero">
      <div>
        <h1>${esc(L.heroTitle)}<br><span class="neon">headhunterAI</span></h1>
        <p class="lead">${esc(L.heroLead)}</p>
        <div class="row">
          <a class="btn primary lg" href="/applicant/#/jobs">${esc(L.startApplicant)}</a>
          <a class="btn lg" href="/admin/">${esc(L.loginCompany)}</a>
        </div>
      </div>
      <img class="hero-img" src="/images/mainpage_image.png.png" alt="">
    </section>
  </div>`;
}

function head(title, lead) {
  return `<section class="page-head"><h1>${esc(title[0])}<br><span class="neon">${esc(title[1])}</span></h1><p>${esc(lead)}</p></section>`;
}

function cards(list) {
  return `<div class="cards">${list.map(c => `
    <div class="card2"><div class="ic">${esc(c.ic)}</div><h3>${esc(c.h)}</h3><p>${esc(c.p)}</p></div>`).join('')}</div>`;
}

function features() {
  const L = t();
  return `<div class="wrap">${head(L.featuresTitle, L.featuresLead)}
    <div class="cards">${L.features.map(f => `
      <div class="card2 feature" id="f-${f.id}"><div class="ic">${esc(f.ic)}</div>
        <h3>${esc(f.name)}${f.soon ? `<em class="soon">${esc(L.soon)}</em>` : ''}</h3>
        <p>${esc(f.body)}</p><ul>${f.points.map(p => `<li>${esc(p)}</li>`).join('')}</ul></div>`).join('')}
    </div></div>`;
}

function about() {
  const L = t();
  return `<div class="wrap">${head(L.aboutTitle, L.aboutLead)}
    <section class="sec"><h2>${esc(L.aboutWhyH)}</h2><p style="max-width:760px;color:var(--muted)">${esc(L.aboutWhy)}</p></section>
    <section class="sec"><h2>${esc(L.aboutPrinciplesH)}</h2>${cards(L.aboutPrinciples)}</section>
    <section class="sec"><h2>${esc(L.aboutTechH)}</h2>${cards(L.aboutTech)}</section>
  </div>`;
}

function pricing() {
  const L = t();
  const plans = L.plans[audience];
  return `<div class="wrap">
    <section class="page-head"><h1>${esc(L.pricingTitle[0])}<br><span class="neon">${esc(L.pricingTitle[1])}</span></h1><p>${esc(L.pricingLead)}</p>
      <div class="seg"><button type="button" data-aud="applicant" class="${audience === 'applicant' ? 'on' : ''}">${esc(L.segApplicant)}</button><button type="button" data-aud="company" class="${audience === 'company' ? 'on' : ''}">${esc(L.segCompany)}</button></div>
    </section>
    <div class="plans">${plans.map(p => `
      <div class="plan${p.rec ? ' rec' : ''}">
        <div class="ph"><h3>${esc(p.name)}</h3><span class="tag">${esc(p.tag)}</span></div>
        <p class="desc">${esc(p.desc)}</p>
        <div class="price">${esc(won(p.price))}${p.price ? `<small> ${esc(L.perMonth)}</small>` : ''}</div>
        <div class="sub">${esc(p.sub)}</div>
        <ul>${p.items.map(i => `<li>${esc(i)}</li>`).join('')}</ul>
        ${p.href ? `<a class="btn lg" href="${p.href}">${esc(p.cta)}</a>` : `<button class="btn primary lg" type="button" disabled style="opacity:.55;cursor:not-allowed">${esc(L.payReady)}</button>`}
      </div>`).join('')}
    </div>
    <p class="note">${esc(L.pricingNote)}</p>
  </div>`;
}

const ROUTES = { '': home, features, about, pricing };

function render() {
  document.documentElement.lang = lang;
  document.querySelectorAll('.lang button').forEach(b => b.classList.toggle('on', b.dataset.lang === lang));
  dropdown();
  const [page, sub] = location.hash.replace(/^#\/?/, '').split('/');
  const fn = ROUTES[page] || home;
  document.querySelectorAll('[data-nav]').forEach(a => a.classList.toggle('on', a.dataset.nav === page));
  view.innerHTML = fn();
  // 언어·대상 전환은 같은 위치를 유지하고, 메뉴 이동 때만 스크롤을 옮긴다
  if (location.hash === lastHash) return;
  lastHash = location.hash;
  if (page === 'features' && sub) document.getElementById('f-' + sub)?.scrollIntoView({ behavior: 'smooth' });
  else window.scrollTo(0, 0);
}
let lastHash = null;

document.querySelector('.lang').addEventListener('click', e => { const b = e.target.closest('[data-lang]'); if (b) setLang(b.dataset.lang); });
view.addEventListener('click', e => { const b = e.target.closest('[data-aud]'); if (b) { audience = b.dataset.aud; render(); } });
window.addEventListener('hashchange', render);
render();
