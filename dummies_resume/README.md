# dummies_resume — 지원자 테스트용 더미 이력서

- 2026-11-20 신설(사용자 지시). 지원자 화면 테스트(이력서 업로드 → OCR·구조화 → 확인 → 맞춤 질문)에 쓰는 **가상 이력서**만 둔다.
- 모든 이름·학교·회사·경력·수치는 가상 설정이다. 실제 지원자 데이터·실존 인물 정보를 넣지 않는다.
- **납품은 PDF**(사용자 지시 2026-11-20 "md로 주지 말고 이제부터 pdf로"): 직무 폴더에는 PDF만 둔다.
  - `AI_AX/` — AI/AX 엔지니어 (`01_한도윤_제조AIAX.pdf`)
- 원본 MD는 `_src/<직무>/`(양식 `_src/AI_AX/_양식_template.md`, 공통 스타일 `_src/_resume_style.html`).
  빌드: `python tools/build_pdf.py dummies_resume/_src/<직무>/<이름>.md dummies_resume/<직무>/<이름>.pdf`
- 담당 팀원: `resume`(정의 `.claude/agents/resume.md`).
