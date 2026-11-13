"""사내문서 적재 A13 — 금지 용어 사전 · prune · audit 단위 검사 (2026-11-13). 모델·Redis·Chroma 없이 호스트에서 돈다.

    cd backend && python -m tools.test_internal_docs_ingest

가짜 llm.embed · 메모리 Chroma(internal_store 함수 바꿔치기) · 메모리 Redis로 다음을 확인한다.
  F1  사전은 **모든 등급** 문서의 forbid 주석을 모은다 · 쓰기/읽기 왕복 · 빈 사전·Redis 오류 → ForbidUnavailable · forbid_ok=False
  F2  find_terms — 영문 ID 단어 경계(ISS ≠ ISSUE, PRJ-CC는 PRJ-CC-PUB에서 걸림) · 띄어 쓰기 우회('대한 전자') · chunk_terms가 일반 단어 제외
  I1  ingest → 두 컬렉션 분리 · 사전 기록 · audit 통과 · rag_ready True
  I2  같은 원본으로 sync 두 번 → 둘째는 임베딩 0회
  I3  등급 public → internal 변경 후 sync → public에서 사라지고 restricted에만(두 컬렉션 삭제)
  I4  임베딩 실패 → 그 문서만 건너뜀, 사전은 그대로 최신
  P1  원본에서 지운 문서 → prune이 두 컬렉션 모두에서 삭제, --dry-run은 안 지움
  P2  다시 적재 안 된 옛 등급 조각(moved) → audit critical moved_left → prune이 정리 → audit 통과
  P3  안전장치: 저장 문서의 25% 넘게 지우게 되면 멈춤(--force면 진행) · 원본 0개면 --force여도 멈춤
  P4  doc 메타가 깨진 파일(원본은 있음) → 지우지 않고 보류
  A1  audit은 아무것도 지우지 않는다(실패해도) · 결과는 idoc:audit에 판정·건수만(본문·용어 원문 없음)
  A2  public 본문에 restricted 문서에만 적힌 금지 용어 → critical public_forbid → rag_ready False(audit_failed)
  A3  일반 단어(주소)만 들어 있는 public 본문은 critical 아님
  A4  Redis 사전이 원본과 다름 → forbid_stale · 사전 없음 → forbid_unavailable · 기록 없음 → rag_ready audit_missing
  A5  public 컬렉션에 disclosure≠public 조각 → public_mixed
  J1~J4  tools.judge_preflight — 판정 모델 설치·적재·자기 평가 편향 점검(Ollama 조회만, 모델 호출 없음)
"""
import os
import shutil
import sys
import tempfile
import types

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
try:
    import langsmith  # noqa: F401
except ImportError:  # 호스트에 langsmith가 없으면 traceable을 그대로 통과시키는 가짜
    _ls = types.ModuleType("langsmith")
    _ls.traceable = lambda *a, **k: (a[0] if a and callable(a[0]) and not k else (lambda f: f))
    sys.modules["langsmith"] = _ls

from app import internal_store as S, llm  # noqa: E402
from app.agents import internal_forbid as F  # noqa: E402
from app.agents import internal_docs_agent as A  # noqa: E402


# ---------------- 가짜 ----------------
class FakeRedis:
    def __init__(self):
        self.h, self.fail = {}, False

    def _chk(self):
        if self.fail:
            raise ConnectionError("redis down")

    def hset(self, key, mapping):
        self._chk(); self.h.setdefault(key, {}).update({k: str(v) for k, v in mapping.items()})

    def rename(self, a, b):
        self._chk(); self.h[b] = self.h.pop(a)

    def hkeys(self, key):
        self._chk(); return list(self.h.get(key, {}))

    def hgetall(self, key):
        self._chk(); return dict(self.h.get(key, {}))

    def delete(self, key):
        self._chk(); self.h.pop(key, None)


