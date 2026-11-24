"""QA 에이전트의 컨테이너 안 검사 모음 — `tools/qa_agent.py`가 api 컨테이너에 복사해 실행한다(직접 실행해도 됨).

    docker cp tools/qa_checks.py headhunterAI-api:/srv/qa_checks.py
    docker exec -w /srv headhunterAI-api python qa_checks.py [TC ID …]

한 줄에 하나씩 JSON 결과: {"id", "status": 통과|실패|보류, "actual", "ms"}.
외부 AI API(OpenAI)는 부르지 않는다(생성 품질 지표는 DB에 저장된 결과로 계산).
로컬 모델(임베딩·LLM·음성)도 부르지 않는다 — 예외는 GPU_CHECKS 4건(UT-35·UT-36·IT-37·IT-38)뿐이고,
QA_NO_GPU=1(`qa_agent.py run --no-gpu`)이면 이 4건은 실행하지 않고 '보류'로 남긴다.
테스트가 남기는 데이터(조회 인원 키 등)는 끝에서 지운다.
"""
import json
import sys
import time
import traceback

import httpx

from app import db

db.pool.open()
BASE = "http://localhost:8000"
CHECKS = {}


def check(tc):
    def deco(fn):
        CHECKS[tc] = fn
        return fn
    return deco


# ---------------- 회귀: 기존 단위 테스트 중 순수 함수 ----------------
@check("UT-03")
def ut03():
    from app.evaluation import quote_ok
    a = "저는 RAG 검색 품질을 먼저 측정했습니다. 그다음 프롬프트를 고쳤습니다."
    r = (quote_ok("RAG 검색 품질을   먼저 측정했습니다", a), quote_ok("없는 문장입니다", a), quote_ok("저는", a))
    return r == (True, False, False), f"공백 차이 인용 {r[0]} / 없는 문장 {r[1]} / 4자 미만 {r[2]}"


@check("UT-07")
def ut07():
    from app.security import hash_pw, verify_pw
    h = hash_pw("Test1234!")
    r = (h.startswith("$argon2"), verify_pw(h, "Test1234!"), verify_pw(h, "test1234!"))
    return r == (True, True, False), f"argon2 {r[0]} / 같은 값 {r[1]} / 대소문자 다름 {r[2]}"


@check("UT-13")
def ut13():
    from app.agents.translation_agent import normalize
    out = normalize({"q": "올바른  chunk 를 검색했지만 RAG 가 무시하면?"}, "ko")["q"]
    return out == "올바른 chunk를 검색했지만 RAG가 무시하면?", out


# ---------------- 코딩 문제은행: 검증 에이전트 A11 (모델 호출 없음) ----------------
GOOD = {
    "title": "가장 긴 오름 구간", "func_name": "longest_rise", "params": ["nums"],
    "statement": "x", "io_spec": "x", "constraints": ["0 <= len(nums) <= 100000", "빈 배열 허용", "중복 허용"],
    "solution": "def longest_rise(nums):\n    best = cur = 0\n    prev = None\n    for v in nums:\n        cur = cur + 1 if prev is not None and v > prev else 1\n        best = max(best, cur)\n        prev = v\n    return best\n",
    "brute": "def longest_rise(nums):\n    n = len(nums)\n    best = 0\n    for i in range(n):\n        for j in range(i, n):\n            if all(nums[k] < nums[k + 1] for k in range(i, j)):\n                best = max(best, j - i + 1)\n    return best\n",
    "validator": "def validate(nums):\n    return isinstance(nums, list) and len(nums) <= 100000 and all(isinstance(v, int) and -10**9 <= v <= 10**9 for v in nums)\n",
    "gen_examples": "def gen_examples():\n    return [[[1, 2, 3, 1, 2]], [[5, 4, 3]], [[1, 3, 5, 7, 2, 4, 6, 8, 10]]]\n",
    "gen_edge": "def gen_edge():\n    return [[[]], [[7]], [[2, 2, 2]], [[1, 2]], [[2, 1]], [[-1000000000, 1000000000]]]\n",
    "gen_random": "import random\ndef gen_random(seed):\n    rng = random.Random(seed)\n    return [[rng.randint(-3, 3) for _ in range(rng.randint(0, 8))]]\n",
    "gen_large": "import random\ndef gen_large(seed):\n    rng = random.Random(seed)\n    return [[rng.randint(-10**9, 10**9) for _ in range(100000)]]\n",
    "wrong_solutions": [
        "# 버그: 같은 값도 오름으로 셈\ndef longest_rise(nums):\n    best = cur = 0\n    prev = None\n    for v in nums:\n        cur = cur + 1 if prev is not None and v >= prev else 1\n        best = max(best, cur)\n        prev = v\n    return best\n",
        "# 버그: 빈 배열에서 1을 반환\ndef longest_rise(nums):\n    best = cur = 1\n    for i in range(1, len(nums)):\n        cur = cur + 1 if nums[i] > nums[i - 1] else 1\n        best = max(best, cur)\n    return best\n"],
}


def _verify(draft):
    from app.agents import coding_verifier_agent as V
    x = {"spec": {}, "history": [], "attempt": 1, "draft": json.loads(json.dumps(draft)), "fail": None}
    x = V.execution_verifier.invoke(x)
    return x.get("fail")


@check("UT-20")
def ut20():
    f = _verify(GOOD)
    return f is None, "정상 초안 V1~V7 통과" if f is None else f"실패: {f}"


@check("UT-21")
def ut21():
    bad = dict(GOOD, solution="def longest_rise(nums):\n    return len(nums)\n", brute="def longest_rise(nums):\n    return len(nums)\n")
    f = _verify(bad) or {}
    return f.get("stage") == "V5_trivial", f"{f.get('stage')}: {str(f.get('reason'))[:90]}"


@check("UT-22")
def ut22():
    weak = dict(GOOD, gen_edge="def gen_edge():\n    return [[[1, 2, 3]], [[3, 2, 1]], [[1, 3, 2]], [[5, 6]]]\n",
                gen_random="import random\ndef gen_random(seed):\n    rng = random.Random(seed)\n    return [[rng.randint(0, 9) for _ in range(rng.randint(1, 8))]]\n")
    f = _verify(weak) or {}
    return f.get("stage") == "V6_wrong", f"{f.get('stage')}: {str(f.get('reason'))[:90]}"


