/* headhunterAI 공통 첫 페이지 — 해시 라우팅(#/ · #/features/<서비스> · #/about · #/pricing) + KO/EN 문구 전환.
   메뉴 이름(Features · About Us · Pricing)은 두 언어 모두 영어로 두고, 안의 내용만 바꾼다. */
const { esc } = HH;
const view = document.getElementById('view');

const T = {
  ko: {
    heroTitle: '인재를 발견하는 새로운 기준,',
    heroLead: '지원자의 가능성이 기업의 기회로 이어지도록. headhunterAI는 프라이빗 AI 기술로 더 공정하고 간편한 채용 경험을 만듭니다.',
    startApplicant: '지원자로 시작하기 →',
    loginCompany: '기업 담당자 로그인',
    soon: '준비 중',
    // Features 드롭다운의 4개 서비스 페이지 (#/features/<id>). 드롭다운 라벨(한·영)은 언어 전환과 관계없이 SERVICE_LABELS를 쓴다
    services: {
      'coding-practice': {
        // 2026-11-19 사용자 지시 — 기존 제목 2줄을 지우고 영문 한 줄 제목 + 본문 2문단 + 오른쪽 그림 구성으로 바꿈
        hero: {
          title: 'UNLIMITED CODING EXERCISES',
          body: [
            'IT 분야의 커리어를 준비하는 일과 코딩 역량을 쌓는 일이 한 곳에서 이어지기를 바랐습니다. 최신 채용 흐름을 반영한 알고리즘 문제와 SQL 문제를 부담 없이, 횟수 제한 없이 풀어 볼 수 있는 공간을 만든 이유입니다.',
            '연습의 가치는 맞고 틀림에서 끝나지 않습니다. headhunterAI coach의 분석이 더해질 때, 문제를 푸는 시간은 그대로 쌓이는 역량이 됩니다.',
          ],
        },
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
          { h: 'headhunterAI와 함께하는 취업준비', rail: true, cards: [
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
        title: ['headhunterAI 취업 컨설팅', '내 면접결과 100% 맞춤 분석'],
        sections: [
          { h: 'headhunterAI 서비스', rail: true, cards: [
            { h: '내 이력서 맞춤 질문', p: '확인한 이력서의 경력·프로젝트를 근거로 질문이 나오고, 답변이 모호하면 더 구체적으로 묻는 꼬리질문이 이어집니다.' },
            { h: '정량 리포트', p: 'AI 면접 점수와 5개 항목(직무 전문성·논리성·내용 관련성·전달력·조직 적합성)의 100점 환산 점수를 한 장으로 받습니다.' },
            { h: 'AI 코치 코멘트', p: '가장 높은 항목과 낮은 항목을 짚고, 다음 면접 전까지 해 볼 연습 과제 2~3개를 제안합니다. 확정된 점수만 근거로 씁니다.' },
            { h: '직무별 모의면접', p: 'AI/AX 엔지니어 · 백엔드 개발자 · 데이터 분석가 직무를 골라 실전과 같은 방식으로 연습합니다. (준비 중)' },
          ] },
          { h: 'headhunterAI와 함께하는 취업준비', rail: true, cards: [
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
          { h: 'headhunterAI와 함께하는 취업준비', rail: true, cards: [
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
    // ── About (2026-11-19 사용자 지시로 전면 재구성) ──
    // 옛 헤드라인('면접의 첫 단계를 더 투명하게')은 삭제. 유니콘이 페이지를 연다.
    // 흐름: 영감받은 태도 → 채용에서 본 문제 → 그 태도를 제품으로 구현하는 방식
    aboutEyebrow: '유니콘 기업의 도전 정신에서 시작하다',
    // 2026-11-19 사용자 지시: 제목은 한국어판도 영문과 같은 문장을 쓴다
    aboutHeadline: 'Great potential deserves to be seen.',
    // **…** 만 font-weight 600으로 강조. 영문판과 강조 위치를 맞춘다
    aboutIntro: [
      '**작은 팀의 대담한 생각**, 익숙한 방식에 질문을 던지는 용기, 더 나은 방법을 찾는 끊임없는 시도. 우리는 유니콘 기업을 성장시킨 이러한 태도에서 영감을 받았습니다.',
      '지원자의 가능성이 기업의 기회로 이어지도록. headhunterAI는 프라이빗 AI 기술로 더 공정하고 간편한 채용 경험을 만듭니다.',
    ],
    // 유니콘 밴드 — 왼쪽 글귀 / 오른쪽 그림 (2026-11-19 사용자 지시)
    aboutBand: [
      '**headhunterAI**로\n기업 인재 채용의 방식을 다시 씁니다.',
      '**headhunterAI coach**로\n미래의 인재들에게 단단한 버팀목이 되어 줍니다.',
    ],
    aboutPricingCta: 'headhunterAI 요금제 바로가기',
    aboutBeliefH: '그 믿음이 채용에서 뜻하는 것',
    aboutBelief: [
      '가능성이 가장 많이 묻히는 자리가 1차 전형입니다. 인원이 적은 팀에게 1차 면접은 여전히 사람이 하나하나 해야 하는 일이고, 공석 하나를 메우는 데 연봉의 3~4배가 든다고 보는 기업도 많습니다. 빨리 훑고 넘어갈 수밖에 없는 구조입니다.',
      '그렇게 쫓길 때 사람의 판단은 생각보다 일정하지 않습니다. 미국 기술직 지원자 3,296명을 대상으로 한 현장 실험(Avery, 2026)에서 채용 담당자의 점수는 평가한 시간대에 따라 흔들렸고, 첫 답변에 끌려갔고, 가운데 점수로 몰렸습니다. 같은 면접을 AI가 평가했을 때는 그런 현상이 나타나지 않았습니다. 다만 같은 연구는 경고도 남겼습니다 — 면접관 없이 혼자 답하는 비대면 면접에서는 다음 단계로 넘어가는 지원자가 약 53% 줄었고, 우수한 지원자도 예외가 아니었습니다. 과정이 공정해 보이지 않았기 때문입니다.',
      '그래서 두 결과를 함께 담았습니다. 모든 지원자가 같은 질문 구조와 같은 평가 기준을 거치고, 8문항 중 3개는 지원자 본인의 이력서에서 나옵니다. 응시 규칙과 꼬리질문 개수, 답변 시간은 시작 전에 모두 알려 드립니다. 이탈을 줄이는 것은 저희가 지향하는 목표이고, 아직 측정한 결과는 아닙니다.',
    ],
    aboutPracticeH: '우리의 원칙',
    aboutPractice: [
      { h: '같은 질문, 같은 기준', p: '모든 지원자가 같은 8문항 구조로 답하고 같은 루브릭으로 채점됩니다. 누구를 언제 먼저 봤는지에 따라 결과가 달라지지 않습니다.' },
      { h: '근거를 따라갈 수 있는 결과', p: 'AI는 항목별 점수와 답변에서 그대로 따온 근거를 냅니다. 합산과 합불은 모델이 아니라 코드에 적힌 규칙이 결정합니다.' },
      { h: '최종 판단은 기업이', p: '분석이 끝나면 바로 공개할지, 담당자가 확인한 뒤 공개할지 공고마다 고릅니다. 지원자는 항목별 리포트를 메일로 요청할 수 있습니다.' },
    ],
    // 출처는 About 페이지 각주가 아니라 맨 아래 'source' 링크(#/sources)로 모았다 (2026-11-19 사용자 지시)
    // 대표자 연락처 (#/contact) — 2026-11-19 사용자 지시
    contactTitle: ['대표자 연락처', 'Founder of headhunterAI'],
    contactLead: '서비스·제휴·채용 문의는 아래 메일로 보내 주세요.',
    contactName: '김소현',
    contactRole: 'Founder of headhunterAI',
    contactEmailLabel: '이메일',
    contactFoot: '궁금한 점이나 불편한 점은 이메일로 문의 부탁드립니다.',
    sourcesTitle: ['이 사이트가', '인용한 자료'],
    sourcesLead: 'About 페이지의 수치와 연구 결과가 어디에서 왔는지 모아 둔 곳입니다.',
    sources: [
      {
        h: '주 연구 — 비대면 면접과 AI 평가 현장 실험',
        cite: 'Avery, M., Ip, E., Leibbrandt, A., Vecci, J. (2026). "A Brave New World of Hiring: A Natural Field Experiment on How Asynchronous Interviews and AI Assessment Reshape Recruitment." CESifo Working Paper No. 12573 (SSRN 6482825).',
        note: '미국 기술직 지원자 3,296명을 대상으로 한 현장 실험입니다. About 페이지의 다음 내용이 이 연구에서 나왔습니다 — 전문 채용 담당자의 점수가 평가한 시간대에 따라 달라지고 첫 질문의 답변에 끌렸으며 가운데로 몰렸다는 점, 같은 면접을 AI가 평가했을 때는 그 패턴이 나타나지 않고 12개월 뒤 취업 성과를 사람 점수·이력서 지표보다 두 배 이상 잘 예측했다는 점, 면접관 없이 혼자 답하는 비대면 면접이 다음 단계로 넘어가는 지원자를 약 53% 줄였다는 점. 모두 이 연구의 결과이며 headhunterAI로 측정한 수치가 아닙니다.',
      },
      {
        h: '기업 AI 도입률 · 공석 비용 수치',
        cite: 'Brookings (2025) · Fuller 외 (2021) · Navarra (2022).',
        note: '"미국 기업의 절반 이상, Fortune 500에서는 94~98%"라는 도입률과 "공석 하나를 채우는 데 연봉의 3~4배"라는 비용 수치의 출처입니다. 위 Avery 외(2026) 논문이 인용한 자료이며, 개별 수치가 어느 자료에서 나왔는지는 그 논문의 표기를 따릅니다.',
      },
    ],
    aboutTechH: '우리의 시스템',
    aboutTech: [
      { ic: 'L', h: '자체 서버의 언어 모델', p: '질문 생성과 평가, 음성 인식을 직접 운영하는 오픈 모델(Qwen3)로 처리합니다.' },
      { ic: 'O', h: '이력서 인식', p: 'PDF 텍스트 추출과 Tesseract OCR(한국어·영어)을 같은 환경에서 함께 씁니다.' },
      { ic: 'S', h: '격리된 코드 실행', p: '코딩테스트 코드는 별도 컨테이너에서 시간·메모리·동시 실행 제한을 두고 실행합니다.' },
    ],
    aboutTechInbound: '지원자의 이력서',
    aboutTechResult: '지원자의 답변·영상·코드는 저희 서버에만 있습니다',
    // 첫 페이지 Features 섹션 (2026-11-19 사용자 지시) — 히어로 아래로 스크롤하면 브랜드 소개 → Features
    homeFeaturesH: 'Features',
    homeDemos: [
      { label: '지원자 대시보드',
        desc: '지원한 공고와 면접 진행 상황, 결과 알림을 한 화면에서 봅니다.',
        cta: '공고 둘러보기', href: '/applicant/#/jobs', img: '/images/demo_dashboard.png', rail: true },
      { label: '무제한 코딩 연습',
        desc: '최신 채용 흐름을 반영한 알고리즘·SQL 문제를 횟수 제한 없이 풀어 봅니다. 공개 예제로 실행해 보고 숨은 테스트까지 채점받습니다.',
        cta: '연습 화면 보기', href: '#/features/coding-practice', img: '/images/demo_coding.png' },
      { label: 'headhunterAI만의 정성적 리포트',
        desc: '점수만 주고 끝내지 않습니다. 문항마다 내 답변을 그대로 인용해, 다섯 항목이 왜 그 점수인지 문장으로 설명합니다.',
        cta: '취업 컨설팅 보기', href: '#/features/career-consulting', shots: ['/images/demo_report_1.png', '/images/demo_report_2.png', '/images/demo_report_3.png'] },
    ],
    // Pricing 히어로 (2026-11-19 사용자 지시): 유리 박스 'Subscription' → 흰 제목 2줄, 끝의 'is now.'만 네온
    pricingEyebrow: 'Subscription',
    pricingHead: ['The best time to bring headhunterAI', 'to your system'],
    pricingHeadAccent: 'is now.',
    segApplicant: '지원자', segCompany: '기업',
    perMonth: '/월', free: '무료',
    plans: {
      applicant: [
        { name: 'Basic', tag: 'FREE', desc: '공고에 지원해 AI 1차 면접을 봅니다.', price: 0, sub: '주 1회 이하 이용',
          items: ['공고 지원 · AI 1차 면접', '이력서 자동 인식', '결과 공개(합불 · 강점 · 개선점)'], cta: '무료로 시작하기', href: '/applicant/#/jobs' },
        { name: 'Advanced', tag: 'RECOMMENDED', rec: true, desc: '실전 전에 IT 직군 모의면접과 코딩 연습을 충분히 합니다.', price: 9900, sub: '주 2회 이상 이용',
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
  },
  en: {
    heroTitle: 'A new standard for discovering talent,',
    heroLead: 'Turning candidate potential into company opportunity. headhunterAI makes early hiring fairer and simpler — powered by private AI.',
    startApplicant: 'Start as an applicant →',
    loginCompany: 'Recruiter sign-in',
    soon: 'Coming soon',
    services: {
      'coding-practice': {
        hero: {
          title: 'UNLIMITED CODING EXERCISES',
          body: [
            'Finding your next role in tech and sharpening your coding skill belong in the same place. That is why we built one — algorithm and SQL problems that follow current hiring trends, open to practise as often as you like.',
            'Practice is worth more than right or wrong. With analysis from headhunterAI coach, the hours you put into a problem become skill that stays with you.',
          ],
        },
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
          { h: 'Preparing with headhunterAI', rail: true, cards: [
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
        title: ['headhunterAI Career Consulting', 'Your interview result, analysed for you'],
        sections: [
          { h: 'headhunterAI services', rail: true, cards: [
            { h: 'Questions fitted to your résumé', p: 'Questions are grounded in the experience and projects you confirmed, and vague answers get a follow-up asking for specifics.' },
            { h: 'Quantitative report', p: 'Your AI interview score and five item scores (expertise · logic · relevance · delivery · fit) on a 100-point scale, on one page.' },
            { h: 'AI coach comments', p: 'Highlights your strongest and weakest items and suggests 2–3 practice tasks before your next interview, based only on finalised scores.' },
            { h: 'Role-based mock interviews', p: 'Pick AI/AX engineer, backend developer or data analyst and practise the way the real interview works. (Coming soon)' },
          ] },
          { h: 'Preparing with headhunterAI', rail: true, cards: [
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
          { h: 'headhunterAI services', rail: true, cards: [
            { h: 'AI interviews fitted to your post', p: '8 questions: 2 personality · 3 résumé · 2 role · 1 company. Every question is retrieved from our question bank using the job post and résumé, then rewritten to fit your company intro, duties and the applicant\'s résumé.' },
            { h: 'Evidence-based evaluation', p: 'The AI gives 0–5 scores per item with quotes taken verbatim from answers. Totals and pass/fail use the weights and pass mark set on the post.' },
            { h: 'Coding test', p: 'Turn it on per post and set its weight. A basic Python test is available today; custom tests by type and language are coming soon.' },
            { h: 'Choose how decisions are made', p: "Automatic: the AI decision is final right away. Semi-automatic: a recruiter finalises it with one click on 'Approve' or 'Change decision'. Scores never change." },
          ] },
          { h: 'Preparing with headhunterAI', rail: true, cards: [
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
    aboutEyebrow: 'Inspired by unicorns',
    aboutHeadline: 'Great potential deserves to be seen.',
    aboutIntro: [
      'We’re inspired by the spirit of unicorn companies — startups that grew from a handful of people into something that reshaped an entire industry: **small teams with bold ideas**, the courage to challenge familiar systems, and the ambition to create something better.',
      'That spirit shapes headhunterAI — helping companies **look beyond familiar profiles** and giving candidates a clearer opportunity to show what they can do.',
    ],
    aboutBand: [
      '**headhunterAI** rewrites how companies\nfind and hire talent.',
      '**headhunterAI coach** stands behind\nthe people who will become that talent.',
    ],
    aboutPricingCta: 'See headhunterAI pricing',
    aboutBeliefH: 'What that belief means for hiring',
    aboutBelief: [
      'The first round is where potential gets missed. For a lean team it is still manual work, and employers often put the cost of an unfilled role at three to four times its salary — so the pressure is to skim.',
      'Under that pressure human judgment is less steady than it looks. In a field experiment with 3,296 U.S. tech applicants (Avery, 2026), professional recruiters’ scores drifted with the time of day, were anchored by the first answer, and clustered toward the middle. AI scoring of the same interviews showed none of those patterns. The same study is also a warning: one-way asynchronous interviews cut application continuation by about 53%, strongest applicants included, because the process read as unfair.',
      'So we built for both findings. Every applicant gets the same question structure and the same criteria, with three of the eight questions drawn from their own résumé. The rules, the follow-up limit and the answer time are shown before anyone starts. Reducing drop-off is what we are aiming for, not something we have measured yet.',
    ],
    aboutPracticeH: 'Our principles',
    aboutPractice: [
      { h: 'The same questions, the same criteria', p: 'Every applicant answers the same eight-question structure and is scored against the same rubric. A result does not depend on when or in what order it was reviewed.' },
      { h: 'Results you can trace', p: 'The AI returns per-item scores and quotes taken directly from the answer. Totals and pass/fail come from fixed rules in code, not from the model.' },
      { h: 'The company still decides', p: 'Each posting chooses whether results publish as soon as analysis finishes or wait for a recruiter to confirm. Applicants can request a per-item report by email.' },
    ],
    contactTitle: ['Contact', 'Founder of headhunterAI'],
    contactLead: 'For questions about the service, partnerships or hiring, email me directly.',
    contactName: 'Sohyun Kim',
    contactRole: 'Founder of headhunterAI',
    contactEmailLabel: 'Email',
    contactFoot: 'For questions or concerns, please reach out by email.',
    sourcesTitle: ['What this site', 'cites'],
    sourcesLead: 'Where the figures and research findings on the About page come from.',
    sources: [
      {
        h: 'Primary study — field experiment on asynchronous interviews and AI assessment',
        cite: 'Avery, M., Ip, E., Leibbrandt, A., Vecci, J. (2026). "A Brave New World of Hiring: A Natural Field Experiment on How Asynchronous Interviews and AI Assessment Reshape Recruitment." CESifo Working Paper No. 12573 (SSRN 6482825).',
        note: 'A field experiment with 3,296 U.S. tech job applicants. It is the source for the following points on the About page: professional recruiters\' scores shifted with the time of day, were anchored by the answer to the first question and were compressed toward the middle; when AI evaluated the same interviews those patterns did not appear, and its scores predicted job success 12 months later at least twice as well as human scores or CV-based metrics; one-way asynchronous interviews cut application continuation by about 53%. These are findings of that study, not measured outcomes of headhunterAI.',
      },
      {
        h: 'Employer AI adoption and vacancy cost figures',
        cite: 'Brookings (2025) · Fuller et al. (2021) · Navarra (2022).',
        note: 'The source for "more than half of U.S. firms, rising to 94–98% among Fortune 500 companies" and for "filling a vacancy costs three to four times the position\'s salary". These are cited in the Avery et al. (2026) paper above; which figure comes from which work follows that paper\'s attribution.',
      },
    ],
    aboutTechH: 'Our system',
    aboutTech: [
      { ic: 'L', h: 'Self-hosted language model', p: 'Question generation, evaluation and speech-to-text run on an open model (Qwen3) operated on our own server.' },
      { ic: 'O', h: 'Résumé recognition', p: 'PDF text extraction with Tesseract OCR for Korean and English, in the same environment.' },
      { ic: 'S', h: 'Sandboxed code execution', p: 'Coding-test code runs in a separate container under time, memory and concurrency limits.' },
    ],
    aboutTechInbound: 'Applicant’s resume',
    aboutTechResult: 'Applicant answers, video and code stay on our servers',
    homeFeaturesH: 'Features',
    homeDemos: [
      { label: 'Applicant dashboard',
        desc: 'Your applications, interview progress and result notices in one place.',
        cta: 'Browse open roles', href: '/applicant/#/jobs', img: '/images/demo_dashboard.png', rail: true },
      { label: 'Unlimited coding exercises',
        desc: 'Algorithm and SQL problems that follow current hiring trends, with no limit on how often. Run the public examples, then get graded on hidden tests.',
        cta: 'See the practice screen', href: '#/features/coding-practice', img: '/images/demo_coding.png' },
      { label: 'Qualitative report, only on headhunterAI',
        desc: 'Not just a score. Every question quotes your own answer back and explains, in sentences, why each of the five items scored the way it did.',
        cta: 'See career consulting', href: '#/features/career-consulting', shots: ['/images/demo_report_1.png', '/images/demo_report_2.png', '/images/demo_report_3.png'] },
    ],
    // Pricing 히어로 (2026-11-19 사용자 지시): 유리 박스 'Subscription' → 흰 제목 2줄, 끝의 'is now.'만 네온
    pricingEyebrow: 'Subscription',
    pricingHead: ['The best time to bring headhunterAI', 'to your system'],
    pricingHeadAccent: 'is now.',
    segApplicant: 'Applicants', segCompany: 'Companies',
    perMonth: '/mo', free: 'Free',
    plans: {
      applicant: [
        { name: 'Basic', tag: 'FREE', desc: 'Apply to job posts and take the AI first-round interview.', price: 0, sub: 'Up to one use a week',
          items: ['Apply · AI first-round interview', 'Résumé recognition', 'Results (pass/fail · strengths · improvements)'], cta: 'Start for free', href: '/applicant/#/jobs' },
        { name: 'Advanced', tag: 'RECOMMENDED', rec: true, desc: 'Practise IT mock interviews and coding before the real thing.', price: 9900, sub: 'Two or more uses a week',
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
  },
};

// 드롭다운 카드: 한국어(흰색) + 영어(작게, 네온)를 항상 함께 표시 (PRD WEB-03)
// 드롭다운 두 섹션(소제목은 영문 대문자 그대로, 클릭 없음). 섹션마다 왼쪽·오른쪽 카드 2개
const SERVICE_SECTIONS = [
  { title: 'APPLICANTS', items: [
    { id: 'coding-practice', ko: '무제한 코딩 연습', en: 'Unlimited Coding Practice' },
    { id: 'career-consulting', ko: 'headhunter 취업 컨설팅', en: 'headhunter Career Consulting' } ] },
  { title: 'RECRUITERS', items: [
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
  </div>
  <!-- 히어로 아래로 내리면 브랜드 소개 → Features (2026-11-19 사용자 지시) -->
  <div class="ab hm-brand">${brandBlock(L, bandItems(L), 'h2')}</div>
  <section class="hm-feat">
    <h2 class="hm-feat-h">${esc(L.homeFeaturesH)}</h2>
    ${L.homeDemos.map((d, i) => `
      <div class="hm-demo${i % 2 ? ' rev' : ''}">
        <div class="hm-media${d.shots ? ' shots' : ''}" data-shots="${d.shots ? d.shots.length : 0}">
          ${d.shots
            ? d.shots.map((src, k) => `<img class="hm-media-el hm-shot${k ? '' : ' on'}" src="${src}" alt="${esc(d.label)} ${k + 1}">`).join('')
              + `<button type="button" class="hm-arrow prev" aria-label="이전">‹</button>
                 <button type="button" class="hm-arrow next" aria-label="다음">›</button>
                 <div class="hm-dots">${d.shots.map((_, k) => `<span class="${k ? '' : 'on'}"></span>`).join('')}</div>`
            : d.video ? `<video class="hm-media-el" src="${d.video}" autoplay muted loop playsinline
                 onerror="this.closest('.hm-media').classList.add('no-media')"></video>`
            : `<img class="hm-media-el" src="${d.img}" alt="${esc(d.label)}"
                 onerror="this.closest('.hm-media').classList.add('no-media')">`}
          <div class="hm-media-fall"><span>${esc(d.label)}</span></div>
        </div>
        <div class="hm-side">
          <p class="hm-label">${esc(d.label)}</p>
          <p class="hm-desc">${esc(d.desc)}</p>
          ${d.rail ? '<div class="hm-rail" id="hm-jobs"><div class="hm-rail-track"></div></div>' : ''}
          <a class="btn" href="${d.href}">${esc(d.cta)}</a>
        </div>
      </div>`).join('')}
  </section>`;
}

/* 지원자 대시보드 옆에서 공고가 계속 흘러가게 — 실제 공개 공고(GET /api/jobs)만 쓴다.
   목록이 비거나 요청이 실패하면 그 칸만 숨긴다(가짜 공고를 만들지 않는다). */
/* 리포트 스샷 캐러셀 — 좌우 화살표로 넘긴다 (2026-11-19 사용자 지시).
   키보드(←·→)와 점 표시도 같이 둔다. 자동 재생은 하지 않는다. */
function bindShots() {
  document.querySelectorAll('.hm-media.shots').forEach((box) => {
    const shots = [...box.querySelectorAll('.hm-shot')];
    const dots = [...box.querySelectorAll('.hm-dots span')];
    if (shots.length < 2) return;
    let i = 0;
    const go = (n) => {
      i = (n + shots.length) % shots.length;
      shots.forEach((el, k) => el.classList.toggle('on', k === i));
      dots.forEach((el, k) => el.classList.toggle('on', k === i));
    };
    box.querySelector('.prev').onclick = () => go(i - 1);
    box.querySelector('.next').onclick = () => go(i + 1);
    dots.forEach((el, k) => (el.onclick = () => go(k)));
    box.tabIndex = 0;
    box.onkeydown = (e) => {
      if (e.key === 'ArrowLeft') { e.preventDefault(); go(i - 1); }
      if (e.key === 'ArrowRight') { e.preventDefault(); go(i + 1); }
    };
  });
}

async function fillJobRail() {
  const rail = document.getElementById('hm-jobs');
  if (!rail) return;
  let rows = [];
  try { rows = await fetch('/api/jobs').then((r) => (r.ok ? r.json() : [])); } catch (_) { rows = []; }
  const open = rows.filter((r) => r.status === 'published').slice(0, 8);
  if (!open.length) { rail.remove(); return; }
  const card = (j) => `<div class="hm-job"><b>${esc(j.title)}</b><span>${esc(j.company)} · ${esc(j.location || '')}</span></div>`;
  // 같은 목록을 두 벌 이어 붙여야 위로 흐르다 끊기지 않고 이어진다
  rail.querySelector('.hm-rail-track').innerHTML = open.map(card).join('') + open.map(card).join('');
  rail.style.setProperty('--rail-n', String(open.length));
}

/* 서비스 소개 페이지 상단 — 왼쪽 제목·본문 / 오른쪽 연습 바로가기 버튼(2026-11-19 사용자 지시).
   hero가 있는 서비스는 아래쪽 '지금 제공 범위' 문구(status)와 가운데 바로가기 줄을 쓰지 않는다. */
function serviceHero(h, launch) {
  return `<section class="svc-hero">
    <div class="svc-hero-copy">
      <h1 class="svc-hero-title">${esc(h.title)}</h1>
      ${h.body.map((x) => `<p>${esc(x)}</p>`).join('')}
    </div>
    ${launch ? `<div class="svc-hero-side">
      <button type="button" class="btn lg launch-btn" id="launch-practice">${esc(launch.label)}</button>
    </div>` : ''}
  </section>`;
}

function head(title, lead) {
  return `<section class="page-head"><h1>${esc(title[0])}<br><span class="neon">${esc(title[1])}</span></h1>${lead ? `<p>${esc(lead)}</p>` : ''}</section>`;
}

/* rail=true면 카드를 가로로 이어 붙여 좌우로 스크롤해 보게 한다 (2026-11-19 사용자 지시) */
function cards(list, rail) {
  return `<div class="cards${rail ? ' rail' : ''}">${list.map(c => `
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
  if (S.hero) {
    return `<div class="wrap">${serviceHero(S.hero, S.launch)}
      ${S.sections.map(sec => `<section class="sec"><h2>${esc(sec.h)}</h2>${cards(sec.cards, sec.rail)}</section>`).join('')}
      <div class="row" style="justify-content:center;margin:28px 0 8px">${S.ctas.map(c => `<a class="btn lg${c.primary ? ' primary' : ''}" href="${c.href}">${esc(c.label)}</a>`).join('')}</div>
    </div>`;
  }
  return `<div class="wrap">${head(S.title, S.lead)}
    ${S.status ? `<p class="note" style="margin:-16px 0 8px">${esc(S.status)}</p>` : ''}
    ${S.launch ? `<div class="row" style="justify-content:center;margin:20px 0 8px"><button type="button" class="btn lg launch-btn" id="launch-practice">${esc(S.launch.label)}</button></div>` : ''}
    ${S.sections.map(sec => `<section class="sec"><h2>${esc(sec.h)}</h2>${cards(sec.cards, sec.rail)}</section>`).join('')}
    <div class="row" style="justify-content:center;margin:28px 0 8px">${S.ctas.map(c => `<a class="btn lg${c.primary ? ' primary' : ''}" href="${c.href}">${esc(c.label)}</a>`).join('')}</div>
  </div>`;
}

/* About 페이지 (2026-11-19 사용자 지시로 전면 재구성)
   ① 상단 = 유니콘 중심의 '하나의' 중앙 구성 (좌우 2열 히어로 아님)
   ② 그 믿음이 채용에서 뜻하는 것 — 옛 '만든 이유'+'만들고 싶은 변화'를 통합해 다시 씀
   ③ 제품으로 구현한 방식 — 같은 크기 3항목 가로(모바일은 세로)
   ④ 기술 구성 — 본문 컨테이너 폭에 맞춤
   읽는 글은 가운데 둔 읽기 폭(640~760px) 안에서 왼쪽 정렬한다. */
/* 본문 안 **…** 만 굵게(600). 이스케이프한 뒤 바꾸므로 HTML 주입 위험 없음 */
const emph = (x) => esc(x).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');

/* 브랜드 소개(라벨 → 헤드라인 → 인용 상자 → 유니콘 밴드).
   About 페이지와 첫 페이지가 같이 쓴다. 첫 페이지에서는 히어로에 h1이 이미 있으므로 h2로 쓴다. */
function brandBlock(L, band, tag) {
  const head = Array.isArray(L.aboutHeadline) ? L.aboutHeadline.map(esc).join('<br>') : esc(L.aboutHeadline);
  return `<header class="ab-hero ab-col">
      <p class="ab-eyebrow">${esc(L.aboutEyebrow)}</p>
      <${tag} class="ab-headline">${head}</${tag}>
      <div class="ab-intro">${L.aboutIntro.map((x) => `<p>${emph(x)}</p>`).join('')}</div>
    </header>
    <section class="ab-band">
      <div class="ab-col ab-band-inner">
        <div class="ab-band-copy">${band}</div>
        <img class="ab-band-art" src="/images/about_unicorn_hero.png?v=2" alt="" aria-hidden="true">
      </div>
    </section>`;
}

function bandItems(L) {
  return L.aboutBand.map((x, i) => `
    <div class="ab-band-item"><span class="ab-band-no">0${i + 1}</span>
      <p>${emph(x).replace(/\n/g, '<br>')}</p></div>`).join('');
}

function about() {
  const L = t();
  const band = bandItems(L);
  return `<div class="ab">
    ${brandBlock(L, band, 'h1')}
    <section class="ab-sec ab-col">
      <h2>${esc(L.aboutBeliefH)}</h2>
      <div class="ab-read">${L.aboutBelief.map((x) => `<p>${esc(x)}</p>`).join('')}</div>
    </section>
    <section class="ab-sec ab-col">
      <h2>${esc(L.aboutPracticeH)}</h2>
      <div class="ab-three">${L.aboutPractice.map((c) => `
        <div class="ab-item"><h3>${esc(c.h)}</h3><p>${esc(c.p)}</p></div>`).join('')}</div>
    </section>
    <section class="ab-sec ab-col">
      <h2>${esc(L.aboutTechH)}</h2>
      ${perimeterDiagram(L.aboutTech, L.aboutTechInbound, L.aboutTechResult)}
    </section>
    <div class="ab-col ab-cta"><a class="btn primary lg" href="#/pricing">${esc(L.aboutPricingCta)}</a></div>
  </div>`;
}

/* 대표자 연락처 (#/contact) — 맨 아래 '대표자 연락처' 링크에서 들어온다 (2026-11-19 사용자 지시) */
const CONTACT_EMAIL = 'lsk99013@gmail.com';
function contact() {
  const L = t();
  return `<div class="wrap">${head(L.contactTitle, L.contactLead)}
    <div class="contact-card">
      <p class="contact-name">${esc(L.contactName)}</p>
      <p class="contact-role">${esc(L.contactRole)}</p>
      <p class="contact-mail"><span>${esc(L.contactEmailLabel)}</span><a href="mailto:${CONTACT_EMAIL}">${CONTACT_EMAIL}</a></p>
      <p class="contact-foot">${esc(L.contactFoot)}</p>
    </div>
  </div>`;
}

/* 인용 모음 (#/sources). About 페이지 각주로 있던 출처를 맨 아래 'source' 링크 한 곳으로 모았다 (2026-11-19 사용자 지시) */
function sources() {
  const L = t();
  return `<div class="wrap">${head(L.sourcesTitle, L.sourcesLead)}
    ${L.sources.map((s) => `<section class="sec"><div style="max-width:760px;margin:0 auto">
      <h2>${esc(s.h)}</h2>
      <p style="color:var(--white);font-size:15px;line-height:1.7;margin:0 0 12px">${esc(s.cite)}</p>
      <p style="color:var(--muted);font-size:14.5px;line-height:1.75;margin:0">${esc(s.note)}</p>
    </div></section>`).join('')}
  </div>`;
}

function pricing() {
  const L = t();
  const plans = L.plans[audience];
  return `<div class="wrap pr-wrap">
    <section class="pr-hero">
      <p class="pr-eyebrow"><span>${esc(L.pricingEyebrow)}</span></p>
      <h1 class="pr-title"><span class="pr-ln">${esc(L.pricingHead[0])}</span> <span class="pr-ln">${esc(L.pricingHead[1])} <span class="pr-accent">${esc(L.pricingHeadAccent)}</span></span></h1>
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
  </div>`;
}

// Features는 드롭다운만 있고 목록 페이지는 없다. #/features/<id> = 서비스 페이지 4개
const ROUTES = { '': home, about, pricing, sources, contact };

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
  fillJobRail();
  bindShots();
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
