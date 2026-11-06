"""QA_doc 버전 관리 에이전트 (2026-11-04, 엑셀 3파일 판)

QA_doc/ 의 엑셀 3파일 + md 3파일을 언제·얼마나 자주 버전 올릴지 판단하고, 올려야 하면 직접 올린다.
    엑셀: headhunterAI_QA_테스트계획서.xlsx · headhunterAI_테스트케이스.xlsx · headhunterAI_QA_WBS.xlsx
    md  : QA.md(결함 상세, 사람이 씀) · VERSIONS.md(정책 설명·버전 이력) · TEST.md(엑셀 테스트케이스의 읽기용 사본, 자동 생성)
여섯 파일은 항상 같은 버전이다. 파일 이름은 바꾸지 않고, 엑셀은 '표지'!B4(버전)·B5(작성일)와 '변경이력' 시트,
md는 머리말(version·date)과 변경 기록 표에 남긴다.

    collect_signals → evaluate(정책) → apply(버전 올림) → report

판단 기준 = 테스트계획서 '6_버전정책' 시트의 키·값(사람이 고칠 수 있음). 기준값(마지막 버전의 해시·ID·상태·문서 버전)은
테스트계획서의 숨긴 시트 '_agent'에 둔다.

신호 (마지막 버전 이후)
  doc_versions   : PRD·TRD·ADLC에 새 버전 파일이 생겼는가 (요구사항 기준이 바뀜 → QA도 다시 맞춰야 함)
  content_changed: 세 엑셀의 내용(표지 버전·작성일, 변경이력, _agent 제외)이 바뀌었는가
  new_ids        : 새로 생긴 UT-/IT-/E2E-/EV-/QA- ID 수
  status_changes : '상태' 열이 바뀐 ID 수
  code_churn     : backend/·deploy/·docker-compose.yml에서 바뀐 줄 수 (git diff 기준 커밋 → 작업 트리)
  hours_since    : 마지막 버전 이후 지난 시간

결정
  내용 변경 없음 + 문서 새 버전 있음 → 올리지 않고 'QA 갱신 필요' 알림(빈 버전은 만들지 않는다)
  내용 변경 있음 + (문서 새 버전 | new_ids ≥ N | status_changes ≥ N | code_churn ≥ N | hours_since ≥ stale) → 올림
  단, min_interval_hours 안에는 한 번만(문서 새 버전은 예외). 하루에 몰린 변화는 한 버전으로 묶는다
  올림 폭: 기본 minor(v0.1 → v0.2). major(v1.0)는 --major로 사람이 정할 때만

실행 (저장소 루트, 호스트 Python + openpyxl)
  python tools/qa_version_agent.py check          # 판단만 출력
  python tools/qa_version_agent.py apply          # 필요하면 올림
  python tools/qa_version_agent.py apply --force  # 정책과 관계없이 올림(내용 변경은 있어야 함)
  python tools/qa_version_agent.py init           # 지금 상태를 기준값으로 저장(버전은 그대로)
  실행 시점(2026-11-04 결정, git 훅 쓰지 않음): PRD·TRD·ADLC 새 버전을 만든 직후, 테스트케이스·지표를 고친 직후, 세션을 마칠 때
  엑셀에서 파일을 열어 둔 채로 apply하면 저장이 막힌다 → 닫고 다시 실행
"""
import argparse
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parent.parent
QA = ROOT / "QA_doc"
FILES = {"PLAN": QA / "headhunterAI_QA_테스트계획서.xlsx", "CASES": QA / "headhunterAI_테스트케이스.xlsx", "WBS": QA / "headhunterAI_QA_WBS.xlsx"}
CASE_SHEETS = ("단위테스트", "통합테스트", "E2E테스트", "AI평가", "결함관리")
SKIP_SHEETS = ("변경이력", "_agent")
HEADER_ROW, FIRST_ROW = 4, 5
DOC_DIRS = {"PRD": ROOT / "PRD" / "PRD_prompt", "TRD": ROOT / "TRD" / "TRD_prompt", "ADLC": ROOT / "ADLC"}
CODE_PATHS = ["backend", "deploy", "docker-compose.yml"]
KST = dt.timezone(dt.timedelta(hours=9))
DEFAULT_POLICY = {"min_interval_hours": 12, "new_ids": 3, "status_changes": 3, "code_churn": 400, "stale_days": 7}
ID_RE = re.compile(r"^(UT|IT|E2E|EV|QA)-\d{2,}$")
# md 3파일도 같이 둔다(2026-11-04 사용자 지시). QA.md·VERSIONS.md는 사람이 쓰고, TEST.md는 엑셀에서 자동 생성(읽기용 사본)
MD = {"QA": QA / "QA.md", "TEST": QA / "TEST.md", "VERSIONS": QA / "VERSIONS.md"}
MD_CHANGELOG = {"QA": "## 변경 기록", "VERSIONS": "## 2. 버전 이력"}


