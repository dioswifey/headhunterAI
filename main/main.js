/* headhunterAI 공통 첫 페이지 — 해시 라우팅(#/ · #/features/<서비스> · #/about · #/pricing) + KO/EN 문구 전환.
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
    // Features 드롭다운의 4개 서비스 페이지 (#/features/<id>). 드롭다운 라벨(한·영)은 언어 전환과 관계없이 SERVICE_LABELS를 쓴다
    services: {
      'coding-practice': {
        title: ['무제한 코딩 연습', '원하는 만큼, 원하는 언어로'],
        lead: '공고가 없어도 IT 직군 코딩 문제를 반복해서 풀어 볼 수 있습니다. 언어를 고르고, 바로 실행해 보고, 실제 코딩테스트와 같은 방식으로 채점받습니다.',
        status: '지금은 알고리즘 문제를 Python으로 로그인 후 횟수 제한 없이 풀 수 있습니다(공개 예제 실행 · 숨은 테스트까지 채점, Junior·Senior 레벨). SQLD 유형 SQL 문제(제1·2과목 4지선다)도 풀 수 있습니다. C언어는 준비 중이며, 내용은 출시 전 바뀔 수 있습니다.',
        sections: [
          { h: '할 수 있는 것', cards: [
            { h: '언어 선택', p: 'Python · C · SQL 중에서 골라 같은 유형의 문제를 언어별로 연습합니다.' },
            { h: '유형별 문제', p: '알고리즘(그리디·DP 등), SQL 조회·집계, 저지연 C처럼 실제 기업이 고르는 유형으로 나뉜 문제 은행에서 출제됩니다.' },
            { h: '실전과 같은 채점', p: '공개 예제로 먼저 실행해 보고, 제출하면 숨은 테스트케이스까지 통과율로 채점합니다. 코드는 외부와 차단된 환경에서 실행됩니다.' },
            { h: '기록은 나만', p: '연습 결과와 코드는 본인만 보고, 기업에는 전달되지 않습니다. 실전 응시 횟수에도 영향을 주지 않습니다.' },
          ] },
          { h: '진행 순서', cards: [
            { h: '1. 언어와 유형 고르기', p: '연습할 언어와 문제 유형, 난이도를 고릅니다.' },
            { h: '2. 풀고 실행하기', p: '편집기에서 코드를 쓰고 공개 예제로 바로 실행해 봅니다.' },
            { h: '3. 제출하고 채점받기', p: '통과한 테스트 수와 실패한 경우를 확인합니다.' },
            { h: '4. 다시 풀기', p: '같은 문제를 다른 언어로, 또는 다음 난이도로 이어 갑니다.' },
          ] },
        ],
        ctas: [{ label: '요금제 보기', href: '#/pricing', primary: true }, { label: '채용 공고 둘러보기', href: '/applicant/#/jobs' }],
        // 첫 화면 바로가기 버튼 → 미니 창(알고리즘 / SQL). 2026-11-06 사용자 지시
        launch: {
          label: 'headhunterAI 코딩테스트 연습 바로가기',
          title: '어떤 문제를 연습할까요?',
          close: '닫기',
          tracks: [
            { key: 'algo', btn: '알고리즘 문제 시작하기', href: '/applicant/#/practice/coding/junior',
              desc: 'Python · C언어로 푸는 알고리즘 문제입니다. 지금은 Python만 지원하고 C언어는 준비 중입니다.',
              who: '백엔드 개발자, 소프트웨어 개발자, AI/AX 엔지니어에게 유용해요.' },
            { key: 'sql', btn: 'SQL 문제 시작하기', href: '/applicant/#/practice/sql',
              desc: 'SQLD 시험 유형(제1과목 데이터 모델링 · 제2과목 SQL 기본과 활용)의 4지선다 문제입니다. SQL 결과는 실제로 실행해 계산했습니다.',
              who: '데이터 분석가, 데이터 사이언티스트에게 유용해요.' },
          ],
        },
      },
      'career-consulting': {
        title: ['headhunter 취업 컨설팅', '내 면접을 숫자로 보고, 다음을 준비'],
        lead: '내 이력서에 맞춘 AI 면접을 보고, 항목별 점수와 잘한 점·발전할 점, 다음 면접까지 해 볼 연습 과제를 받아 봅니다.',
        status: 'AI 면접 후 정량 리포트 이메일은 지금 받을 수 있습니다. 공고 없이 보는 직무별 모의면접(AI/AX · 백엔드 · 데이터 분석)은 준비 중입니다.',
        sections: [
          { h: '받을 수 있는 것', cards: [
            { h: '내 이력서 맞춤 질문', p: '확인한 이력서의 경력·프로젝트를 근거로 질문이 나오고, 답변이 모호하면 더 구체적으로 묻는 꼬리질문이 이어집니다.' },
            { h: '정량 리포트', p: 'AI 면접 점수와 5개 항목(직무 전문성·논리성·내용 관련성·전달력·조직 적합성)의 100점 환산 점수를 한 장으로 받습니다.' },
            { h: 'AI 코치 코멘트', p: '가장 높은 항목과 낮은 항목을 짚고, 다음 면접 전까지 해 볼 연습 과제 2~3개를 제안합니다. 확정된 점수만 근거로 씁니다.' },
            { h: '직무별 모의면접', p: 'AI/AX 엔지니어 · 백엔드 개발자 · 데이터 분석가 직무를 골라 실전과 같은 방식으로 연습합니다. (준비 중)' },
          ] },
          { h: '진행 순서', cards: [
            { h: '1. 이력서 확인', p: '이력서를 올리면 내용을 읽어 정리하고, 직접 확인한 내용만 씁니다.' },
            { h: '2. AI 면접', p: '질문 8개와 꼬리질문에 답합니다. 기본 질문 90초, 꼬리질문 60초.' },
            { h: '3. 리포트 받기', p: '분석이 끝나면 버튼 한 번으로 정량 리포트를 이메일로 받습니다. 합격 여부와는 별개입니다.' },
            { h: '4. 연습하고 다시', p: '연습 과제와 코딩 연습으로 약한 항목을 보완합니다.' },
          ] },
        ],
        ctas: [{ label: '채용 공고 둘러보기', href: '/applicant/#/jobs', primary: true }, { label: '요금제 보기', href: '#/pricing' }],
      },
      'first-round-hiring': {
        title: ['기업용 1차 채용 서비스', '1차 면접을 같은 기준으로, 자동으로'],
        lead: '공고를 올리면 AI가 지원자마다 이력서에 맞춘 1차 면접을 진행하고, 정해 둔 기준으로 점수와 1차 결과를 만듭니다.',
        status: '',
        sections: [
          { h: '제공하는 것', cards: [
            { h: '공고 맞춤 AI 면접', p: '인성 2 · 이력서 기반 3 · 직무 2 · 기업 1, 8문항. 모든 질문은 질문 은행에서 공고와 이력서로 검색해 고른 뒤, 공고의 회사 소개·주요 업무와 지원자 이력서에 맞춰 다시 씁니다.' },
            { h: '근거 있는 평가', p: 'AI는 항목별 0~5점과 답변에서 그대로 인용한 근거만 냅니다. 합산과 합격·불합격은 공고에 설정한 가중치와 합격선으로 계산합니다.' },
            { h: '코딩테스트', p: '공고마다 사용 여부와 반영 비중을 정합니다. 지금은 Python 기본 문제를 제공하고, 유형·언어를 고르는 맞춤 코딩테스트는 준비 중입니다.' },
            { h: '합불 결정 방식 선택', p: '자동: AI 판정을 바로 확정. 반자동: 담당자가 \'허용\' 또는 \'판정 변경\'을 한 번 눌러 확정. 점수는 어느 쪽도 바뀌지 않습니다.' },
          ] },
          { h: '진행 순서', cards: [
            { h: '1. 공고와 기준 설정', p: '공고를 쓰고 평가 항목 가중치, 합격선, 코딩테스트, 결정 방식을 정합니다.' },
            { h: '2. 지원자 응시', p: '지원자는 이력서 확인과 장치 점검 후 AI 면접과 코딩테스트를 봅니다.' },
            { h: '3. AI 평가', p: '응시가 끝나면 문항별 점수·근거·강점·개선점이 정리됩니다.' },
            { h: '4. 1차 결과 확정', p: '자동 공고는 바로, 반자동 공고는 담당자 확인 후 결과가 공개됩니다.' },
          ] },
        ],
        ctas: [{ label: '기업 담당자 로그인', href: '/admin/', primary: true }, { label: '요금제 보기', href: '#/pricing' }],
      },
      'hiring-dashboard': {
        title: ['기업 맞춤 대시보드', '지원자 전체를 한 화면에서'],
        lead: '공고별 지원자 현황부터 한 사람의 면접 리포트, 1차 합격자와 통계까지 기업 담당자 화면 하나에서 확인합니다.',
        status: '',
        sections: [
          { h: '화면 구성', cards: [
            { h: '지원자 목록', p: '공고·1차 결과·응시 상태로 거르고, AI 면접·코딩·종합 점수 순으로 봅니다. 반자동 공고는 \'확인 대기\'만 모아 볼 수 있습니다.' },
            { h: '한 페이지 리포트', p: '점수와 항목별 근거, 질문별 답변과 꼬리질문, 코딩 제출 코드, 이력서, 장애·이탈 기록을 한 화면에 모았습니다.' },
            { h: '비교와 다음 전형', p: '2~3명을 나란히 비교하고, 1차 합격자 목록과 연락처를 공고별로 확인합니다.' },
            { h: '통계', p: '월별 응시자 수, 공고별 1차 합격률과 평균 점수, 이력서 인식 건수를 집계합니다.' },
          ] },
          { h: '우리 회사에 맞추기', cards: [
            { h: '평가 기준', p: '5개 평가 항목의 가중치를 5% 단위로, 합격선을 점수로 정합니다.' },
            { h: '코딩테스트', p: '사용 여부와 AI 면접 대비 반영 비중(10% 단위)을 정합니다.' },
            { h: '결정 방식', p: '회사 규모와 채용 방식에 맞춰 자동 또는 반자동을 고릅니다.' },
            { h: '내부 메모', p: '같은 회사 담당자끼리만 보는 메모를 지원자별로 남깁니다.' },
          ] },
        ],
        ctas: [{ label: '기업 담당자 로그인', href: '/admin/', primary: true }, { label: '요금제 보기', href: '#/pricing' }],
      },
    },
    statusH: '지금 제공 범위',
    aboutTitle: ['면접의 첫 단계를', '더 투명하게'],
    aboutLead: 'headhunterAI는 1차 면접을 AI로 진행하되, 판단 과정을 지원자와 기업이 모두 이해할 수 있게 만드는 서비스입니다. 중소·중견 기업 그룹, 스타트업 창업자, 인원이 적은 HR팀이 headhunterAI로 초기 지원자 선별의 힘든 일을 덜어 냅니다.',
    aboutWhyH: '만든 이유',
    aboutWhy: [
      '면접은 지난 100년 동안 크게 바뀌지 않았지만, 지금은 빠르게 바뀌고 있습니다. 미국 기업의 절반 이상이 채용에 AI 도구를 쓰고, Fortune 500 기업에서는 94~98%에 이릅니다. 반면 인원이 적은 HR팀에게 1차 면접은 여전히 손이 많이 가는 일입니다. 공석 하나를 채우는 데 연봉의 3~4배가 든다고 보는 기업도 많습니다.',
      '사람의 평가도 생각보다 흔들립니다. 미국 기술직 지원자 3,296명을 대상으로 한 현장 실험(Avery 외, 2026)에서 전문 채용 담당자의 점수는 평가한 시간대에 따라 달라졌고, 첫 질문의 답변에 끌려 나머지 답변도 비슷하게 매겼으며, 점수를 가운데로 몰아 주는 경향이 있었습니다. 같은 면접을 AI가 평가했을 때는 이런 패턴이 나타나지 않았고, 12개월 뒤 실제 취업 성과를 사람 점수나 이력서 지표보다 두 배 이상 잘 예측했습니다.',
      '하지만 같은 연구는 경고도 남겼습니다. 면접관 없이 혼자 답하는 비대면 면접은 다음 단계로 넘어가는 지원자를 약 53% 줄였고, 우수한 지원자도 예외가 아니었습니다. 원인은 기술 자체가 아니라 "공정하지 않을 것 같다", "경쟁이 지나치게 넓을 것 같다"는 인식이었고, 면접이 구조화되어 있고 공정하게 설계되었다는 것을 분명히 알리면 이 이탈을 줄일 수 있다고 연구진은 제안합니다.',
      'headhunterAI는 이 두 결과를 함께 담으려고 만들었습니다. AI 평가의 일관성은 살리고, 지원자가 과정을 믿지 못해 떠나는 일은 줄이는 것. 그래서 질문 구성과 응시 규칙을 미리 알리고, 점수에는 답변 속 근거를 붙이고, 합불은 정해진 규칙이 결정하게 했습니다.',
    ],
    aboutChangeH: '만들고 싶은 변화',
    aboutChange: [
      '작은 팀도 큰 기업과 같은 기준으로: 같은 구조의 질문 8개와 같은 평가 기준을 모든 지원자에게 적용해, 평가하는 시간이나 순서에 따라 결과가 달라지지 않게 합니다. HR 인원이 적은 기업도 1차 선별을 일관되게 운영할 수 있습니다.',
      '지원자가 떠나지 않는 AI 면접: 질문 구성(인성·이력서·직무·기업), 꼬리질문 개수, 답변 시간을 응시 전에 보여 주고, 합불과 별개로 항목별 점수 리포트를 받을 수 있게 합니다. 과정이 보이면 공정하지 않다는 인식으로 이탈하는 일을 줄일 수 있다고 봅니다.',
      '판단은 끝까지 사람과 규칙이: AI는 점수와 인용만 내고, 반자동 공고는 담당자가 확인해야 결과가 공개됩니다. 나이·가족·학벌 같은 질문은 차단하고, 지원자가 확인한 이력서 내용만 씁니다.',
      '준비할 기회도 함께: 기업만 AI를 쓰는 것이 아니라 지원자도 무제한 코딩 연습과 리포트로 같은 단계를 미리 준비하고 돌아볼 수 있게 합니다.',
    ],
    aboutSource: '출처: Avery, M., Ip, E., Leibbrandt, A., Vecci, J. (2026). "A Brave New World of Hiring: A Natural Field Experiment on How Asynchronous Interviews and AI Assessment Reshape Recruitment." CESifo Working Paper No. 12573 (SSRN 6482825). 미국 기술직 지원자 대상 연구이며, 기업 AI 도입률·공석 비용은 이 논문이 인용한 Brookings(2025)·Fuller 외(2021)·Navarra(2022) 자료입니다.',
    aboutPrinciplesH: '일하는 방식',
    aboutPrinciples: [
      { ic: '1', h: 'AI는 점수와 근거만', p: 'AI는 항목별 점수와 답변 인용을 내고, 합산·합불·상태 변경은 코드의 규칙이 처리합니다.' },
      { ic: '2', h: '확인한 이력서만', p: '이력서는 지원자가 직접 확인한 내용만 질문과 평가에 씁니다. 나이·가족·학벌 같은 질문은 차단합니다.' },
      { ic: '3', h: '결과는 확정되는 대로', p: '자동 공고는 분석 직후, 반자동 공고는 담당자가 확인한 뒤 결과를 공개합니다. 합불과 별개로 항목별 점수를 담은 리포트를 이메일로 받을 수 있습니다.' },
    ],
    aboutTechH: '기술 구성',
    aboutTech: [
      { ic: 'L', h: '자체 서버의 언어 모델', p: '면접 질문과 평가는 서비스 서버에서 직접 운영하는 오픈 모델(Qwen3)로 처리하며, 외부 AI API로 이력서·답변을 보내지 않습니다.' },
      { ic: 'O', h: '이력서 인식', p: 'PDF 텍스트 추출과 Tesseract OCR(한국어·영어)을 함께 씁니다.' },
      { ic: 'S', h: '격리된 코드 실행', p: '코딩테스트 코드는 네트워크가 차단된 별도 컨테이너에서 시간·메모리 제한을 두고 실행합니다.' },
    ],
    aboutTechInbound: '지원자 이력서 · 답변 · 코드',
    aboutTechResult: '지원자의 이력서·답변·코드는 서버 밖으로 나가지 않습니다',
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
    paySubscribe: '네이버페이로 구독하기',
    pricingNote: 'Advanced 결제(네이버페이 · 토스페이먼츠)는 준비 중입니다. 표시 가격은 출시 전 바뀔 수 있습니다.',
  },
  en: {
    heroTitle: 'A new standard for discovering talent,',
    heroLead: 'Upload your résumé and the AI reads it, then runs a first-round interview with questions made for you. Companies get first-round results built on the same criteria and evidence.',
    startApplicant: 'Start as an applicant →',
    loginCompany: 'Recruiter sign-in',
    soon: 'Coming soon',
    services: {
      'coding-practice': {
        title: ['Unlimited Coding Practice', 'As much as you want, in your language'],
        lead: 'Practise IT coding problems again and again — no job post needed. Pick a language, run your code right away, and get graded the same way as a real coding test.',
        status: 'Algorithm problems in Python are available now after sign-in, with no limit on runs (public examples, then hidden tests on submit; Junior and Senior levels). SQLD-style SQL problems (Parts 1 and 2, multiple choice) are available too. C is coming, and details may change before launch.',
        sections: [
          { h: 'What you can do', cards: [
            { h: 'Choose a language', p: 'Practise the same problem types in Python, C or SQL.' },
            { h: 'Problems by type', p: 'Problems come from a bank organised by the types companies actually pick: algorithms (greedy, DP and more), SQL queries and aggregation, low-latency C.' },
            { h: 'Graded like the real thing', p: 'Run against public examples first, then submit to be scored on hidden test cases too. Code runs in an isolated, network-blocked environment.' },
            { h: 'Private by default', p: 'Your results and code are only visible to you and never shared with companies. Practice does not count toward real attempts.' },
          ] },
          { h: 'How it works', cards: [
            { h: '1. Pick language and type', p: 'Choose the language, problem type and difficulty.' },
            { h: '2. Solve and run', p: 'Write code in the editor and run it against public examples.' },
            { h: '3. Submit and get graded', p: 'See how many tests passed and where it failed.' },
            { h: '4. Try again', p: 'Solve it in another language or move to the next level.' },
          ] },
        ],
        ctas: [{ label: 'See pricing', href: '#/pricing', primary: true }, { label: 'Browse open roles', href: '/applicant/#/jobs' }],
        launch: {
          label: 'Go to headhunterAI coding test practice',
          title: 'What would you like to practise?',
          close: 'Close',
          tracks: [
            { key: 'algo', btn: 'Start algorithm problems', href: '/applicant/#/practice/coding/junior',
              desc: 'Algorithm problems in Python and C. Python is available now; C is coming soon.',
              who: 'Useful for backend developers, software engineers and AI/AX engineers.' },
            { key: 'sql', btn: 'Start SQL problems', href: '/applicant/#/practice/sql',
              desc: 'Multiple-choice problems in the SQLD exam format (Part 1 data modeling, Part 2 SQL basics and usage). SQL results are computed by actually running the queries.',
              who: 'Useful for data analysts and data scientists.' },
          ],
        },
      },
      'career-consulting': {
        title: ['headhunter Career Consulting', 'See your interview in numbers, prepare the next one'],
        lead: 'Take an AI interview fitted to your résumé, then get per-item scores, what went well, what to improve, and practice tasks for your next interview.',
        status: 'The quantitative report email after an AI interview is available now. Role-based mock interviews without a job post (AI/AX · Backend · Data analysis) are coming soon.',
        sections: [
          { h: 'What you get', cards: [
            { h: 'Questions fitted to your résumé', p: 'Questions are grounded in the experience and projects you confirmed, and vague answers get a follow-up asking for specifics.' },
            { h: 'Quantitative report', p: 'Your AI interview score and five item scores (expertise · logic · relevance · delivery · fit) on a 100-point scale, on one page.' },
            { h: 'AI coach comments', p: 'Highlights your strongest and weakest items and suggests 2–3 practice tasks before your next interview, based only on finalised scores.' },
            { h: 'Role-based mock interviews', p: 'Pick AI/AX engineer, backend developer or data analyst and practise the way the real interview works. (Coming soon)' },
          ] },
          { h: 'How it works', cards: [
            { h: '1. Confirm your résumé', p: 'Upload it, we organise the content, and only what you confirm is used.' },
            { h: '2. AI interview', p: 'Answer 8 questions plus follow-ups: 90 seconds each, 60 for follow-ups.' },
            { h: '3. Get the report', p: 'When analysis is done, one click sends the quantitative report to your email. It is separate from the pass/fail result.' },
            { h: '4. Practise and retry', p: 'Work on weaker items with the practice tasks and coding practice.' },
          ] },
        ],
        ctas: [{ label: 'Browse open roles', href: '/applicant/#/jobs', primary: true }, { label: 'See pricing', href: '#/pricing' }],
      },
      'first-round-hiring': {
        title: ['AI First-Round Hiring', 'First interviews on one standard, automatically'],
        lead: 'Post a job and the AI runs a first-round interview fitted to each résumé, then produces scores and first-round results using the criteria you set.',
        status: '',
        sections: [
          { h: 'What you get', cards: [
            { h: 'AI interviews fitted to your post', p: '8 questions: 2 personality · 3 résumé · 2 role · 1 company. Every question is retrieved from our question bank using the job post and résumé, then rewritten to fit your company intro, duties and the applicant\'s résumé.' },
            { h: 'Evidence-based evaluation', p: 'The AI gives 0–5 scores per item with quotes taken verbatim from answers. Totals and pass/fail use the weights and pass mark set on the post.' },
            { h: 'Coding test', p: 'Turn it on per post and set its weight. A basic Python test is available today; custom tests by type and language are coming soon.' },
            { h: 'Choose how decisions are made', p: "Automatic: the AI decision is final right away. Semi-automatic: a recruiter finalises it with one click on 'Approve' or 'Change decision'. Scores never change." },
          ] },
          { h: 'How it works', cards: [
            { h: '1. Set up the post', p: 'Write the post and set item weights, pass mark, coding test and decision mode.' },
            { h: '2. Applicants take it', p: 'After a résumé check and device check, applicants take the AI interview and coding test.' },
            { h: '3. AI evaluation', p: 'Scores, evidence, strengths and improvements are compiled per question.' },
            { h: '4. Finalise results', p: 'Automatic posts publish right away; semi-automatic posts publish after recruiter review.' },
          ] },
        ],
        ctas: [{ label: 'Recruiter sign-in', href: '/admin/', primary: true }, { label: 'See pricing', href: '#/pricing' }],
      },
      'hiring-dashboard': {
        title: ['Custom Hiring Dashboard', 'Every applicant on one screen'],
        lead: 'From applicants per post to one person’s full interview report, shortlists and statistics — all in the recruiter screen.',
        status: '',
        sections: [
          { h: 'What’s inside', cards: [
            { h: 'Applicant list', p: "Filter by post, first-round result and status, and sort by interview, coding and total scores. For semi-automatic posts, view only those 'awaiting review'." },
            { h: 'One-page report', p: 'Scores with per-item evidence, answers and follow-ups per question, submitted code, résumé and disconnect/exit logs on one screen.' },
            { h: 'Compare and next steps', p: 'Compare 2–3 people side by side and see first-round passes with contact details per post.' },
            { h: 'Statistics', p: 'Monthly applicants, pass rate and average score per post, and résumé recognition counts.' },
          ] },
          { h: 'Make it yours', cards: [
            { h: 'Evaluation criteria', p: 'Set weights for the five items in 5% steps and a pass mark.' },
            { h: 'Coding test', p: 'Turn it on and set its weight against the interview in 10% steps.' },
            { h: 'Decision mode', p: 'Pick automatic or semi-automatic to match your size and process.' },
            { h: 'Internal notes', p: 'Leave notes per applicant that only your company’s recruiters can see.' },
          ] },
        ],
        ctas: [{ label: 'Recruiter sign-in', href: '/admin/', primary: true }, { label: 'See pricing', href: '#/pricing' }],
      },
    },
    statusH: 'Available today',
    aboutTitle: ['Making the first interview', 'more transparent'],
    aboutLead: 'headhunterAI runs first-round interviews with AI while keeping the reasoning understandable for both applicants and companies. Small and mid-sized corporate groups, startup founders, and lean HR teams use headhunterAI to handle the heavy lifting of early-stage applicant screening.',
    aboutWhyH: 'Why we built it',
    aboutWhy: [
      'Job interviews barely changed for a century, and now they are changing fast. More than half of U.S. firms use AI tools in hiring, rising to 94–98% among Fortune 500 companies. For lean HR teams, though, the first interview round is still heavy manual work, and many employers estimate that filling a vacancy costs three to four times the position\'s salary.',
      'Human judgment is also less steady than it looks. In a field experiment with 3,296 U.S. tech job applicants (Avery et al., 2026), professional recruiters\' scores shifted with the time of day, were anchored by the answer to the first question, and were compressed toward the middle. When AI evaluated the same interviews, these patterns did not appear, and its scores predicted job success 12 months later at least twice as well as human scores or CV-based metrics.',
      'The same study also carries a warning. One-way asynchronous interviews cut application continuation by about 53%, including among the most qualified applicants. The cause was not the technology itself but the perception that the process was unfair and overly competitive, and the authors suggest employers can reduce this drop-off by clearly signalling that interviews are structured and designed to be fair.',
      'We built headhunterAI to take both findings seriously: keep the consistency of AI evaluation, and stop applicants from walking away because they cannot trust the process. That is why we show the question structure and interview rules up front, attach evidence from the answer to every score, and let fixed rules decide pass or fail.',
    ],
    aboutChangeH: 'The change we want to lead',
    aboutChange: [
      'Big-company standards for small teams: every applicant gets the same eight structured questions and the same criteria, so results do not depend on when or in what order they were reviewed. Even companies with a small HR team can run a consistent first round.',
      'AI interviews applicants do not abandon: the question mix (personality, résumé, role, company), follow-up limit and answer time are shown before the interview, and applicants can receive a per-item score report separate from pass/fail. A visible process should reduce drop-off driven by perceived unfairness.',
      'People and rules keep the final say: the AI only produces scores and quotes, and semi-automatic posts publish results only after a recruiter confirms them. Questions about age, family or school background are blocked, and only résumé content the applicant has confirmed is used.',
      'A chance to prepare, too: AI is not only on the employer\'s side. Applicants can practise with unlimited coding problems and review their reports to prepare for the same step.',
    ],
    aboutSource: 'Source: Avery, M., Ip, E., Leibbrandt, A., Vecci, J. (2026). "A Brave New World of Hiring: A Natural Field Experiment on How Asynchronous Interviews and AI Assessment Reshape Recruitment." CESifo Working Paper No. 12573 (SSRN 6482825). The study covers U.S. tech job applicants; employer AI adoption and vacancy cost figures are from Brookings (2025), Fuller et al. (2021) and Navarra (2022) as cited in the paper.',
    aboutPrinciplesH: 'How we work',
    aboutPrinciples: [
      { ic: '1', h: 'AI scores, rules decide', p: 'The AI provides per-item scores and quotes; totals, pass/fail and status changes are handled by rules in code.' },
      { ic: '2', h: 'Only confirmed résumés', p: 'Only résumé content the applicant has confirmed is used. Questions about age, family or school background are blocked.' },
      { ic: '3', h: 'Results as soon as they are final', p: 'Automatic posts publish right after analysis; semi-automatic posts after recruiter review. A report with per-item scores can be emailed separately from pass/fail.' },
    ],
    aboutTechH: 'Under the hood',
    aboutTech: [
      { ic: 'L', h: 'Self-hosted language model', p: 'Questions and evaluation run on an open model (Qwen3) operated on our own server; résumés and answers are not sent to external AI APIs.' },
      { ic: 'O', h: 'Résumé recognition', p: 'PDF text extraction combined with Tesseract OCR (Korean and English).' },
      { ic: 'S', h: 'Isolated code execution', p: 'Coding-test code runs in a separate network-blocked container with time and memory limits.' },
    ],
    aboutTechInbound: 'Applicant résumé · answers · code',
    aboutTechResult: 'Applicant résumés, answers and code never leave our servers',
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
    paySubscribe: 'Subscribe with Naver Pay',
    pricingNote: 'Advanced payments (Naver Pay · Toss Payments) are coming soon. Prices may change before launch.',
  },
};

// 드롭다운 카드: 한국어(흰색) + 영어(작게, 네온)를 항상 함께 표시 (PRD WEB-03)
// 드롭다운 두 섹션(소제목은 영문 대문자 그대로, 클릭 없음). 섹션마다 왼쪽·오른쪽 카드 2개
const SERVICE_SECTIONS = [
  { title: 'JOB SEEKERS', items: [
    { id: 'coding-practice', ko: '무제한 코딩 연습', en: 'Unlimited Coding Practice' },
    { id: 'career-consulting', ko: 'headhunter 취업 컨설팅', en: 'headhunter Career Consulting' } ] },
  { title: 'EMPLOYERS', items: [
    { id: 'first-round-hiring', ko: '기업용 1차 채용 서비스', en: 'AI First-Round Hiring' },
    { id: 'hiring-dashboard', ko: '기업 맞춤 대시보드', en: 'Custom Hiring Dashboard' } ] },
];

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
  document.getElementById('dd-features').innerHTML = SERVICE_SECTIONS.map(sec => `
    <div class="dd-sec"><div class="dd-sec-h">${esc(sec.title)}</div><div class="dd-row">${sec.items.map(f => `
      <a class="dd-item" href="#/features/${f.id}"><b>${esc(f.ko)}</b><span class="en">${esc(f.en)}</span></a>`).join('')}</div></div>`).join('');
}

function home() {
  const L = t();
  return `
  <div class="wrap hero-wrap">
    <section class="hero">
      <div>
        <h1>${esc(L.heroTitle)}<br><span class="neon">headhunterAI</span></h1>
        <p class="lead">${esc(L.heroLead)}</p>
        <div class="row">
          <a class="btn primary lg" href="/applicant/#/jobs">${esc(L.startApplicant)}</a>
          <a class="btn lg" href="/admin/">${esc(L.loginCompany)}</a>
        </div>
      </div>
      <div class="hero-art" aria-hidden="true"></div>
    </section>
  </div>`;
}

function head(title, lead) {
  return `<section class="page-head"><h1>${esc(title[0])}<br><span class="neon">${esc(title[1])}</span></h1><p>${esc(lead)}</p></section>`;
}

function cards(list) {
  return `<div class="cards">${list.map(c => `
    <div class="card2">${c.ic ? `<div class="ic">${esc(c.ic)}</div>` : ''}<h3>${esc(c.h)}</h3><p>${esc(c.p)}</p></div>`).join('')}</div>`;
}

/* About 페이지 다이어그램(2026-11-18 사용자 지시 — 카드 6개를 그냥 나열하지 말고 시각화).
   SVG 대신 HTML+CSS로 짜서 한국어·영어 길이가 달라도 자동으로 줄바꿈된다(반응형). */