class FakeStore:
    def __init__(self):
        self.c = {S.PUBLIC: {}, S.RESTRICTED: {}}
        self.deletes = 0

    def upsert(self, name, ids, docs, metas, vecs):
        assert len(ids) == len(vecs)
        for i, d, m in zip(ids, docs, metas):
            self.c[name][i] = (d, dict(m))

    def delete_doc(self, name, doc_id):
        gone = [i for i, (_, m) in self.c[name].items() if m.get("doc_id") == doc_id]
        for i in gone:
            del self.c[name][i]
        self.deletes += len(gone)

    def doc_hashes(self, name):
        return {m["doc_id"]: m.get("doc_content_hash") for _, m in self.c[name].values()}

    def count(self, name):
        return len(self.c[name])

    def chunks(self, name, documents=False):
        out = []
        for i, (d, m) in self.c[name].items():
            r = {"id": i, "metadata": m}
            if documents:
                r["document"] = d
            out.append(r)
        return out

    def ids(self, name, doc_id=None):
        return sorted(i for i, (_, m) in self.c[name].items() if doc_id is None or m["doc_id"] == doc_id)


EMBED = {"calls": 0, "fail_on": None}


def fake_embed(texts, batch=16, timeout=120):
    EMBED["calls"] += 1
    if EMBED["fail_on"] and any(EMBED["fail_on"] in t for t in texts):
        raise llm.LLMError("fake embed down")
    return [[0.1] * 4 for _ in texts]


def doc(doc_id, disclosure, sections, title=None):
    lines = [f"# {title or doc_id}", f"<!--meta: doc={doc_id}; doc_type=project; tier=1; topic=T; disclosure={disclosure}; status=active-->", ""]
    for n, (body, forbid) in enumerate(sections, 1):
        lines += [f"## 섹션{n}", f"<!--meta: sec=s{n}; dept=AIAX|BE; level=주니어; ct_band=중-->"]
        if forbid:
            lines.append(f"<!--forbid: {forbid}-->")
        lines += [body, ""]
    return "\n".join(lines) + "\n"


class Env:
    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="idoc_test_")
        self.store, self.r = FakeStore(), FakeRedis()
        A.DOCS_DIR = self.dir
        for fn in ("upsert", "delete_doc", "doc_hashes", "count", "chunks"):
            setattr(S, fn, getattr(self.store, fn))
        F._client = lambda r=None, _r=self.r: r if r is not None else _r
        llm.embed = fake_embed
        A.llm.embed = fake_embed
        EMBED.update(calls=0, fail_on=None)

    def put(self, name, text):
        with open(os.path.join(self.dir, name), "wb") as f:
            f.write(text.encode("utf-8"))

    def rm(self, name):
        os.remove(os.path.join(self.dir, name))

    def docs(self):
        return A.load_docs()

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)


def base(env):
    env.put("01_pub.md", doc("PUB-A", "public", [("공개 프로젝트 소개 본문입니다.", "대한전자|PRJ-CC"), ("두 번째 섹션 본문.", "")]))
    env.put("02_det.md", doc("DET-B", "internal", [("상세판: 대한전자 콜센터 계약 범위와 한빛유통 단가.", "대한전자|한빛유통|주소")]))
    env.put("03_pub.md", doc("PUB-C", "public", [("또 다른 공개 문서. API 주소와 메서드를 설명한다.", "")]))


# ---------------- 검사 ----------------
FAILS = []


def check(name, cond, info=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f" — {info}" if info and not cond else ""))
    if not cond:
        FAILS.append(name)


def quiet(fn, *a, **k):
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()) as buf:
        out = fn(*a, **k)
    return out, buf.getvalue()


