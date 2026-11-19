/* 지원자·기업 담당자 화면 영문 사전 (2026-11-19 사용자 지시).
   키 = 한국어 원문. 여기에 없는 말은 한국어 그대로 나간다(화면이 깨지지 않게).
   범위: 채용 공고 화면 · 로그인/회원가입 화면 — 사용자가 지정한 두 곳. */
HH.addDict({
  /* 공통 */
  '로그인': 'Sign in', '회원가입': 'Sign up', '로그아웃': 'Sign out', '계정관리': 'Account',
  '이메일': 'Email', '비밀번호': 'Password', '이름': 'Name', '휴대폰 번호': 'Mobile number',
  '이전': 'Back', '다음': 'Next', '취소': 'Cancel', '확인': 'OK', '닫기': 'Close',
  '님': '', '개': '', '명': '',

  /* 채용 공고 — 직무 고르기 */
  '채용 공고': 'Open roles',
  'headhunterAI는 IT 직무에 특화된 기업 1차 면접 대행 AI 면접 서비스입니다. 지원자님께 맞는 공고를 아래에서 확인해 보세요.':
    'headhunterAI runs first-round interviews for tech roles on behalf of employers. Find the roles that fit you below.',
  '마이페이지로 바로가기': 'Go to My Page',
  '무제한 코딩 연습 바로가기': 'Unlimited coding practice',
  '지원 가능': 'Accepting applications', '준비 중': 'Coming soon',
  '접수 중 공고 {n}개': '{n} open posting(s)',
  '공고 {n}개 · 질문 은행 준비 중': '{n} posting(s) · interview questions coming soon',
  '질문 은행 준비 중': 'Interview questions coming soon',

  /* 채용 공고 — 직무 안 */
  '직무 선택': 'Choose a role',
  '열려 있는 공고를 둘러보거나, 지원한 공고의 진행 상황을 확인하세요.':
    'Browse the open postings, or check where your applications stand.',
  '공고는 둘러볼 수 있으나, 이 직무의 AI 면접은 아직 준비 중이라 지원할 수 없습니다.':
    'You can browse these postings, but the AI interview for this role is not ready yet, so applications are closed.',
  '현재 열려 있는 공고 둘러보기': 'Browse open postings',
  '기업을 고른 뒤 그 기업의 공고를 봅니다. 로그인 없이 볼 수 있습니다.':
    'Pick a company, then see its postings. No sign-in needed.',
  '내가 지원한 공고': 'My applications',
  '지원한 공고의 진행 상태와 응시할 면접을 확인합니다. 로그인이 필요합니다.':
    'See where each application stands and which interview is waiting. Sign-in required.',
  '{name} 님이 지원한 공고입니다.': 'Applications submitted by {name}.',

  /* 채용 공고 — 기업·목록 */
  '기업 선택': 'Choose a company',
  '공고를 올린 기업입니다. 기업을 고르면 그 기업의 공고를 모두 볼 수 있습니다.':
    'Companies hiring through headhunterAI. Pick one to see all of its postings.',
  '공고를 올린 기업이 없습니다.': 'No companies are hiring right now.',
  '지원 가능 {n}개': '{n} open', '공고 {n}개': '{n} posting(s)',
  '이 기업의 공고 {n}개입니다. 준비 중인 직무의 공고는 볼 수만 있고 지원할 수 없습니다.':
    '{n} posting(s) from this company. Postings for roles still in preparation can be viewed but not applied to.',

  /* 공고 카드·상세 */
  '마감': 'Closed', '접수 중': 'Open',
  '준비 중 · 지원 불가': 'Coming soon · not accepting',
  '코딩테스트 포함': 'Includes coding test', 'AI 면접만': 'AI interview only',
  '접수 마감': 'Closes', '결과 공개': 'Results',
  '접수 기간': 'Application window',
  '진행 방식 · 예상 소요': 'Format · estimated time',
  '1차 결과 공개': 'First-round results',
  'AI 면접': 'AI interview', '+ 코딩테스트': '+ coding test',
  '약 {n}분': 'about {n} min',
  '모집 {n}명': '{n} opening(s)',
  '지원하기': 'Apply',
  '지금 {n}명이 보고 있어요': '{n} people viewing now',

  /* 로그인 · 회원가입 (지원자) */
  '지원자 로그인': 'Applicant sign-in',
  '지원자로 회원가입하기': 'Create an applicant account',
  '기업 담당자 로그인': 'Recruiter sign-in',
  '지원자이신가요?': 'Are you an applicant?',
  '계정이 없으신가요?': 'No account yet?',
  '비밀번호 보기': 'Show password',

  /* 로그인 (기업 담당자) */
  '기업 담당자 계정은 운영 측에서 사전 발급합니다.':
    'Recruiter accounts are issued by the headhunterAI team.',
});

HH.addDict({
  /* 직무 이름 */
  'AI/AX 엔지니어': 'AI/AX Engineer', '백엔드 개발자': 'Backend Engineer',
  '프론트엔드 개발자': 'Frontend Engineer', '데이터 엔지니어': 'Data Engineer',
  '데이터 사이언티스트·분석가': 'Data Scientist / Analyst',
  'DevOps·클라우드 엔지니어': 'DevOps / Cloud Engineer', '모바일 앱 개발자': 'Mobile App Engineer',

  /* 공고 상세 */
  '회사 소개': 'About the company', '주요 업무': 'What you will do',
  '자격 요건': 'Requirements', '우대 사항': 'Nice to have',
  '지원 후 이력서를 올리면 AI가 내용을 읽어 맞춤 질문 2개를 만듭니다. AI 면접은 1회만 응시할 수 있고 중단 버튼이 없습니다.':
    'After you apply, upload your resume and the AI will draft 2 questions from it. The AI interview can be taken once, and there is no pause button.',
  '접수 마감됨': 'Applications closed',
  '준비 중인 직무 · 지원 불가': 'Role in preparation · not accepting',
  '{company} 공고': '{company} postings',

  /* 지원자 로그인 */
  '로그인 후 지원 절차로 돌아갑니다.': 'You will return to where you left off after signing in.',
  '아직 회원이 아니신가요?': 'New here?',
  '기업 담당자는 {link}을 이용해 주세요.': 'Recruiters, please use {link}.',

  /* 지원자 회원가입 */
  '지원자 회원가입': 'Create an applicant account',
  '평가와 무관한 생년월일·성별·주소·사진은 받지 않습니다.':
    'We do not collect date of birth, gender, address or photos — none of it affects evaluation.',
  '이름 *': 'Name *', '이메일 (아이디) *': 'Email (your ID) *',
  '비밀번호 * (8자 이상)': 'Password * (8+ characters)', '휴대전화번호 *': 'Mobile number *',
  '만 14세 이상입니다.': 'I am 14 years of age or older.',
  '(필수)': '(required)', '전문 보기': 'Read in full',
  '이용약관에 동의합니다.': 'I agree to the Terms of Service.',
  '회원 서비스 제공을 위한 개인정보 수집·이용(이름·이메일·비밀번호·휴대전화번호, 탈퇴 시까지 보관)에 동의합니다. 서비스 서버는 미국(Railway)에 있습니다.':
    'I agree to the collection and use of my personal data (name, email, password, mobile number) to provide the service, kept until I close my account. Our servers are located in the United States (Railway).',
  '가입하기': 'Create account',
  '응시 데이터 수집 동의는 공고에 지원할 때 따로 받습니다.':
    'Consent for interview data is requested separately when you apply to a posting.',

  /* 기업 담당자 로그인 */
  '기업 담당자 로그인': 'Recruiter sign-in',
});
