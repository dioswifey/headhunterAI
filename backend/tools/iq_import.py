"""사내문서 질문 풀(A17) JSONL → Chroma 적재 — `internal_question_agent export`의 역방향. **모델 호출 0회.**

왜 생겼나: 질문 풀은 Chroma에 있고 Chroma는 git에 없다(CLAUDE.md 2026-11-18). 그래서 PC를 옮기면
`export`로 남긴 백업(`documents/03_QA_doc/eval_runs/iq_backup_*/.../internal_questions.jsonl`)이 유일한
사본인데 되돌리는 명령이 없었다. 백업 JSONL에는 **임베딩이 base64로 들어 있으므로**(`run_export`)
임베딩 모델을 부르지 않고 그대로 넣는다 — `app.llm`을 import하지 않는 것이 이 파일의 불변식이다.

    cd backend && python -m tools.iq_import <jsonl> --dry-run
    cd backend && python -m tools.iq_import <jsonl> --replace

안전장치
  - 파일 머리줄 `_meta`의 `embed_model`·`embed_dim`이 지금 설정(`config.EMBED_MODEL`·`EMBED_DIM`)과
    다르면 **중단한다**. 벡터 공간이 다르면 검색이 성립하지 않는다(CLAUDE.md 2026-11-11).
  - 면접관용 문서(루브릭·평가 기준 등, `IQ.INTERVIEWER_DOC`)에서 나온 질문은 **버린다**.
    2026-11-20 사용자 지적으로 풀에서 지운 15개(MSK-41 인재상 면접 평가 루브릭)가 백업에는 남아 있다.
  - `--replace`는 그 카테고리의 기존 질문을 **전부 지우고** 넣는다. 지우기 전에 반드시 `export`로 백업할 것.
  - api 컨테이너가 돌고 있으면 같은 sqlite를 다른 프로세스가 열고 있다 → `docker compose stop api` 후
    적재하고 `start api`로 다시 띄운다.
"""
import argparse
import base64
import collections
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app import config, internal_store as S  # noqa: E402
from app.agents import internal_question_agent as IQ  # noqa: E402

BATCH = 200


def _vec(b64):
    """base64(float32 연속) → 파이썬 실수 목록. numpy 없이도 돌게 struct로 푼다."""
    import struct
    raw = base64.b64decode(b64)
    if len(raw) % 4:
        raise ValueError("임베딩 길이가 4의 배수가 아니다")
    return list(struct.unpack("<%df" % (len(raw) // 4), raw))


def read_jsonl(path):
    meta, rows = {}, []
    # 2026-11-24: `.gz`도 그대로 받는다 — 배포 사본(`questionbank/vectorDB/_export/`)이 gzip이라
    # 다른 PC에서 풀지 않고 바로 적재할 수 있게.
    opener = __import__("gzip").open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if "_meta" in rec:
                meta = rec["_meta"]
                continue
            rows.append(rec)
    return meta, rows


def check_meta(meta):
    """임베딩 모델·차원이 지금 설정과 같은지. 다르면 적재하지 않는다."""
    problems = []
    if meta.get("embed_model") and meta["embed_model"] != config.EMBED_MODEL:
        problems.append(f"embed_model {meta['embed_model']} != 설정 {config.EMBED_MODEL}")
    if meta.get("embed_dim") and int(meta["embed_dim"]) != config.EMBED_DIM:
        problems.append(f"embed_dim {meta['embed_dim']} != 설정 {config.EMBED_DIM}")
    return problems


def run(path, replace=False, dry=True, keep_interviewer=False):
    meta, rows = read_jsonl(path)
    problems = check_meta(meta)
    if problems:
        print("중단 — 임베딩이 맞지 않는다: " + " / ".join(problems))
        return 2
    print(f"파일 {path}: {len(rows)}행, _meta={meta}")

    by_cat = collections.defaultdict(list)
    skipped = collections.Counter()
    bad = []
    for r in rows:
        cat = r.get("q_cat")
        m = r.get("metadata") or {}
        if cat not in IQ.CATS:
            bad.append((r.get("id"), f"알 수 없는 q_cat {cat}"))
            continue
        if not keep_interviewer and IQ.INTERVIEWER_DOC.search(m.get("doc_title") or ""):
            skipped["interviewer_doc"] += 1       # 면접관용 문서 질문(2026-11-20 사용자 지적으로 삭제한 것)
            continue
        if not r.get("document"):
            bad.append((r.get("id"), "질문 본문 없음"))
            continue
        try:
            vec = _vec(r.get("embedding") or "")
        except Exception as e:                    # noqa: BLE001
            bad.append((r.get("id"), f"임베딩 복원 실패 {type(e).__name__}"))
            continue
        if len(vec) != config.EMBED_DIM:
            bad.append((r.get("id"), f"차원 {len(vec)} != {config.EMBED_DIM}"))
            continue
        by_cat[cat].append((r["id"], r["document"], m, vec))

    for cat in IQ.CATS:
        print(f"  {cat}: 넣을 질문 {len(by_cat.get(cat, []))}개 / 지금 저장돼 있는 것 {S.count_questions(cat)}개")
    if skipped:
        print(f"  건너뜀: {dict(skipped)}")
    if bad:
        print(f"  버린 행 {len(bad)}개: {bad[:5]}")
    dups = [i for i, c in collections.Counter(i for v in by_cat.values() for i, *_ in v).items() if c > 1]
    if dups:
        print(f"중단 — id 중복 {len(dups)}개: {dups[:5]}")
        return 2
    if dry:
        print("--dry-run이라 쓰지 않았다.")
        return 0

    for cat, items in by_cat.items():
        if replace:
            old = [r["id"] for r in S.questions_all(cat, documents=False)]
            if old:
                coll = S.question_collection(cat)
                for i in range(0, len(old), BATCH):
                    coll.delete(ids=old[i:i + BATCH])
                print(f"  {cat}: 기존 {len(old)}개 삭제")
        for i in range(0, len(items), BATCH):
            part = items[i:i + BATCH]
            S.upsert_questions(cat, [p[0] for p in part], [p[1] for p in part],
                               [p[2] for p in part], [p[3] for p in part])
        print(f"  {cat}: {len(items)}개 적재 → 지금 {S.count_questions(cat)}개")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description="사내문서 질문 풀 JSONL 적재(모델 호출 0회)")
    p.add_argument("jsonl")
    p.add_argument("--replace", action="store_true", help="그 카테고리의 기존 질문을 전부 지우고 넣는다")
    p.add_argument("--dry-run", action="store_true", help="세어만 보고 쓰지 않는다")
    p.add_argument("--keep-interviewer", action="store_true",
                   help="면접관용 문서(루브릭 등) 질문도 넣는다 — 평소에는 쓰지 않는다")
    a = p.parse_args(argv)
    return run(a.jsonl, replace=a.replace, dry=a.dry_run, keep_interviewer=a.keep_interviewer)


if __name__ == "__main__":
    sys.exit(main())