@check("UT-23")
def ut23():
    inval = dict(GOOD, gen_random="import random\ndef gen_random(seed):\n    rng = random.Random(seed)\n    return [[rng.randint(-3, 3) for _ in range(rng.randint(0, 8))] + ['x']]\n")
    f = _verify(inval) or {}
    return f.get("stage") == "V2_inputs", f"{f.get('stage')}: {str(f.get('reason'))[:90]}"


# ---------------- 공고 · 조회 인원 · 범위 필터 · 명세 ----------------
@check("UT-24")
def ut24():
    from app import job_import as J
    sample = ("[테스트] 백엔드 엔지니어\n테스트 회사입니다.\n근무지: 서울\n\n주요 업무\n- API 개발\n- DB 설계\n\n자격 요건\n• Python 3년\n\n"
              "우대 사항\n1. Redis 경험\n\n복지\n- 점심")
    r = J.import_job(sample)["fields"]
    blocked = 0
    for u in ("http://127.0.0.1:8000/", "http://10.0.0.5/", "file:///etc/passwd"):
        try:
            J.import_job("", u)
        except ValueError:
            blocked += 1
    ok = (r["title"] == "[테스트] 백엔드 엔지니어" and r["location"] == "서울" and r["duties"] == "- API 개발\n- DB 설계"
          and r["requirements"] == "- Python 3년" and r["preferred"] == "- Redis 경험" and "근무지" not in r["company_intro"] and blocked == 3)
    return ok, f"칸 분리 {'정상' if ok else r} · 내부·파일 주소 차단 {blocked}/3"


@check("UT-25")
def ut25():
    from app import presence, vectorstore
    jid = 999999
    try:
        one = presence.beat(jid, "10.250.0.1")
        for i in range(2, 10):
            presence.beat(jid, f"10.250.0.{i}")
        nine = presence.beat(jid, "10.250.0.9")
        dup = presence.beat(jid, "10.250.0.9")
        ten = presence.beat(jid, "10.250.0.10")
    finally:
        vectorstore.client().delete(presence.KEY.format(jid))
    ok = (one, nine, dup, ten) == (None, None, None, 10)
    return ok, f"1명 {one} · 9명 {nine} · 같은 IP 재신호 {dup} · 10명 {ten} (10명 미만 숨김)"


@check("UT-26")
def ut26():
    from app.agents.coding_specs import TAXONOMY, framework_specs
    sp = framework_specs(10, seed=11)
    jr = sum(s["difficulty"] == "basic" for s in sp)
    st = json.load(open(TAXONOMY, encoding="utf-8"))   # QA-31: 옛 경로 하드코딩 대신 제품 상수(questionbank/coding_sql_practice/…)
    fams = {k for k, v in st["structure_families"].items() if v >= 10 and k != "예제 없음"}
    ok = len(sp) == 10 and jr == 6 and all(s["structure"] in fams for s in sp)
    return ok, f"명세 {len(sp)}개 · Junior {jr} / Senior {10 - jr} · 구조 모두 10회 이상 나온 구조 {all(s['structure'] in fams for s in sp)}"


@check("UT-27")
def ut27():
    from app.agents.translation_agent import scope
    a = scope({"question": "How do you handle Indian languages in an LLM feature?"})
    b = scope({"question": "How do you debug a slow API endpoint?", "follow_up": "What metric do you check first?"})
    ok = a["decision"] == "drop" and a["by"] == "rule" and b["decision"] == "keep"
    return ok, f"다른 나라 언어 질문 {a['decision']}({a['by']}) · 일반 IT 질문 {b['decision']}"


@check("UT-30")
def ut30():
    from app.agents.coding_quality import eloquence, code_size
    good, _ = eloquence("정수 배열이 주어집니다. 가장 긴 오름 구간의 길이를 반환하세요. 배열이 비어 있으면 0을 반환합니다.")
    bad, d = eloquence("당신은 그것은 배열에 대해서 처리하는 것이다. 당신은 결과를 가지고 있는 것을 반환하는 것이다.")
    cs = code_size("def f(a):\n    for x in a:\n        if x:\n            return x\n    return 0\n")
    ok = good >= 80 and bad < good and d["translationese"] >= 3 and cs == {"lines": 5, "loops": 1, "branches": 1}
    return ok, f"자연스러운 문장 {good}점 · 번역투 문장 {bad}점(번역투 {d['translationese']}개) · 코드 규모 {cs}"


@check("UT-31")
def ut31():
    from app import sqlbank
    a = sqlbank.run("CREATE TABLE T (A INT); INSERT INTO T VALUES (1),(2);", ["SELECT A FROM T ORDER BY A"])
    b = sqlbank.run("", ["SELECT pg_read_file('/etc/passwd')"])
    c = sqlbank.run("", ["COMMIT"])
    d = sqlbank.run("", ["SELECT count(*) FROM users"])
    e = sqlbank.run("", ["SELECT count(*) FROM T"])
    ok = (a["result"] == {"columns": ["A"], "rows": [["1"], ["2"]]} and "허용" in b["error"] and "허용" in c["error"]
          and "does not exist" in d["error"] and "does not exist" in e["error"])
    return ok, f"조회 {a['result']} · pg_ 함수 차단 {bool(b['error'])} · COMMIT 차단 {bool(c['error'])} · 앱 표 접근 {d['error'][:30]} · 실행 후 표 남음 {not e['error']}"


@check("UT-32")
def ut32():
    from collections import Counter
    from app.agents.sql_scope import items, plan
    from app.agents.sql_generator_agent import split_sql
    p = plan(60)
    c = Counter(s["subject"] for s in p)
    every = set(it for _, _, it in items()) <= {s["item"] for s in p}
    parts = split_sql("UPDATE T SET B='a;b' WHERE A=1; SELECT * FROM T ORDER BY A;")
    ok = c[1] == 12 and c[2] == 48 and every and len(parts) == 2
    return ok, f"1과목 {c[1]} · 2과목 {c[2]} · 세부항목 모두 포함 {every} · 문장 나누기 {len(parts)}개"


# ---------------- 통합 ----------------
def _login():
    c = httpx.Client(base_url=BASE, timeout=60)
    r = c.post("/api/auth/login", json={"email": "applicant@headhunter.ai", "password": "test1234!", "role": "applicant"})
    if r.status_code != 200:
        raise RuntimeError(f"테스트 계정 로그인 실패 {r.status_code}")
    return c