def md_read(p):
    return p.read_bytes().decode("utf-8") if p.exists() else ""


def md_write(p, text):
    data = text.encode("utf-8")
    assert len(data) > 200, f"refuse to write tiny file {p}"
    p.write_bytes(data)                      # 바이너리로 써서 LF 유지


def md_version(text):
    m = re.search(r"^version:\s*(\S+)", text, flags=re.M)
    return m.group(1) if m else None


def md_set_front(text, **kv):
    for k, v in kv.items():
        text = re.sub(rf"^{k}:.*$", f"{k}: {v}", text, count=1, flags=re.M)
    return text


def md_body_for_hash(key, text):
    t = re.sub(r"^---\n.*?\n---\n", "", text, flags=re.S)
    return t.split(MD_CHANGELOG[key])[0].strip()


def md_add_row(text, head, row):
    """head 절의 첫 표 구분선(|---) 바로 아래에 새 줄을 넣는다(최신이 위)."""
    a, b = text.split(head, 1)
    lines = b.split("\n")
    idx = next(i for i, l in enumerate(lines) if l.startswith("|---")) + 1
    lines.insert(idx, row)
    return a + head + "\n".join(lines)


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, dt.datetime):
        v = v.strftime("%Y-%m-%d")
    return str(v).replace("|", "/").replace("\n", " ").strip()


def export_test_md(wbs, version, date):
    """엑셀 테스트케이스 → TEST.md (읽기용 사본). 직접 고치지 않는다."""
    wb = wbs["CASES"]
    out = [f"---\ndoc: TEST\ntitle: headhunterAI 테스트 (단위 · 통합 · E2E · AI 평가) — 엑셀 사본\nversion: {version}\ndate: {date}\n---\n",
           "# headhunterAI 테스트 — 읽기용 사본\n",
           "> **원본은 `headhunterAI_테스트케이스.xlsx`** 이다. 이 파일은 버전 관리 에이전트(`tools/qa_version_agent.py`)가 엑셀에서 자동으로 만든다. "
           "테스트 추가·결과 기록은 엑셀에서 하고, 여기는 고치지 않는다(다음 실행 때 덮어씀). 전략·환경·지표는 `headhunterAI_QA_테스트계획서.xlsx`, 결함 상세는 `QA.md`.\n",
           "## 요약\n", "| 시트 | 전체 | " + " | ".join(("통과", "실패", "부분 통과", "진행 중", "미실행", "보류")) + " |", "|---" * 8 + "|"]
    specs = {"단위테스트": ("TC ID", "상태", "계층", "대상 (함수·API·화면)", "예상 결과", "실제 결과", "실행일", "결함 ID"),
             "통합테스트": ("TC ID", "상태", "계층", "대상 (함수·API·화면)", "예상 결과", "실제 결과", "실행일", "결함 ID"),
             "E2E테스트": ("TC ID", "상태", "계층", "대상 (함수·API·화면)", "관련 AC", "실제 결과", "실행일", "결함 ID"),
             "AI평가": ("EV ID", "상태", "지표 ID", "대상", "모델", "측정값", "단위", "목표", "방향", "실행일", "비고"),
             "결함관리": ("결함 ID", "상태", "심각도", "영역", "증상", "조치", "발견일", "해결일")}
    sections = []
    for name, cols in specs.items():
        ws = wb[name]
        heads = [c.value for c in ws[HEADER_ROW]]
        idx = [heads.index(c) + 1 for c in cols]
        rows = []
        for r in range(FIRST_ROW, ws.max_row + 1):
            v = ws.cell(r, 1).value
            if isinstance(v, str) and ID_RE.match(v.strip()):
                rows.append([_cell(ws.cell(r, j).value) for j in idx])
        counts = [sum(1 for x in rows if x[1] == s) for s in ("통과", "실패", "부분 통과", "진행 중", "미실행", "보류")]
        out.append(f"| {name} | {len(rows)} | " + " | ".join(map(str, counts)) + " |")
        hdr = list(cols)
        if name == "AI평가":
            hdr.insert(9, "판정")
            for x in rows:
                val, goal, way = x[5], x[7], x[8]
                try:
                    ok = float(val) >= float(goal) if way == "이상" else float(val) <= float(goal)
                    x.insert(9, "달성" if ok else "미달")
                except ValueError:
                    x.insert(9, "미측정")
        sec = [f"\n## {name}\n", "| " + " | ".join(hdr) + " |", "|---" * len(hdr) + "|"]
        sec += ["| " + " | ".join(x) + " |" for x in rows]
        sections.append("\n".join(sec))
    out.append("\n(결함관리의 '통과' = 조치 완료)")
    out += sections
    cl = wbs["PLAN"]["변경이력"]
    out.append("\n## 변경 기록\n\n| 버전 | 날짜 | 변경 |\n|---|---|---|")
    for r in range(FIRST_ROW, cl.max_row + 1):
        if cl.cell(r, 1).value:
            out.append(f"| {_cell(cl.cell(r, 1).value)} | {_cell(cl.cell(r, 2).value)} | {_cell(cl.cell(r, 5).value)} |")
    md_write(MD["TEST"], "\n".join(out) + "\n")