def t_forbid():
    print("F1 · F2 금지 용어 사전")
    env = Env()
    try:
        base(env)
        docs, _ = env.docs()
        m = F.collect(docs)
        check("F1 모든 등급에서 수집(restricted에만 있는 한빛유통 포함) · 접두어 PRJ-CC → 상세판 ID", set(m) == {"대한전자", "PRJ-CC-DET", "한빛유통", "주소"}, m)
        check("F1 용어 → 출처 doc_id", sorted(m["대한전자"]) == ["DET-B", "PUB-A"])
        F.write(m, len(docs))
        check("F1 왕복", F.load_terms() == sorted(m))
        check("F1 meta 기록(용어 원문 아님)", F.meta()["terms"] == "4" and "대한전자" not in str(F.meta()))
        try:
            F.write({}, 0); ok = False
        except ValueError:
            ok = True
        check("F1 빈 사전은 쓰지 않음", ok and F.load_terms() == sorted(m))
        env.r.h.pop(F.KEY)
        try:
            F.load_terms(); ok = False
        except F.ForbidUnavailable:
            ok = True
        check("F1 키 없음 → ForbidUnavailable", ok)
        check("F1 사전 없음 → forbid_ok False(보수적)", F.forbid_ok("아무 문장") is False)
        env.r.fail = True
        try:
            F.load_terms(); ok = False
        except F.ForbidUnavailable:
            ok = True
        check("F1 Redis 오류 → ForbidUnavailable", ok)
        env.r.fail = False

        t = ["ISS", "PRJ-CC", "대한전자", "주소"]
        check("F2 ISS는 ISSUE에서 안 걸림", F.find_terms("ISSUE 트래커", t) == [])
        check("F2 PRJ-CC는 PRJ-CC-PUB에서 걸림", F.find_terms("문서 PRJ-CC-PUB 참고", t) == ["PRJ-CC"])
        check("F2 띄어 쓰기 우회 차단", F.find_terms("대한 전자 사례", t) == ["대한전자"])
        check("F2 chunk_terms는 일반 단어 제외", F.chunk_terms(t) == ["ISS", "PRJ-CC", "대한전자"])
        check("F2 forbid_ok(terms 지정)", F.forbid_ok("깨끗한 문장", t) and not F.forbid_ok("대한전자", t))
    finally:
        env.close()


def t_ingest():
    print("I1~I4 적재")
    env = Env()
    try:
        base(env)
        rep, out = quiet(A.run, "ingest")
        st = env.store
        check("I1 public 컬렉션엔 public 문서만", {m["doc_id"] for _, m in st.c[S.PUBLIC].values()} == {"PUB-A", "PUB-C"})
        check("I1 restricted엔 그 밖", {m["doc_id"] for _, m in st.c[S.RESTRICTED].values()} == {"DET-B"})
        check("I1 사전 기록", F.load_terms() == sorted(["대한전자", "PRJ-CC-DET", "한빛유통", "주소"]))
        check("I1 audit 통과", rep["ok"] and not rep["critical"], rep["critical"])
        check("I1 rag_ready True", F.rag_ready() == (True, ""), F.rag_ready())
        check("I1 출력에 금지 용어 원문 없음", "한빛유통" not in out)

        EMBED["calls"] = 0
        quiet(A.run, "sync")
        check("I2 두 번째 sync 임베딩 0회", EMBED["calls"] == 0, EMBED["calls"])
        n0 = (env.store.deletes, dict(env.r.h))
        _, out = quiet(A.run, "sync", dry=True)
        check("I2 sync --dry-run은 해시를 비교해 대상 0개 · 쓰기 없음",
              "적재 대상 0개" in out and (env.store.deletes, dict(env.r.h)) == n0, out[-200:])

        env.put("01_pub.md", doc("PUB-A", "internal", [("이제 내부 문서.", "대한전자")]))
        rep, _ = quiet(A.run, "sync")
        check("I3 등급 내려간 문서가 public에서 사라짐", st.ids(S.PUBLIC, "PUB-A") == [])
        check("I3 restricted에만 있음", len(st.ids(S.RESTRICTED, "PUB-A")) == 1)
        check("I3 audit 통과", rep["ok"], rep["critical"])

        env.put("03_pub.md", doc("PUB-C", "public", [("고친 공개 문서 EMBEDFAIL", "새용어")]))
        EMBED["fail_on"] = "EMBEDFAIL"
        rep, out = quiet(A.run, "sync")
        check("I4 임베딩 실패 문서는 건너뜀", st.ids(S.PUBLIC, "PUB-C") == [] and "임베딩 실패" in out)
        check("I4 사전은 최신(새용어 포함)", "새용어" in F.load_terms())
        check("I4 audit은 missing 경고만(게이트 통과)", rep["ok"] and "missing" in rep["warnings"], rep)
    finally:
        env.close()