@check("IT-23")
def it23():
    c = _login()
    lst = c.get("/api/practice/coding").json()
    g = db.one("SELECT published_problem_id pid, solution FROM generated_problems WHERE published_problem_id IS NOT NULL ORDER BY id DESC LIMIT 1")
    v = c.get(f"/api/practice/coding/{g['pid']}").json()
    run = c.post(f"/api/practice/coding/{g['pid']}/run", json={"code": v["starter"]}).json()
    ok_sub = c.post(f"/api/practice/coding/{g['pid']}/submit", json={"code": g["solution"]}).json()
    bad_sub = c.post(f"/api/practice/coding/{g['pid']}/submit", json={"code": v["starter"]}).json()
    ok = (len(lst) > 0 and run.get("status") == "ok" and ok_sub["passed"] == ok_sub["total"] and bad_sub["passed"] < bad_sub["total"])
    return ok, (f"목록 {len(lst)}문제 · 문제 {g['pid']} 기준 풀이 제출 {ok_sub['passed']}/{ok_sub['total']}"
                f"(숨은 {ok_sub['hidden_passed']}/{ok_sub['hidden_total']}) · 시작 코드 제출 {bad_sub['passed']}/{bad_sub['total']}")


@check("IT-24")
def it24():
    c = _login()
    lst = c.get("/api/practice/coding").json()
    jr, sr = sum(p["level"] == "basic" for p in lst), sum(p["level"] == "applied" for p in lst)
    n = db.one("SELECT count(*) n FROM coding_problems")["n"]
    return jr + sr == n == len(lst), f"Junior {jr} + Senior {sr} = {jr + sr} (게시 문제 {n})"


@check("IT-25")
def it25():
    jobs = httpx.get(BASE + "/api/jobs", timeout=30).json()
    exp = {r["id"]: r["n"] for r in db.all_("""SELECT id, (status='published' AND open_date <= (now() AT TIME ZONE 'Asia/Seoul')::date
                                              AND open_date >= (now() AT TIME ZONE 'Asia/Seoul')::date - 7) n FROM jobs""")}
    ok = all("is_new" in j and "viewers" in j and j["is_new"] == exp[j["id"]] for j in jobs)
    small = all(j["viewers"] is None or j["viewers"] >= 10 for j in jobs)
    return ok and small, f"공고 {len(jobs)}건 is_new 규칙 일치 {ok} · 10명 미만 숫자 노출 없음 {small}"


@check("IT-27")
def it27():
    r = db.one("""SELECT count(*) n, count(*) FILTER (WHERE license_status<>'unverified') changed,
                  count(published_problem_id) published FROM collected_problems""")
    ok = r["published"] == 0 or r["changed"] >= r["published"]
    return ok, f"수집 {r['n']}문항 · 권한 상태 변경 {r['changed']} · 게시 {r['published']} (승인 없이 게시 0)"


@check("IT-28")
def it28():
    r = db.one("""SELECT count(*) FILTER (WHERE published_problem_id IS NOT NULL AND quality_status<>'verified') bad,
                  count(*) FILTER (WHERE quality_status='on_hold' AND published_problem_id IS NOT NULL) held_pub,
                  count(*) FILTER (WHERE origin<>'self_generated') other,
                  count(*) FILTER (WHERE published_problem_id IS NOT NULL) pub FROM generated_problems""")
    ok = r["bad"] == 0 and r["held_pub"] == 0 and r["other"] == 0
    return ok, f"게시 {r['pub']}건 모두 verified {r['bad'] == 0} · 보류 게시 {r['held_pub']} · origin 이상 {r['other']}"


@check("IT-29")
def it29():
    c = _login()
    lst = c.get("/api/practice/sql").json()
    p = db.one("SELECT id, answer FROM sql_problems WHERE status='published' ORDER BY id LIMIT 1")
    v = c.get(f"/api/practice/sql/{p['id']}").json()
    leak = [k for k in ("answer", "hidden", "explanation", "setup_sql") if k in v]
    right = c.post(f"/api/practice/sql/{p['id']}/answer", json={"choice": p["answer"]}).json()
    wrong = c.post(f"/api/practice/sql/{p['id']}/answer", json={"choice": p["answer"] % 4 + 1}).json()
    ok = len(lst) > 0 and not leak and right["correct"] and not wrong["correct"] and bool(right["explanation"])
    return ok, f"목록 {len(lst)}문제 · 문제 응답 노출 {leak or '없음'} · 정답 {right['correct']} · 오답 {wrong['correct']}"


@check("IT-30")
def it30():
    from app import sqlbank
    from app.agents.sql_generator_agent import split_sql
    rows = db.all_("SELECT * FROM sql_problems WHERE status='published'")
    bad = [r["id"] for r in rows if r["quality_status"] != "verified" or len(r["choices"]) != 4 or r["answer"] not in (1, 2, 3, 4)]
    redo = []
    for r in rows:
        if r["kind"] == "result":
            got = sqlbank.run(r["setup_sql"], split_sql(r["query_sql"]))["result"]
            want = {k: r["choices"][r["answer"] - 1][k] for k in ("columns", "rows")}
            if got != want:
                redo.append(r["id"])
    held = db.one("SELECT count(*) n FROM sql_problems WHERE status='on_hold'")["n"]
    return not bad and not redo, f"게시 {len(rows)} · 형식 이상 {bad or 0} · 실행 결과 재계산 불일치 {redo or 0} · 보류 {held}"


@check("IT-31")
def it31():
    c = httpx.Client(base_url=BASE, timeout=30)
    r = c.post("/api/auth/login", json={"email": "recruiter@headhunter.ai", "password": "recruiter99!", "role": "recruiter"})
    if r.status_code != 200:
        raise RuntimeError(f"담당자 로그인 실패 {r.status_code}")
    empty = c.post("/api/admin/applicants/delete", json={"ids": []}).status_code
    none = c.post("/api/admin/applicants/delete", json={"ids": [987654321]}).json()
    return empty == 400 and none.get("deleted") == 0, f"빈 목록 {empty} · 없는 id 삭제 {none.get('deleted')}건"


@check("IT-32")
def it32():
    bad = db.all_("""SELECT u.email FROM users u JOIN companies c ON c.id=u.company_id
                     WHERE u.role='recruiter' AND u.name<>c.name""")
    demo = db.all_("""SELECT c.name, string_agg(j.role_code, ',' ORDER BY j.role_code) roles FROM companies c
                      JOIN jobs j ON j.company_id=c.id WHERE c.name LIKE %s GROUP BY c.name""", "헤드헌터랩\\_%")
    ok_demo = len(demo) == 3 and all(d["roles"] == "ai_ax,backend,data_sci" for d in demo)
    return not bad and ok_demo, f"이름≠기업명 담당자 {len(bad)} · 테스트 기업 {len(demo)}곳 직무 {[d['roles'] for d in demo]}"


