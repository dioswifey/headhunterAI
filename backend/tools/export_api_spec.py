"""API 명세 내보내기: FastAPI 앱에서 OpenAPI JSON과 사람이 읽는 MD를 만든다.

실행 (api 컨테이너 안):
    docker exec headhunterAI-api python -m tools.export_api_spec v0.3
→ /srv/api_spec_out/ 에 생성 → docker cp로 backend/api_spec/ 에 복사
"""
import json
import sys
from pathlib import Path

from fastapi.routing import APIRoute

from app.main import app

AUTH = {"applicant": "지원자", "recruiter": "기업 담당자", "operator": "운영자", "current_user": "로그인", "optional_user": "공개(로그인 선택)"}
GROUPS = [("/api/auth", "인증"), ("/api/dev", "개발 전용 (DEV_MODE=1)"), ("/api/admin", "기업 담당자"), ("/api/ops", "운영자"),
          ("/api/system", "공개"), ("/api/job", "공개"), ("/api/companies", "공개"), ("/api", "지원자")]


# 함수 설명(docstring)이 없는 엔드포인트의 설명 — TRD 4장 요약과 같게 유지
DESC = {
    ("POST", "/api/auth/signup"): "지원자 가입(이름·이메일·비밀번호·휴대전화·만 14세·필수 동의). ACC-02",
    ("POST", "/api/auth/login"): "role=applicant·recruiter. 5회 실패 시 10분 잠금, 계정 존재 여부 비노출. ACC-05",
    ("POST", "/api/auth/logout"): "세션 삭제",
    ("GET", "/api/auth/me"): "현재 사용자(로그인 안 했으면 null)",
    ("POST", "/api/auth/change-password"): "초기 비밀번호 변경(담당자는 변경 전 다른 API 403). ACC-03",
    ("GET", "/api/job-roles"): "직무 목록(준비 중 포함)",
    ("GET", "/api/jobs"): "게시·마감 공고 목록(직무·기업·검색어 필터, role_status·company_id 포함). APP-01, APP-10",
    ("GET", "/api/jobs/{job_id}"): "공고 상세·예상 소요·코딩테스트 여부",
    ("POST", "/api/applications"): "지원 생성(이미 있으면 기존 건 반환). APP-02",
    ("GET", "/api/applications/{app_id}"): "지원 단계·공고·응시 상태",
    ("POST", "/api/applications/{app_id}/attempt"): "응시 시작: 동시 응시 상한, 평가 기준 잠금, 질문 스냅샷 생성. INT-08, JOB-07",
    ("POST", "/api/applications/{app_id}/consent"): "5개 항목 동의 기록(버전). APP-03",
    ("POST", "/api/applications/{app_id}/env"): "환경 점검 5항목 통과 기록 → 질문 사전 생성 시작. APP-06, RSQ-05",
    ("POST", "/api/applications/{app_id}/resume"): "이력서 확인 완료 스냅샷 저장, 자동값 대비 수정 필드 계산. APP-05, OCR-05",
    ("GET", "/api/attempts/{attempt_id}"): "현재 상태·현재 질문·남은 시간. 재접속 시 장애/임의 종료 판정",
    ("POST", "/api/attempts/{attempt_id}/answer"): "답변 저장 → 꼬리질문 판단 → 다음 질문 또는 코딩/완료 전이. INT-02~07",
    ("GET", "/api/attempts/{attempt_id}/coding"): "코딩 문제·공개 예제·저장 코드·남은 시간. COD-01",
    ("POST", "/api/attempts/{attempt_id}/coding/submit"): "최종 제출 → 응시 완료. COD-04",
    ("POST", "/api/attempts/{attempt_id}/coding/{problem_id}/run"): "공개 예제로 실행(샌드박스). COD-05",
    ("POST", "/api/attempts/{attempt_id}/coding/{problem_id}/save"): "코드 자동 저장",
    ("POST", "/api/attempts/{attempt_id}/events"): "exit · user_exit · paste_block · offline 이벤트. 5.2, RES-04, COD-07",
    ("POST", "/api/attempts/{attempt_id}/heartbeat"): "5초 주기 생존 신호. 5.1",
    ("GET", "/api/me/applications"): "내 지원 현황·응시·분석 상태·결과 공개 여부·결정 방식",
    ("GET", "/api/me/notifications"): "사이트 내 알림",
    ("POST", "/api/me/notifications/read"): "알림 모두 읽음",
    ("GET", "/api/me/resumes"): "마이페이지 등록 이력서 목록. APP-04",
    ("POST", "/api/me/resumes"): "이력서 저장",
    ("DELETE", "/api/me/resumes/{rid}"): "이력서 삭제",
    ("GET", "/api/resume/extractions/{xid}"): "OCR 진행 단계·진행률·쪽별 결과·구조화 결과. OCR-03, 07",
    ("GET", "/api/resume/extractions/{xid}/file"): "업로드 원본 파일",
    ("POST", "/api/resume/uploads"): "이력서 업로드(PDF·JPG·PNG, 10MB) → extraction_id, OCR 시작. OCR-01, 6.2",
    ("GET", "/api/admin/jobs"): "우리 회사 공고 목록",
    ("POST", "/api/admin/jobs"): "공고 생성(차별 소지 표현 경고 반환). JOB-01, 02",
    ("GET", "/api/admin/jobs/{job_id}"): "공고 상세 + 평가 기준",
    ("PUT", "/api/admin/jobs/{job_id}"): "공고 수정",
    ("DELETE", "/api/admin/jobs/{job_id}"): "공고 삭제(지원자 있으면 불가)",
    ("POST", "/api/admin/jobs/{job_id}/publish"): "게시. JOB-01",
    ("POST", "/api/admin/jobs/{job_id}/close"): "마감. JOB-01",
    ("POST", "/api/admin/jobs/draft-ai"): "AI 공고 초안. JOB-02",
    ("PUT", "/api/admin/jobs/{job_id}/criteria"): "가중치·코딩 사용·반영 비중·합격 기준·합불 결정 방식(auto/semi). 잠금 후 409. JOB-03~07, DEC-01",
    ("GET", "/api/admin/applicants"): "지원자 목록(공고·결과 pass/fail/pending·상태 필터). ADM-01, DEC-05",
    ("GET", "/api/admin/applicants/{app_id}"): "한 페이지 리포트: 결과·점수·근거·질문별 답변·꼬리질문·코드·이벤트·이력서·메모. RPT-01, ADM-02",
    ("POST", "/api/admin/applicants/{app_id}/memos"): "내부 메모. ADM-05",
    ("GET", "/api/admin/files/{file_id}"): "이력서 원본(우리 회사 지원 건만). ADM-02",
    ("GET", "/api/admin/notifications"): "담당자 알림. ADM-08",
    ("GET", "/api/admin/stats"): "월별 응시·공고별 합격률(확정 판정 기준)·평균·장애·OCR 집계. ADM-07",
    ("GET", "/api/ops/analysis"): "운영자: 완료되지 않은 분석 작업. EVL-08",
    ("POST", "/api/ops/analysis/{attempt_id}/retry"): "운영자: 분석 재처리(시도 횟수 초기화). EVL-08",
}