def t_prune():
    print("P1~P4 prune")
    env = Env()
    try:
        base(env)
        env.put("04_pub.md", doc("PUB-D", "public", [("네 번째 공개 문서.", "")]))
        env.put("05_pub.md", doc("PUB-E", "public", [("다섯 번째 공개 문서.", "")]))
        quiet(A.run, "ingest")
        st = env.store

        env.rm("03_pub.md")
        docs, sk = env.docs()
        before = st.ids(S.PUBLIC)
        res, _ = quiet(A.prune, docs, sk, dry=True)
        check("P1 --dry-run은 안 지움", st.ids(S.PUBLIC) == before and [p[1] for p in res["plan"]] == ["PUB-C"])
        res, _ = quiet(A.prune, docs, sk)
        check("P1 원본 없는 문서 삭제", st.ids(S.PUBLIC, "PUB-C") == [] and res["deleted"][0][2] == "gone")
        check("P1 다른 문서는 그대로", len(st.ids(S.PUBLIC, "PUB-A")) == 2 and len(st.ids(S.RESTRICTED, "DET-B")) == 1)

        # 옛 등급 조각을 직접 남겨 둔다(코드가 지우기 전 버전으로 적재된 상황)
        st.c[S.PUBLIC]["DET-B#000#0"] = ("옛 공개판 조각", {**st.c[S.RESTRICTED]["DET-B#000#0"][1], "disclosure": "public"})
        rep, _ = quiet(A.audit, docs, sk)
        check("P2 audit critical moved_left", "moved_left" in rep["critical"], rep["critical"])
        check("P2 rag_ready False", F.rag_ready() == (False, "audit_failed"))
        res, _ = quiet(A.prune, docs, sk)
        check("P2 prune이 옛 등급 조각 정리(moved)", st.ids(S.PUBLIC, "DET-B") == [] and res["deleted"][0][2] == "moved"
              and len(st.ids(S.RESTRICTED, "DET-B")) == 1)
        rep, _ = quiet(A.audit, docs, sk)
        check("P2 정리 후 audit 통과 · rag_ready True", rep["ok"] and F.rag_ready()[0], rep["critical"])

        for f in ("04_pub.md", "05_pub.md"):
            env.rm(f)
        docs, sk = env.docs()
        n0 = st.deletes
        res, out = quiet(A.prune, docs, sk)
        check("P3 25% 넘게 지우면 멈춤", res["refused"] == "ratio" and st.deletes == n0, res.get("refused"))
        res, _ = quiet(A.prune, docs, sk, force=True)
        check("P3 --force면 진행", res["refused"] is None and st.ids(S.PUBLIC, "PUB-D") == [])
        res, _ = quiet(A.prune, [], [], force=True)
        check("P3 원본 0개면 --force여도 멈춤", res["refused"] == "no_source_docs" and st.ids(S.PUBLIC, "PUB-A"))

        env.put("01_pub.md", "# 메타가 깨진 문서\n\n## 섹션\n본문\n")
        docs, sk = env.docs()
        res, _ = quiet(A.prune, docs, sk, force=True)
        check("P4 doc 메타 깨진 파일 → 보류, 안 지움", st.ids(S.PUBLIC, "PUB-A") and res["hold"]
              and res["hold"][0][1] == "PUB-A", res)
    finally:
        env.close()