@check("IT-33")
def it33():
    c = _login()
    comps = c.get("/api/companies?role=ai_ax").json()
    exp = {r["id"]: r["n"] for r in db.all_("""SELECT company_id id, count(*) n FROM jobs
                                              WHERE status IN ('published','closed') GROUP BY company_id""")}
    counts_ok = len(comps) == len(exp) and all(x["jobs"] == exp[x["id"]] for x in comps)
    jobs = c.get(f"/api/jobs?company={comps[0]['id']}").json()
    scoped = all(j["company_id"] == comps[0]["id"] and "role_status" in j for j in jobs)
    prep = db.one("""SELECT j.id FROM jobs j JOIN job_roles r ON r.code=j.role_code
                     WHERE r.status='preparing' AND j.status='published' LIMIT 1""")
    code = c.post("/api/applications", json={"job_id": prep["id"]}).status_code if prep else None
    return counts_ok and scoped and code == 400, f"기업 {len(comps)}곳 공고 수 일치 {counts_ok} · 기업 필터 {scoped} · 준비 중 직무 지원 {code}"


# ---------------- AI 평가: 문제은행 생성 품질 (DB 기록으로 계산, 외부 호출 없음) ----------------
def _batch():
    from app import config
    rows = db.all_("SELECT quality_status, verification_summary s FROM generated_problems WHERE model=%s", config.OPENAI_MODEL)
    return config.OPENAI_MODEL, rows


@check("EV-17")
def ev17():
    m, rows = _batch()
    v = sum(r["quality_status"] == "verified" for r in rows)
    rate = round(v / len(rows), 3) if rows else None
    return (rate is not None and rate >= 0.8), {"value": rate, "n": len(rows), "model": m, "note": f"검증 통과 {v}/{len(rows)}"}


@check("EV-18")
def ev18():
    m, rows = _batch()
    first = sum(r["quality_status"] == "verified" and (r["s"] or {}).get("attempts") == 1 for r in rows)
    rate = round(first / len(rows), 3) if rows else None
    return (rate is not None and rate >= 0.6), {"value": rate, "n": len(rows), "model": m, "note": f"첫 시도 통과 {first}/{len(rows)}"}


@check("EV-19")
def ev19():
    m, rows = _batch()
    secs = [(r["s"] or {}).get("elapsed_sec") for r in rows if (r["s"] or {}).get("elapsed_sec")]
    avg = round(sum(secs) / len(secs), 1) if secs else None
    return (avg is not None and avg <= 120), {"value": avg, "n": len(secs), "model": m, "note": f"문제당 평균 {avg}초(최대 {max(secs) if secs else '-'}초)"}


@check("EV-20")
def ev20():
    m, rows = _batch()
    ok_rows = [r for r in rows if r["quality_status"] == "verified"]
    tok = sum(((r["s"] or {}).get("cost") or {}).get("prompt_tokens", 0) + ((r["s"] or {}).get("cost") or {}).get("completion_tokens", 0) for r in rows)
    per = round(tok / len(ok_rows)) if ok_rows else None
    return (per is not None and per <= 15000), {"value": per, "n": len(ok_rows), "model": m, "note": f"전체 토큰 {tok} ÷ 통과 {len(ok_rows)}"}


@check("EV-21")
def ev21():
    rows = db.all_("SELECT verification_log v FROM generated_problems WHERE quality_status='verified'")
    sims = []
    for r in rows:
        for h in r["v"]:
            if h.get("stage") == "V11_similarity" and h.get("ok"):
                sims.append(h.get("top_collected") or 0)
    mx = max(sims) if sims else None
    return (mx is not None and mx < 0.9), {"value": mx, "n": len(sims), "model": "-", "note": "참고 지표(독창성·법적 적합성 확정 아님)"}


@check("EV-27")
def ev27():
    r = db.one("SELECT count(*) n, count(*) FILTER (WHERE status='published') p FROM sql_problems")
    rate = round(r["p"] / r["n"], 3) if r["n"] else None
    return (rate is not None and rate >= 0.8), {"value": rate, "n": r["n"], "model": "SQL_GEN_MODEL", "note": f"게시 {r['p']}/{r['n']}"}


@check("EV-28")
def ev28():
    rows = db.all_("SELECT answer, verification_log v FROM sql_problems WHERE kind='concept'")
    tries = hits = 0
    for r in rows:
        for h in r["v"]:
            if h.get("step") == "S6_solve":
                tries += 1
        last = [h for h in r["v"] if h.get("step") == "S6_solve"]
        hits += sum(1 for h in last if h.get("answer") == r["answer"])
    rate = round(hits / tries, 3) if tries else None
    return (rate is not None and rate >= 0.8), {"value": rate, "n": tries, "model": "qwen3:8b(로컬)", "note": f"블라인드 풀이 일치 {hits}/{tries}(수정 전 시도 포함)"}


# ---------------- v0.9: 실행기 보안 · 결제 · 탈퇴 · 개인정보 · 음성 (2026-11-09) ----------------
@check("UT-33")
def ut33():
    from app import coding
    p = db.one("SELECT * FROM coding_problems WHERE id=(SELECT min(id) FROM coding_problems)")
    f = p["func_name"]
    attacks = [
        "import os\ndef %s(*a):\n    raise Exception(str(os.listdir('.')) + open('cases.json').read()[:300])\n",
        "import sys\ndef %s(*a):\n    g=sys._getframe(1).f_globals\n    raise Exception(str(g.get('cases')))\n",
        "import sys\ndef %s(*a):\n    print('LEAK', a); print('LEAK', a, file=sys.stderr)\n",
    ]
    leaks = 0
    for code in attacks:
        res = coding.sandbox_run(code % f, f, p["tests"], hide_hidden=True)
        hid = [r for t, r in zip(p["tests"], res.get("results", [])) if not t.get("public")]
        leaks += sum(1 for r in hid if set(r) - {"ok", "error"} or len(str(r.get("error", ""))) > 40)
        if "expected" in json.dumps(res):
            leaks += 1
    sols = db.all_("SELECT g.solution, p.func_name, p.tests FROM generated_problems g JOIN coding_problems p ON p.id=g.published_problem_id LIMIT 20")
    bad = 0
    for g in sols:
        r = coding.sandbox_run(g["solution"], g["func_name"], g["tests"], wait=40, hide_hidden=True)
        bad += int(r["status"] != "ok" or sum(1 for x in r["results"] if x.get("ok")) != len(g["tests"]))
    return leaks == 0 and bad == 0, f"누출 {leaks}건 · 기준 풀이 {len(sols) - bad}/{len(sols)} 통과"