def auth_of(route):
    names = []

    def walk(dep):
        for d in dep.dependencies:
            n = getattr(d.call, "__name__", "")
            if n in AUTH:
                names.append(AUTH[n])
            walk(d)
    walk(route.dependant)
    return names[0] if names else "공개"


def group_of(path):
    for prefix, name in GROUPS:
        if path.startswith(prefix):
            return name
    return "기타"


def schema_name(ref):
    return ref.split("/")[-1] if ref else ""


def body_of(route, spec):
    op = spec["paths"][route.path_format][[m.lower() for m in route.methods if m != "HEAD"][0]]
    rb = op.get("requestBody", {}).get("content", {})
    if not rb:
        return ""
    ct, v = next(iter(rb.items()))
    ref = v.get("schema", {}).get("$ref", "")
    if not ref:
        return ct
    sch = spec["components"]["schemas"][schema_name(ref)]
    req = set(sch.get("required", []))
    fields = ", ".join(f"{k}{'' if k in req else '?'}: {p.get('type', p.get('anyOf', [{}])[0].get('type', 'object'))}"
                       for k, p in sch.get("properties", {}).items())
    return f"`{schema_name(ref)}` {{{fields}}}" if ct == "application/json" else f"{ct}: {fields}"


def params_of(route, spec):
    op = spec["paths"][route.path_format][[m.lower() for m in route.methods if m != "HEAD"][0]]
    ps = [f"{p['name']}{'' if p.get('required') else '?'}({p['in'][0]})" for p in op.get("parameters", []) if p["in"] in ("path", "query")]
    return ", ".join(ps)