function flowDiagram(list) {
  // 2026-11-18 사용자 지시: 원형 번호 아이콘 삭제, 화살표로 잇는 흐름만 남긴다
  return `<div class="flow">${list.map((c, i) => `
    ${i ? '<div class="flow-arrow" aria-hidden="true">→</div>' : ''}
    <div class="card2 flow-step"><h3>${esc(c.h)}</h3><p>${esc(c.p)}</p></div>`).join('')}</div>`;
}

/* '기술 구성'은 카드가 아니라 보안 경계 다이어그램으로(2026-11-18 사용자 지시 — 더 creative하게).
   바깥에서 들어오는 지원자 데이터 → 점선 경계 안 3개 구성 요소를 거침 → 밖으로 나가는 화살표는 없다(밀봉) */
function perimeterDiagram(list, inbound, result) {
  return `<div class="perimeter-wrap">
    <div class="perimeter-in" aria-hidden="true"><span>${esc(inbound)}</span><span class="perimeter-in-arrow">↓</span></div>
    <div class="perimeter">
      <div class="perimeter-row">${list.map((c, i) => `
        ${i ? '<div class="perimeter-sep" aria-hidden="true"></div>' : ''}
        <div class="perimeter-node"><h3>${esc(c.h)}</h3><p>${esc(c.p)}</p></div>`).join('')}</div>
    </div>
    <div class="perimeter-seal">${esc(result)}</div>
  </div>`;
}