@check("UT-34")
def ut34():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from app import payments
    d = payments.add_month(datetime(2027, 1, 31, 10, tzinfo=ZoneInfo("Asia/Seoul")))
    k1, k2 = payments.merchant_user_id("user", 3), payments.merchant_user_id("user", 3)
    src = open("/srv/app/config.py", encoding="utf-8").read()
    rule = 'if _pm == "mock" and (os.getenv("RAILWAY_ENVIRONMENT") or not DEV_MODE)' in src
    ok = (d.month, d.day) == (2, 28) and k1 == k2 and len(k1) == 40 and rule
    return ok, f"1/31+1개월 = {d.month}/{d.day} · 사용자 키 {len(k1)}자 동일 {k1 == k2} · 배포본 mock 차단 규칙 {rule}"


def _new_member(c, tag):
    e = f"qa{tag}{int(time.time() * 1000)}@example.test"
    c.post("/api/auth/signup", json={"name": "QA시험", "email": e, "password": "qatest1234!", "phone": "010-0000-0000",
                                     "age14": True, "terms": True, "privacy": True}).raise_for_status()
    return e, db.one("SELECT id FROM users WHERE email=%s", e)["id"]


def _subscribe(c):
    o = c.post("/api/payments/subscriptions").json()
    return o, c.get(o["redirect"].replace(BASE, ""), follow_redirects=False)


@check("IT-34")
def it34():
    from app import config, payments
    from app.payments import mock
    if config.PAYMENTS_MODE != "mock":
        raise RuntimeError(f"PAYMENTS_MODE={config.PAYMENTS_MODE} (mock에서만 시험)")
    c = httpx.Client(base_url=BASE, timeout=60)
    e, uid = _new_member(c, "pay")
    o, r1 = _subscribe(c)
    plan1 = c.get("/api/payments/me").json()["plan"]
    replay = c.get(o["redirect"].replace(BASE, ""), follow_redirects=False).headers.get("location", "")
    dup = c.post("/api/payments/subscriptions").status_code
    sid = db.one("SELECT max(id) m FROM subscriptions WHERE owner_type='user' AND owner_id=%s", uid)["m"]
    end0 = db.one("SELECT current_period_end e FROM subscriptions WHERE id=%s", sid)["e"]
    db.run("UPDATE subscriptions SET next_charge_at=now() - interval '1 minute' WHERE id=%s", sid)
    payments.renew_due()
    end1 = db.one("SELECT current_period_end e FROM subscriptions WHERE id=%s", sid)["e"]
    cancel = c.post("/api/payments/subscriptions/cancel").json()
    refund = c.post("/api/payments/1/refund", json={}).status_code
    c2 = httpx.Client(base_url=BASE, timeout=60)   # 3회 실패 → expired (별도 계정)
    e2, uid2 = _new_member(c2, "fail")
    _subscribe(c2)
    sid2 = db.one("SELECT max(id) m FROM subscriptions WHERE owner_type='user' AND owner_id=%s", uid2)["m"]
    mock.FAIL_NEXT["charge"] = 5
    for _ in range(3):
        db.run("UPDATE subscriptions SET next_charge_at=now() - interval '1 minute' WHERE id=%s", sid2)
        payments.renew_due()
    mock.FAIL_NEXT["charge"] = 0
    st2 = db.one("SELECT status FROM subscriptions WHERE id=%s", sid2)["status"]
    for cl in (c, c2):   # 시험 계정 정리(탈퇴 — 결제 기록은 남음)
        cl.post("/api/auth/withdraw", json={"password": "qatest1234!"})
    ok = ("result=ok" in r1.headers.get("location", "") and plan1 == "advanced" and "result=invalid" in replay and dup == 409
          and end1 > end0 and cancel["plan"] == "advanced" and cancel["subscription"]["status"] == "canceled" and refund == 403 and st2 == "expired")
    return ok, (f"첫 결제 {plan1} · 재사용 {replay.split('=')[-1]} · 중복 {dup} · 갱신 {end0:%m/%d}→{end1:%m/%d} · 해지 후 {cancel['plan']} · "
                f"지원자 환불 {refund} · 3회 실패 {st2}")


@check("IT-35")
def it35():
    from app import config
    if config.PAYMENTS_MODE != "mock":
        raise RuntimeError(f"PAYMENTS_MODE={config.PAYMENTS_MODE} (mock에서만 시험)")
    c = httpx.Client(base_url=BASE, timeout=60)
    e, uid = _new_member(c, "wd")
    _subscribe(c)
    sid = db.one("SELECT max(id) m FROM subscriptions WHERE owner_type='user' AND owner_id=%s", uid)["m"]
    wrong = c.post("/api/auth/withdraw", json={"password": "nope"}).status_code
    ok_w = c.post("/api/auth/withdraw", json={"password": "qatest1234!"}).status_code
    users = db.one("SELECT count(*) n FROM users WHERE id=%s", uid)["n"]
    sub = db.one("SELECT status, created_by FROM subscriptions WHERE id=%s", sid)
    pays = db.one("SELECT count(*) n FROM payments WHERE subscription_id=%s", sid)["n"]
    aud = db.one("SELECT 1 x FROM audit_logs WHERE action='account_withdrawn' AND target_id=%s", str(uid))
    rc = httpx.Client(base_url=BASE, timeout=30)
    rc.post("/api/auth/login", json={"email": "recruiter@headhunter.ai", "password": "recruiter99!", "role": "recruiter"})
    rec = rc.post("/api/auth/withdraw", json={"password": "x"}).status_code
    ok = (wrong == 400 and ok_w == 200 and users == 0 and sub["status"] == "canceled" and sub["created_by"] is None
          and pays == 1 and bool(aud) and rec in (401, 403))
    return ok, f"틀린 비번 {wrong} · 탈퇴 {ok_w} · 계정 {users} · 구독 {sub['status']}/{sub['created_by']} · 결제 기록 {pays} · 감사 {bool(aud)} · 담당자 {rec}"


@check("IT-36")
def it36():
    from app import config
    codes = [httpx.get(BASE + u, timeout=30).status_code for u in ("/privacy", "/terms")]
    foots = [("legal-links" in httpx.get(BASE + u, timeout=30).text) for u in ("/", "/applicant/", "/admin/")]
    ok = codes == [200, 200] and all(foots) and config.CONSENT_VERSION.startswith("2026-11-09.v3")
    return ok, f"/privacy {codes[0]} · /terms {codes[1]} · 하단 링크 {sum(foots)}/3 · 동의 버전 {config.CONSENT_VERSION.split()[0]}"