def main(version):
    spec = app.openapi()
    spec["info"] = {"title": "headhunterAI API", "version": version,
                    "description": "headhunterAI 개인 프로젝트 API. 인증: HttpOnly 세션 쿠키 hh_session. 기준 문서 TRD " + version + " 4장."}
    out = Path("/srv/api_spec_out")
    out.mkdir(exist_ok=True)
    (out / f"openapi_{version}.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")

    rows = {}
    for r in app.routes:
        if not isinstance(r, APIRoute) or not r.path.startswith("/api"):
            continue
        for m in sorted(r.methods - {"HEAD"}):
            doc = DESC.get((m, r.path)) or (r.endpoint.__doc__ or "").strip().split("\n")[0]
            rows.setdefault(group_of(r.path), []).append((r.path, m, auth_of(r), params_of(r, spec), body_of(r, spec), doc))
    order = ["인증", "공개", "지원자", "기업 담당자", "운영자", "개발 전용 (DEV_MODE=1)", "기타"]
    n = sum(len(v) for v in rows.values())
    md = [f"# headhunterAI API 명세 {version}", "",
          f"- 엔드포인트 {n}개 · 원본 `openapi_{version}.json`(OpenAPI 3) — Swagger UI는 서버 실행 중 `http://localhost:8000/docs`",
          "- 기준 경로 `/api`, 요청·응답 JSON(업로드만 multipart). 인증은 HttpOnly 세션 쿠키 `hh_session`(로그인 시 발급, 2시간 연장)",
          "- 오류 응답: `{\"detail\": \"한국어 메시지\"}` · 401 로그인 필요 · 403 권한 없음 · 404 없음(다른 회사 데이터 포함) · 409 상태 충돌 · 429 횟수 제한 · 503 설정·용량 부족",
          "- 권한: 담당자 API는 항상 세션 사용자의 회사 범위로 제한, 지원자 API는 본인 데이터만",
          "- 설명은 코드의 함수 설명(docstring) 첫 줄. 정책 설명은 TRD 4장·PRD 4장", ""]
    for g in order:
        if g not in rows:
            continue
        md += [f"## {g}", "", "| 메서드 | 경로 | 권한 | 파라미터 | 요청 본문 | 설명 |", "|---|---|---|---|---|---|"]
        for path, m, auth, ps, body, doc in sorted(rows[g], key=lambda x: (x[0], x[1])):
            md.append(f"| {m} | `{path}` | {auth} | {ps or '-'} | {body or '-'} | {doc.replace('|', '/') or '-'} |")
        md.append("")
    md += ["## 정적 화면 (API 밖)", "", "| 경로 | 내용 |", "|---|---|",
           "| `/` · `/main.html` | 공통 첫 페이지 (`#/features` · `#/about` · `#/pricing`) |",
           "| `/applicant/` · `/admin/` | 지원자 · 기업 담당자 화면 (해시 라우팅) |", ""]
    (out / f"headhunterAI_API_spec_{version}.md").write_text("\n".join(md), encoding="utf-8")
    print(n, "endpoints")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "v0.3")