def load_all():
    return {k: openpyxl.load_workbook(p) for k, p in FILES.items()}


def git(*args):
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    return r.stdout.strip() if r.returncode == 0 else ""


def state(wbs):
    ws = wbs["PLAN"]["_agent"]
    return {r[0]: (r[1] or "") for r in ws.iter_rows(min_row=2, values_only=True) if r[0]}


def policy(wbs):
    pol = dict(DEFAULT_POLICY)
    ws = wbs["PLAN"]["6_버전정책"]
    for k, v, *_ in ws.iter_rows(min_row=FIRST_ROW, values_only=True):
        if k in pol and isinstance(v, (int, float)):
            pol[k] = float(v)
    return pol


def content_hash(wbs):
    h = hashlib.sha256()
    for key in ("PLAN", "CASES", "WBS"):
        for ws in wbs[key].worksheets:
            if ws.title in SKIP_SHEETS:
                continue
            for row in ws.iter_rows():
                for c in row:
                    if c.value is None or (ws.title == "표지" and c.coordinate in ("B4", "B5")):
                        continue
                    h.update(f"{key}|{ws.title}|{c.coordinate}|{c.value}\n".encode("utf-8"))
    for key in ("QA", "VERSIONS"):                 # TEST.md는 엑셀에서 만드는 사본이라 제외
        h.update(md_body_for_hash(key, md_read(MD[key])).encode("utf-8"))
    return h.hexdigest()[:16]


def ids_and_status(wbs):
    ids, status = [], {}
    for name in CASE_SHEETS:
        ws = wbs["CASES"][name]
        heads = [c.value for c in ws[HEADER_ROW]]
        col = heads.index("상태") + 1 if "상태" in heads else None
        for r in range(FIRST_ROW, ws.max_row + 1):
            v = ws.cell(r, 1).value
            if isinstance(v, str) and ID_RE.match(v.strip()):
                ids.append(v.strip())
                if col:
                    status[v.strip()] = ws.cell(r, col).value or ""
    return sorted(set(ids)), status


def doc_versions_now():
    out = {}
    for k, d in DOC_DIRS.items():
        vs = sorted(tuple(map(int, m.group(1).split("."))) for p in d.glob("*.md") if (m := re.search(r"_v(\d+\.\d+)\.md$", p.name)))
        out[k] = ".".join(map(str, vs[-1])) if vs else ""
    return out