@check("IT-37")
def it37():
    from app import config, speech
    h = speech.health()
    if not h.get("enabled") or not h.get("tts") or not h.get("asr"):
        raise RuntimeError(f"음성 서비스 준비 안 됨: {h}")
    config.TTS_DIR = "/tmp/qa_tts"
    text = "안녕하세요. 첫 번째 질문입니다."
    wav = speech.synthesize(text)
    back = speech.transcribe(wav)
    norm = lambda x: "".join(ch for ch in x if ch.isalnum())
    return norm(back) == norm(text), f"wav {len(wav)}바이트 · 되돌린 글 '{back}'"



# ---------------- LangGraph 그래프 1·2 (2026-11-09) ----------------
@check("UT-35")
def ut35():
    from app import questions
    from app.agents import evaluation_graph as E, question_graph as G
    n1 = [x for x in G.GENERATION.get_graph().nodes if not x.startswith("__")]
    n2 = [x for x in G.INTERVIEW_STEP.get_graph().nodes if not x.startswith("__")]
    n3 = [x for x in E.GRAPH.get_graph().nodes if not x.startswith("__")]
    ok_nodes = (n1 == ["start", "extract_keywords", "evaluate_trust", "retrieve", "generate", "evaluate_philosophy", "evaluate_answerability", "finalize"]
                and n2 == ["record_answer", "judge_followup", "make_followup", "next_question"]
                and n3 == ["load", "save_cancelled", "evaluate_blocks", "compute", "summarize", "decide", "save"])
    app = db.one("SELECT * FROM applications ORDER BY id DESC LIMIT 1")
    new, _ = G.generate_set(app, use_llm=False)
    old = questions.build_set(app, use_llm=False)
    kind = lambda qs: [q["gen_method"].split(":")[0] for q in qs]          # 인성 md 문항은 무작위라 출처 종류만 비교
    same = [q["category"] for q in new] == [q["category"] for q in old] and kind(new) == kind(old)
    return ok_nodes and len(new) == sum(n for _, n in questions.PLAN) == 8 and same, f"노드 {len(n1)}·{len(n2)}·{len(n3)} · 문항 {len(new)} · build_set과 형식 같음 {same}"


@check("UT-36")
def ut36():
    import re as _re
    from app import llm
    from app.agents import question_chain as QC, question_graph as G
    job = db.one("""SELECT j.*, c.name company_name, r.name role_name FROM jobs j JOIN companies c ON c.id=j.company_id
                    JOIN job_roles r ON r.code=j.role_code WHERE j.role_code='ai_ax' ORDER BY j.id LIMIT 1""")
    v = llm.embed([QC.QUERY_INSTRUCT + QC._query({"job": job, "resume_keywords": ["RAG", "Python"]})])[0]
    used, got = set(), {}
    from app import questions as Q
    need = dict(Q.PLAN)
    for cat, n in (("job", need["job"]), ("personality", need["personality"]), ("company", need["company"])):
        got[cat] = G._pick_redis(v, "ai_ax", cat, n, used) or []
        used |= {b["dup_group"] for b in got[cat]}
    ids = [b["doc_id"] for bs in got.values() for b in bs]
    ok_role = all(i.startswith("ai_ax:") for i in [b["doc_id"] for b in got["job"]]) and         all(i.split(":")[0] in ("common", "ai_ax") for i in ids)
    ok_ko = all(_re.search(r"[가-힣]", b["text"]) for bs in got.values() for b in bs)
    groups = [b["dup_group"] for bs in got.values() for b in bs]
    ok_char = all(b["doc_id"].startswith("common:CA-") for b in got["personality"])     # 인성 = character_assessment md판
    ok = all(len(got[c]) == need[c] for c in got) and ok_char and ok_role and ok_ko and len(groups) == len(set(groups))
    return ok, f"직무 {len(got['job'])}·인성 {len(got['personality'])}·기업 {len(got['company'])} · 인성 md판 {ok_char} · 직무 필터 {ok_role} · 한국어 {ok_ko}"


@check("UT-37")
def ut37():
    from app import questions as Q
    from app.agents import translation_agent as T
    bank = Q.load_character_bank()
    ok_bank = len(bank) >= 8 and all(b["competency"] and b["dup_group"] and b["intent"] for b in bank)
    ok_block = (not Q.BLOCKLIST.search("우선순위를 어떻게 정했는지")) and bool(Q.BLOCKLIST.search("학교 순위"))
    ok_one = (not Q.one_sentence("설명해 주세요. 수행한 적이 있습니다.")) and Q.one_sentence("v1.2에서 무엇을 바꿨는지 설명해 주세요.")
    blank = T.blank_empty({"question_ko": "질문", "intent_ko": "지어낸 의도", "follow_up_ko": ""}, {"question": "q", "intent": "", "follow_up": ""}, "ko")
    ok_blank = blank["intent_ko"] == "" and blank["question_ko"] == "질문"
    ok_wh = bool(T.check_wh({"question_ko": "솔루션을 이양하시겠습니까?"}, "ko", {"question": "How do you hand over a solution?"})) and not T.check_wh({"question_ko": "솔루션을 어떻게 넘겨주시겠습니까?"}, "ko", {"question": "How do you hand over a solution?"})
    pairs = Q.resume_pairs([{"label": "a"}, {"label": "b"}])
    ok_pairs = len(pairs) == Q.RESUME_N == 3 and pairs[2][1] != 0
    ok = ok_bank and ok_block and ok_one and ok_blank and ok_wh and ok_pairs
    return ok, f"인성 은행 {len(bank)}문항 {ok_bank} · 우선순위 허용 {ok_block} · 한 문장 {ok_one} · 빈 칸 유지 {ok_blank} · 의문사 검사 {ok_wh} · 이력서 짝 {ok_pairs}"



@check("UT-38")
def ut38():
    from app import bank_seed as S
    files = sorted(p.name for p in S.SEED_DIR.glob("*")) if S.SEED_DIR.exists() else []
    need = ["bank_coding_problems.sql", "bank_question_bank.sql", "bank_sql_problems.sql", S.REDIS_FILE]
    ok_files = all(f in files for f in need)
    again = S.apply_sql() if ok_files else {}
    ok_idem = ok_files and all(v == 0 for v in again.values())          # 로컬 DB에는 이미 다 있음 → 새로 넣는 행 0
    return ok_files and ok_idem, f"파일 {len([f for f in need if f in files])}/4 · 다시 적용 시 새 행 {sum(again.values()) if again else '-'}"