function service(id) {
  const L = t();
  const S = L.services[id];
  if (!S) return home();
  return `<div class="wrap">${head(S.title, S.lead)}
    ${S.status ? `<p class="note" style="margin:-16px 0 8px">${esc(S.status)}</p>` : ''}
    ${S.launch ? `<div class="row" style="justify-content:center;margin:20px 0 8px"><button type="button" class="btn lg launch-btn" id="launch-practice">${esc(S.launch.label)}</button></div>` : ''}
    ${S.sections.map(sec => `<section class="sec"><h2>${esc(sec.h)}</h2>${cards(sec.cards)}</section>`).join('')}
    <div class="row" style="justify-content:center;margin:28px 0 8px">${S.ctas.map(c => `<a class="btn lg${c.primary ? ' primary' : ''}" href="${c.href}">${esc(c.label)}</a>`).join('')}</div>
  </div>`;
}

function about() {
  const L = t();
  return `<div class="wrap">${head(L.aboutTitle, L.aboutLead)}
    <section class="sec"><h2>${esc(L.aboutWhyH)}</h2>${L.aboutWhy.map(p => `<p style="max-width:760px;margin:0 auto 16px;color:var(--muted)">${esc(p)}</p>`).join('')}</section>
    <section class="sec"><h2>${esc(L.aboutChangeH)}</h2>${L.aboutChange.map(p => `<p style="max-width:760px;margin:0 auto 16px;color:var(--muted)">${esc(p)}</p>`).join('')}</section>
    <section class="sec"><h2>${esc(L.aboutPrinciplesH)}</h2>${flowDiagram(L.aboutPrinciples)}</section>
    <section class="sec"><h2>${esc(L.aboutTechH)}</h2>${perimeterDiagram(L.aboutTech, L.aboutTechInbound, L.aboutTechResult)}</section>
    <p class="note" style="max-width:760px;margin:40px auto 0;text-align:left">${esc(L.aboutSource)}</p>
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
        ${p.href ? `<a class="btn lg" href="${p.href}">${esc(p.cta)}</a>`
          : payOn ? `<a class="btn primary lg" href="${audience === 'company' ? '/admin/#/billing' : '/applicant/#/billing'}">${esc(L.paySubscribe)}</a>`
          : `<button class="btn primary lg" type="button" disabled style="opacity:.55;cursor:not-allowed">${esc(L.payReady)}</button>`}
      </div>`).join('')}
    </div>
    <p class="note">${esc(L.pricingNote)}</p>
  </div>`;
}

// Features는 드롭다운만 있고 목록 페이지는 없다. #/features/<id> = 서비스 페이지 4개
const ROUTES = { '': home, about, pricing };

// 결제가 켜진 환경(네이버페이 키 또는 로컬 가짜 결제)에서만 Advanced 버튼이 요금제 화면으로 간다(PRD v0.9 PAY-05)
let payOn = false;
fetch('/api/payments/config').then((r) => r.json()).then((c) => { payOn = !!c.enabled; if (payOn && location.hash.startsWith('#/pricing')) render(); }).catch(() => {});

function render() {
  document.documentElement.lang = lang;
  document.querySelectorAll('.lang button').forEach(b => b.classList.toggle('on', b.dataset.lang === lang));
  dropdown();
  const [page, sub] = location.hash.replace(/^#\/?/, '').split('/');
  document.querySelectorAll('[data-nav]').forEach(a => a.classList.toggle('on', a.dataset.nav === page));
  document.querySelector('.dd').classList.remove('open');
  view.innerHTML = page === 'features' ? service(sub) : (ROUTES[page] || home)();
  // 언어·대상 전환은 같은 위치를 유지하고, 메뉴 이동 때만 스크롤을 옮긴다
  if (location.hash === lastHash) return;
  lastHash = location.hash;
  window.scrollTo(0, 0);
}
let lastHash = null;

document.querySelector('.lang').addEventListener('click', e => { const b = e.target.closest('[data-lang]'); if (b) setLang(b.dataset.lang); });
view.addEventListener('click', e => { const b = e.target.closest('[data-aud]'); if (b) { audience = b.dataset.aud; render(); } });
// 코딩테스트 연습 바로가기 → 미니 창: 알고리즘(Python·C) / SQL (2026-11-06 사용자 지시)
view.addEventListener('click', e => {
  if (!e.target.closest('#launch-practice')) return;
  const Lx = t().services['coding-practice'].launch;
  const bg = document.createElement('div');
  bg.className = 'modal-bg';
  bg.innerHTML = `<div class="modal launch-modal" role="dialog" aria-modal="true" aria-label="${esc(Lx.title)}">
    <h2>${esc(Lx.title)}</h2>
    ${Lx.tracks.map(tr => `<div class="launch-track">
      <a class="btn lg${tr.key === 'algo' ? ' primary' : ''}" href="${tr.href}">${esc(tr.btn)}</a>
      <p class="small">${esc(tr.desc)}</p><p class="small launch-who">${esc(tr.who)}</p></div>`).join('')}
    <div class="row" style="justify-content:flex-end"><button type="button" class="btn sm ghost" data-close>${esc(Lx.close)}</button></div></div>`;
  const close = () => bg.remove();
  bg.addEventListener('click', ev => { if (ev.target === bg || ev.target.closest('[data-close]')) close(); });
  document.addEventListener('keydown', function esc1(ev) { if (ev.key === 'Escape') { close(); document.removeEventListener('keydown', esc1); } });
  document.body.appendChild(bg);
});
window.addEventListener('hashchange', render);
// Features: 마우스를 올리면(데스크톱) 또는 누르면(모바일·터치) 드롭다운이 열린다. 누르기만으로는 페이지를 바꾸지 않는다
const dd = document.querySelector('.dd');
dd.querySelector('[data-nav="features"]').addEventListener('click', e => { e.preventDefault(); dd.classList.toggle('open'); });
document.addEventListener('click', e => { if (!dd.contains(e.target)) dd.classList.remove('open'); });
render();