# ---------------- ① 신호 ----------------
def collect_signals(wbs):
    st = state(wbs)
    versions = {k: wbs[k]["표지"]["B4"].value for k in FILES}
    versions.update({f"{k}.md": md_version(md_read(p)) for k, p in MD.items() if p.exists()})
    ids_now, status_now = ids_and_status(wbs)
    ids_then = [i for i in st.get("ids", "").split(",") if i]
    status_then = json.loads(st.get("status") or "{}")
    doc_now = doc_versions_now()
    doc_then = json.loads(st.get("docs") or "{}")
    base = st.get("base_commit", "")
    churn = 0
    if base and git("cat-file", "-t", base):
        for line in git("diff", "--numstat", base, "--", *CODE_PATHS).splitlines():
            a, d, *_ = line.split("\t")
            churn += (int(a) if a.isdigit() else 0) + (int(d) if d.isdigit() else 0)
    last = st.get("bumped_at", "")
    hours = (dt.datetime.now(KST) - dt.datetime.fromisoformat(last)).total_seconds() / 3600 if last else 1e9
    ch = content_hash(wbs)
    return {"version": versions["PLAN"], "versions_in_files": versions, "content_hash": ch, "content_changed": ch != st.get("content_hash"),
            "new_ids": [i for i in ids_now if i not in ids_then], "ids_now": ids_now, "status_now": status_now,
            "status_changes": sorted(i for i, s in status_now.items() if i in status_then and status_then[i] != s),
            "doc_versions": {k: f"{doc_then.get(k, '?')}→{v}" for k, v in doc_now.items() if doc_then.get(k) != v}, "doc_now": doc_now,
            "code_churn": churn, "hours_since": round(hours, 1), "base_commit": base}


# ---------------- ② 평가 ----------------
def evaluate(sig, pol, force=False, major=False):
    reasons = []
    if len(set(sig["versions_in_files"].values())) > 1:
        reasons.append(f"세 파일 버전이 다름 {sig['versions_in_files']} → 맞춤")
    if sig["doc_versions"]:
        reasons.append("요구사항 문서 새 버전: " + ", ".join(f"{k} {v}" for k, v in sig["doc_versions"].items()))
    if len(sig["new_ids"]) >= pol["new_ids"]:
        reasons.append(f"새 ID {len(sig['new_ids'])}개 ≥ {int(pol['new_ids'])}")
    if len(sig["status_changes"]) >= pol["status_changes"]:
        reasons.append(f"상태 변경 {len(sig['status_changes'])}건 ≥ {int(pol['status_changes'])}")
    if sig["code_churn"] >= pol["code_churn"]:
        reasons.append(f"코드 변경 {sig['code_churn']}줄 ≥ {int(pol['code_churn'])}")
    if sig["hours_since"] >= pol["stale_days"] * 24:
        reasons.append(f"마지막 버전 후 {sig['hours_since'] / 24:.1f}일 ≥ {int(pol['stale_days'])}일")
    if not sig["content_changed"]:
        notice = "요구사항 문서가 새 버전인데 QA_doc 내용이 그대로다 → 테스트 케이스·지표를 먼저 갱신" if sig["doc_versions"] else ""
        return {"bump": False, "why": "내용 변경 없음(빈 버전은 만들지 않음)", "notice": notice, "reasons": reasons}
    if force:
        return {"bump": True, "level": "major" if major else "minor", "why": "강제 실행(--force)", "reasons": reasons}
    if not reasons:
        return {"bump": False, "why": "내용은 바뀌었지만 기준 미달 → 다음 변경과 묶어서 올림", "reasons": []}
    if sig["hours_since"] < pol["min_interval_hours"] and not sig["doc_versions"]:
        return {"bump": False, "why": f"마지막 버전 후 {sig['hours_since']}시간 < {int(pol['min_interval_hours'])}시간 → 묶어서 올림",
                "reasons": reasons}
    return {"bump": True, "level": "major" if major else "minor", "why": "정책 충족", "reasons": reasons}


def next_version(v, level):
    m = re.match(r"v(\d+)\.(\d+)", v or "v0.0")
    a, b = int(m.group(1)), int(m.group(2))
    return f"v{a + 1}.0" if level == "major" else f"v{a}.{b + 1}"


# ---------------- ③ 적용 ----------------
def write_state(wbs, sig, ch=None, bumped=True):
    ws = wbs["PLAN"]["_agent"]
    st = state(wbs)
    st.update(base_commit=git("rev-parse", "--short", "HEAD") or sig["base_commit"], content_hash=ch or sig["content_hash"],
              ids=",".join(sig["ids_now"]), status=json.dumps(sig["status_now"], ensure_ascii=False, separators=(",", ":")),
              docs=json.dumps(sig["doc_now"], separators=(",", ":")))
    if bumped:
        st["bumped_at"] = dt.datetime.now(KST).isoformat(timespec="seconds")
    ws.delete_rows(2, ws.max_row)
    for i, (k, v) in enumerate(st.items(), start=2):
        ws.cell(i, 1, k)
        ws.cell(i, 2, v)