@check("IT-38")
def it38():
    from app.agents import question_graph as G
    app = db.one("SELECT ap.* FROM applications ap JOIN users u ON u.id=ap.user_id WHERE u.email='applicant@headhunter.ai' AND NOT EXISTS (SELECT 1 FROM attempts a WHERE a.application_id=ap.id) LIMIT 1")
    if not app:
        raise RuntimeError("응시 기록이 없는 테스트 지원자 지원 건이 없음")
    crit = db.one("SELECT * FROM eval_criteria WHERE job_id=%s", app["job_id"])
    snap = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in crit.items()}
    snap.update(coding_enabled=False, decision_mode="auto")
    aid = db.one("""INSERT INTO attempts (application_id, status, phase, criteria_version, criteria_snapshot, started_at, last_heartbeat)
                    VALUES (%s,'interview','interview',%s,%s,now(),now()) RETURNING id""", app["id"], crit["version"], db.J(snap))["id"]
    try:
        for i, t in enumerate(["최근에 맡은 프로젝트에서 데이터 품질 문제를 해결한 경험을 설명해 주세요.", "모델 성능이 떨어졌을 때 원인을 찾는 순서를 설명해 주세요."], 1):
            db.run("INSERT INTO attempt_questions (attempt_id, seq, category, eval_items, text, gen_method, time_limit) VALUES (%s,%s,'job',%s,%s,'db',90)",
                   aid, i, ["expertise", "logic", "relevance", "delivery"], t)
        for a in ["데이터 품질은 중요하다고 생각합니다. 항상 꼼꼼히 확인하는 것이 좋습니다.",
                  "결측치가 12% 있던 센서 데이터를 보간하고 이상치를 IQR로 걸러 정확도를 0.81에서 0.86으로 올렸습니다.",
                  "먼저 입력 데이터 분포가 학습 때와 달라졌는지 보고, 다음으로 라벨 품질, 마지막으로 모델 버전 변경을 확인합니다."]:
            q = G.current_question(aid)
            if q:
                G.answer_step(db.one("SELECT * FROM attempts WHERE id=%s", aid), q, a, "typed")
        att = db.one("SELECT status, followups_used FROM attempts WHERE id=%s", aid)
        for _ in range(60):
            e = db.one("SELECT result, versions FROM evaluations WHERE attempt_id=%s", aid)
            if e:
                break
            time.sleep(5)
        else:
            from app import evaluation
            evaluation.analyze(aid)
            e = db.one("SELECT result, versions FROM evaluations WHERE attempt_id=%s", aid)
        rows = db.one("SELECT count(*) n FROM question_scores s JOIN attempt_questions q ON q.id=s.question_id WHERE q.attempt_id=%s", aid)["n"]
        ok = att["status"] == "completed" and att["followups_used"] == 1 and e and e["versions"].get("graph") == "evaluation_graph-1" and rows == 8
        return ok, f"상태 {att['status']} · 꼬리질문 {att['followups_used']} · 판정 {e and e['result']} · 점수 행 {rows}"
    finally:
        db.run("DELETE FROM analysis_jobs WHERE attempt_id=%s", aid)
        db.run("DELETE FROM attempts WHERE id=%s", aid)
        db.run("UPDATE applications SET step=%s WHERE id=%s", app["step"], app["id"])        # 임시 응시가 만든 결과 알림도 지운다 — 남으면 응시 없는 지원 건을 가리켜 '응시 기록이 없습니다'가 뜬다(2026-11-20)        db.run("DELETE FROM notifications WHERE application_id=%s AND type='result' AND created_at >= now() - interval '1 day'", app["id"])


# ---------------- v0.13 평가 플랜 (PRD 6.3.8~6.3.9) — GPU·모델 호출 없는 구조·규칙 검사 ----------------
GRAPH_SPEC = {   # ADLC v0.8 6.1 · 6.1-B · 6.3 · 6.14 (그래프 노드 · 엣지, 조건 엣지는 True)
    # 2026-11-18: v0.7의 8노드 명세가 낡아 UT-39가 계속 실패했다. v0.8이 사내문서 RAG 노드 2개를
    # 그래프 1-A에 연결했다(D-1 결정) — 4-B `retrieve_internal`(retrieve 뒤) · 7-B `guard_disclosure`(finalize 앞).
    # ADLC v0.8 67행·91행·114행·116행이 둘 다 [구현]으로 적고 있고 실제 엣지와 일치함을 확인했다.
    "GENERATION": {"nodes": ["start", "extract_keywords", "evaluate_trust", "retrieve", "retrieve_internal", "generate",
                             "evaluate_philosophy", "evaluate_answerability", "guard_disclosure", "finalize"],
                   "edges": {("__start__", "start", False), ("start", "extract_keywords", False), ("extract_keywords", "evaluate_trust", False),
                             ("evaluate_trust", "retrieve", False), ("retrieve", "retrieve_internal", False),
                             ("retrieve_internal", "generate", False), ("generate", "evaluate_philosophy", False),
                             ("evaluate_philosophy", "evaluate_answerability", False),
                             ("evaluate_answerability", "guard_disclosure", False), ("guard_disclosure", "finalize", False),
                             ("finalize", "__end__", False)}},
    "INTERVIEW_STEP": {"nodes": ["record_answer", "judge_followup", "make_followup", "next_question"],
                       "edges": {("__start__", "record_answer", False), ("record_answer", "judge_followup", False),
                                 ("judge_followup", "make_followup", True), ("judge_followup", "next_question", True),
                                 ("make_followup", "next_question", False), ("next_question", "__end__", False)}},
    "EVALUATION": {"nodes": ["load", "save_cancelled", "evaluate_blocks", "compute", "summarize", "decide", "save"],
                   "edges": {("__start__", "load", False), ("load", "__end__", True), ("load", "save_cancelled", True),
                             ("load", "evaluate_blocks", True), ("save_cancelled", "__end__", False), ("evaluate_blocks", "compute", False),
                             ("compute", "summarize", False), ("summarize", "decide", False), ("decide", "save", False), ("save", "__end__", False)}},
}