def t_audit():
    print("A1~A5 audit")
    env = Env()
    try:
        base(env)
        quiet(A.run, "ingest")
        st = env.store
        docs, sk = env.docs()

        # A2: restricted 문서에만 있는 고객사명이 public 본문에 남음
        k = st.ids(S.PUBLIC, "PUB-C")[0]
        st.c[S.PUBLIC][k] = ("공개 문서인데 한빛 유통 이름이 남아 있다", st.c[S.PUBLIC][k][1])
        n0 = (len(st.c[S.PUBLIC]), len(st.c[S.RESTRICTED]), st.deletes)
        rep, out = quiet(A.audit, docs, sk)
        check("A2 public_forbid critical", "public_forbid" in rep["critical"], rep["critical"])
        check("A2 rag_ready False(audit_failed)", F.rag_ready() == (False, "audit_failed"))
        check("A1 audit은 삭제하지 않음", (len(st.c[S.PUBLIC]), len(st.c[S.RESTRICTED]), st.deletes) == n0)
        rec = env.r.h[F.AUDIT_KEY]
        check("A1 기록 = 판정·건수만(본문·용어 원문 없음)", rec["ok"] == "0" and "public_forbid" in rec["critical"]
              and "한빛" not in str(rec) and "한빛" not in out, rec)

        st.c[S.PUBLIC][k] = ("또 다른 공개 문서. API 주소와 메서드를 설명한다.", st.c[S.PUBLIC][k][1])
        rep, _ = quiet(A.audit, docs, sk)
        check("A3 일반 단어(주소)는 critical 아님", rep["ok"], rep["critical"])

        env.r.h[F.KEY]["다른용어"] = "|X|"
        rep, _ = quiet(A.audit, docs, sk)
        check("A4 사전이 원본과 다름 → forbid_stale", "forbid_stale" in rep["critical"])
        env.r.h.pop(F.KEY)
        rep, _ = quiet(A.audit, docs, sk, record=False)
        check("A4 사전 없음 → forbid_unavailable", "forbid_unavailable" in rep["critical"])
        check("A4 사전 없으면 rag_ready False", F.rag_ready()[1].startswith("forbid_unavailable"))
        quiet(A.build_forbid, docs)
        env.r.h.pop(F.AUDIT_KEY)
        check("A4 audit 기록 없음 → audit_missing", F.rag_ready() == (False, "audit_missing"))

        st.c[S.PUBLIC]["PUB-A#000#0"][1]["disclosure"] = "internal"
        rep, _ = quiet(A.audit, docs, sk)
        check("A5 public_mixed", "public_mixed" in rep["critical"])
    finally:
        env.close()


def t_judge_preflight():
    print("J1~J4 판정 모델 사전 점검(tools.judge_preflight, 모델 호출 없음)")
    from tools import judge_preflight as J

    class Resp:
        def __init__(self, d):
            self.d = d

        def json(self):
            return self.d

    def http(tags, ps, fail=False):
        class H:
            calls = []

            @staticmethod
            def get(url, timeout=5):
                H.calls.append(url)
                if fail:
                    raise ConnectionError("down")
                return Resp({"models": [{"name": n} for n in tags]} if url.endswith("/tags")
                            else {"models": [{"name": n, "size_vram": 3 * 2**30} for n in ps]})
        return H

    h = http(["qwen3:4b", "qwen3:8b"], [])
    r = J.check("qwen3:8b", "http://x", "qwen3:4b", h)
    check("J1 설치됨·적재 없음 → 준비됨(0)", r["ready"] and r["level"] == 0, r)
    check("J1 /api/chat·generate를 부르지 않음(조회만)", all(u.endswith(("/api/tags", "/api/ps")) for u in h.calls))
    r = J.check("qwen3:8b", "http://x", "qwen3:4b", http(["qwen3:4b"], []))
    check("J2 모델 없음 → 판정 불가(2)", not r["ready"] and r["level"] == 2)
    r = J.check("qwen3:4b", "http://x", "qwen3:4b", http(["qwen3:4b"], ["qwen3:8b"]))
    check("J3 자기 평가 편향·다른 모델 적재 → 경고(1)", r["ready"] and r["level"] == 1 and len(r["notes"]) == 2)
    r = J.check("qwen3:8b", "http://x", "qwen3:4b", http([], [], fail=True))
    check("J4 Ollama 연결 실패 → 판정 불가(2)", r["level"] == 2)


def main():
    t_forbid()
    t_ingest()
    t_prune()
    t_audit()
    t_judge_preflight()
    print(f"\n{'모두 통과' if not FAILS else f'실패 {len(FAILS)}건: {FAILS}'}")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