def add_changelog(ws, row_vals):
    ws.insert_rows(FIRST_ROW)
    for j, v in enumerate(row_vals, start=1):
        c = ws.cell(FIRST_ROW, j, v)
        src = ws.cell(FIRST_ROW + 1, j)
        if src.has_style:
            c._style = src._style


def save_all(wbs):
    for k, wb in wbs.items():
        wb.calculation.fullCalcOnLoad = True       # 엑셀이 열 때 수식을 다시 계산
        try:
            wb.save(FILES[k])
        except PermissionError:
            sys.exit(f"저장 실패: {FILES[k].name}이(가) 엑셀에서 열려 있다. 닫고 다시 실행")


def writable_or_exit():
    """하나라도 열려 있으면(엑셀 잠금) 아무것도 바꾸지 않는다 — 일부만 버전이 올라가는 것을 막음."""
    locked = []
    for p in list(FILES.values()) + list(MD.values()):
        if not p.exists():
            continue
        try:
            with open(p, "r+b"):
                pass
        except OSError:
            locked.append(p.name)
    if locked:
        sys.exit("아무것도 바꾸지 않음: 다음 파일이 다른 프로그램(엑셀 등)에서 열려 있다 → 닫고 다시 실행: " + ", ".join(locked))


def apply(wbs, sig, dec, note=""):
    writable_or_exit()
    date = dt.datetime.now(KST).strftime("%Y-%m-%d")
    new = next_version(sig["version"], dec["level"])
    summary = "; ".join(dec["reasons"]) or dec["why"]
    if sig["new_ids"]:
        summary += f" · 새 ID {', '.join(sig['new_ids'][:12])}{' …' if len(sig['new_ids']) > 12 else ''}"
    if sig["status_changes"]:
        summary += f" · 상태 변경 {', '.join(sig['status_changes'][:12])}"
    head = git("rev-parse", "--short", "HEAD") or "-"
    why = dec["why"] if not dec["reasons"] else "; ".join(dec["reasons"])
    for wb in wbs.values():
        wb["표지"]["B4"] = new
        wb["표지"]["B5"] = date
        add_changelog(wb["변경이력"], [new, date, head, why, note or summary])
    texts = {k: md_read(MD[k]) for k in ("QA", "VERSIONS")}              # md: 전부 읽고
    texts["QA"] = md_add_row(md_set_front(texts["QA"], version=new, date=date), "## 변경 기록", f"| {new} | {date} | {_cell(note or summary)} |")
    texts["VERSIONS"] = md_add_row(md_set_front(texts["VERSIONS"], version=new, date=date), "## 2. 버전 이력",
                                   f"| {new} | {date} | {head} | {_cell(why)}{' · ' + _cell(note) if note else ''} |")
    for k, t in texts.items():                                            # 그다음 쓴다
        md_write(MD[k], t)
    write_state(wbs, sig, ch=content_hash(wbs))     # 버전 올린 뒤 내용 기준으로 저장(표지 버전·변경이력은 해시에서 빠짐)
    save_all(wbs)
    export_test_md(wbs, new, date)
    return new


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")       # Windows 콘솔 한글 깨짐 방지
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["check", "apply", "init", "export-md"])
    p.add_argument("--force", action="store_true")
    p.add_argument("--major", action="store_true")
    p.add_argument("--note", default="", help="변경이력 '변경 내용' 칸에 적을 말")
    a = p.parse_args(argv)
    wbs = load_all()
    sig = collect_signals(wbs)
    if a.cmd == "export-md":
        export_test_md(wbs, sig["version"], wbs["PLAN"]["표지"]["B5"].value)
        print(f"TEST.md ← 엑셀 ({sig['version']})")
        return
    if a.cmd == "init":
        export_test_md(wbs, sig["version"], wbs["PLAN"]["표지"]["B5"].value)
        write_state(wbs, sig)
        save_all(wbs)
        print(json.dumps({"baseline": sig["version"], "ids": len(sig["ids_now"]), "docs": sig["doc_now"]}, ensure_ascii=False))
        return
    dec = evaluate(sig, policy(wbs), force=a.force, major=a.major)
    out = {"version": sig["version"], **dec,
           "signals": {k: sig[k] for k in ("content_changed", "new_ids", "status_changes", "doc_versions", "code_churn", "hours_since")}}
    if a.cmd == "apply" and dec["bump"]:
        out["new_version"] = apply(wbs, sig, dec, a.note)
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