@check("UT-39")
def ut39():
    from app.agents import evaluation_graph as E, question_graph as G
    graphs = {"GENERATION": G.GENERATION, "INTERVIEW_STEP": G.INTERVIEW_STEP, "EVALUATION": E.GRAPH}
    bad = []
    for name, g in graphs.items():
        gg = g.get_graph()
        nodes = [x for x in gg.nodes if not x.startswith("__")]
        edges = {(e.source, e.target, bool(e.conditional)) for e in gg.edges}
        spec = GRAPH_SPEC[name]
        if nodes != spec["nodes"]:
            bad.append(f"{name} 노드 {nodes}")
        if edges != spec["edges"]:
            bad.append(f"{name} 엣지 차이 +{sorted(edges - spec['edges'])} -{sorted(spec['edges'] - edges)}")
    n = {k: len(v["nodes"]) for k, v in GRAPH_SPEC.items()}
    e = {k: len(v["edges"]) for k, v in GRAPH_SPEC.items()}
    return not bad, ("; ".join(bad)[:300] if bad else f"1-A 노드 {n['GENERATION']}·엣지 {e['GENERATION']} / 1-B {n['INTERVIEW_STEP']}·{e['INTERVIEW_STEP']}"
                     f"(조건 2) / 2 {n['EVALUATION']}·{e['EVALUATION']}(조건 3) — 문서와 같음")


@check("IT-39")
def it39():
    from app import internal_store as S
    pub = S.collection(S.PUBLIC).get(include=["metadatas"])["metadatas"] or []
    res = S.collection(S.RESTRICTED).get(include=["metadatas"])["metadatas"] or []
    docs = {m.get("doc_id") for m in pub + res if m}
    mix_pub = sum(1 for m in pub if (m or {}).get("disclosure") != "public")
    mix_res = sum(1 for m in res if (m or {}).get("disclosure") == "public")
    ok = len(docs) == 80 and len(pub) + len(res) == 2270 and len(pub) == 394 and len(res) == 1876 and mix_pub == 0 and mix_res == 0
    return ok, (f"문서 {len(docs)} · 조각 {len(pub) + len(res)}(public {len(pub)} · restricted {len(res)}) · "
                f"혼입 public칸 비공개 {mix_pub} · restricted칸 공개 {mix_res}")


@check("UT-40")
def ut40():
    from app import questions as Q
    plan_ok = Q.PLAN == [("personality", 2), ("resume", 3), ("job", 2), ("company", 1)]
    expected = [c for c, n in Q.PLAN for _ in range(n)]
    rows = db.all_("""SELECT a.id, array_agg(q.category ORDER BY q.seq) cats FROM attempts a
                      JOIN attempt_questions q ON q.attempt_id=a.id AND q.parent_id IS NULL GROUP BY a.id""")
    eight = [r for r in rows if len(r["cats"]) == 8]
    bad = [r["id"] for r in eight if list(r["cats"]) != expected]
    return plan_ok and not bad, (f"PLAN 인성 2·이력서 3·직무 2·기업 1 = {plan_ok} · DB 8문항 응시 {len(eight)}건 중 순서 위반 {len(bad)}"
                                 + ("" if eight else " (DB 응시 없음 — 표본 분포는 EV-35/M-37 평가 결과로 확인)"))


BLOCK_SAMPLES = ["실례지만 나이가 어떻게 되시나요?", "결혼 계획이 있으신가요?", "부모님은 어떤 일을 하시나요?", "종교가 있으신가요?",
                 "고향이 어디인가요?", "병역은 마치셨나요?", "출신 학교가 어디인가요?", "자녀 계획이 있나요?"]
ALLOW_SAMPLES = ["업무 우선순위를 정하는 기준을 설명해 주세요.", "RAG 검색 품질을 어떻게 측정했는지 설명해 주세요.",
                 "장애가 났을 때 원인을 찾은 순서를 설명해 주세요."]


@check("UT-41")
def ut41():
    from app import questions as Q
    from app.agents import question_graph as G
    miss = [s for s in BLOCK_SAMPLES if not (Q.BLOCKLIST.search(s) or G.PERSONAL.search(s))]
    false_hit = [s for s in ALLOW_SAMPLES if Q.BLOCKLIST.search(s)]
    return not miss and not false_hit, f"금지 {len(BLOCK_SAMPLES) - len(miss)}/{len(BLOCK_SAMPLES)} 차단 · 허용 오탐 {len(false_hit)}" + (f" · 놓침 {miss}" if miss else "")


REPORT_BAD = ["이 답변이면 1차 합격 가능성이 높습니다.", "불합격 사유를 보완하세요.", "합불에는 영향이 없습니다.",
              "나이에 비해 경험이 많습니다.", "학벌보다 실력을 보여 주세요.", "성별과 무관하게 좋은 답변입니다."]
REPORT_OK = ["구체적인 수치를 들어 성과를 설명하면 더 설득력이 생깁니다.", "STAR 구조로 상황과 결과를 나눠 말해 보세요."]


@check("UT-42")
def ut42():
    from app.agents import report_coach as RC   # 2026-11-18 CrewAI 제거로 app.crew → app.agents (HANDOFF 27-3)
    miss = [s for s in REPORT_BAD if not RC.FORBIDDEN.search(s)]
    false_hit = [s for s in REPORT_OK if RC.FORBIDDEN.search(s)]
    return not miss and not false_hit, f"금지 문장 {len(REPORT_BAD) - len(miss)}/{len(REPORT_BAD)} 차단 · 정상 문장 오탐 {len(false_hit)}" + (f" · 놓침 {miss}" if miss else "")


GPU_CHECKS = {"UT-35", "UT-36", "IT-37", "IT-38"}   # 임베딩·로컬 LLM·음성 모델을 부르는 검사 — QA_NO_GPU=1이면 건너뜀(다른 평가와 GPU 경합 방지, R-17)


def main(ids):
    import os
    no_gpu = os.environ.get("QA_NO_GPU") == "1"
    for tc in ids or list(CHECKS):
        fn = CHECKS.get(tc)
        if not fn:
            continue
        if no_gpu and tc in GPU_CHECKS:
            print(json.dumps({"id": tc, "status": "보류", "actual": "GPU 검사 생략(QA_NO_GPU=1) — 평가 실행과 GPU 경합 방지", "ms": 0},
                             ensure_ascii=False), flush=True)
            continue
        t = time.time()
        try:
            ok, actual = fn()
            status = "통과" if ok else "실패"
        except Exception as e:
            status, actual = "보류", f"실행 오류: {type(e).__name__}: {str(e)[:150]}"
            traceback.print_exc(file=sys.stderr)
        print(json.dumps({"id": tc, "status": status, "actual": actual, "ms": round((time.time() - t) * 1000)},
                         ensure_ascii=False, default=str), flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
