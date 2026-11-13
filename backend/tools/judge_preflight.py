"""평가 판정 모델 사전 점검 — 다이어그램 모음 v0.2 16.2 '평가 판정(계획)' 연결 (2026-11-13).

`eval_reports.py judge` · `eval_rag_questions.py judge|internal`의 판정 모델은 `--model` 기본 **qwen3:8b**
(PRD PSP-06)로 이미 연결돼 있다. 점선(계획)인 이유는 코드가 아니라 실행 환경이다 — 6GB GPU(회사 PC)에서는
생성 모델(qwen3:4b)과 8b가 번갈아 적재돼 멈춰서 2026-11-12에는 4b로 돌렸다(ADLC 3.4 한계 표).

이 명령은 판정을 돌리기 **전에** 그 모델로 돌려도 되는지만 본다. 모델을 부르거나 적재하지 않는다
(Ollama `/api/tags`·`/api/ps` 조회만 → GPU를 쓰지 않음). 판정 결과는 어디서도 점수 합산·합불에 쓰이지 않는다
(평가 도구는 로컬 파일에만 쓴다 — `evaluation.compute`와 무관).

    cd backend && python -m tools.judge_preflight [--model qwen3:8b] [--base-url http://localhost:11434]
    종료 코드: 0 준비됨 · 1 경고(자기 평가 편향·다른 모델 적재 중) · 2 판정 불가(모델 없음·Ollama 연결 실패)
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def check(model, base_url, pipeline_model, http=None):
    """반환 {ready, level(0|1|2), notes[], installed[], loaded[]}."""
    if http is None:
        import httpx as http
    notes, level = [], 0
    try:
        tags = http.get(f"{base_url}/api/tags", timeout=5).json().get("models", [])
        ps = http.get(f"{base_url}/api/ps", timeout=5).json().get("models", [])
    except Exception as e:
        return {"ready": False, "level": 2, "notes": [f"Ollama 연결 실패({type(e).__name__}) — {base_url}"],
                "installed": [], "loaded": []}
    installed = [m.get("name") for m in tags]
    loaded = [(m.get("name"), round((m.get("size_vram") or 0) / 2**30, 1)) for m in ps]
    if model not in installed:
        return {"ready": False, "level": 2, "installed": installed, "loaded": loaded,
                "notes": [f"판정 모델 {model} 이 설치돼 있지 않음 — `ollama pull {model}`"]}
    if model == pipeline_model:
        level = max(level, 1)
        notes.append(f"판정 모델이 제품 경로 모델(LLM_MODEL={pipeline_model})과 같음 — 자기 평가 편향 가능(R-16), 결과는 참고값")
    others = [n for n, _ in loaded if n != model]
    if others:
        level = max(level, 1)
        notes.append(f"다른 모델이 적재돼 있음({', '.join(others)}) — 작은 GPU면 판정 모델과 번갈아 적재돼 느려지거나 멈출 수 있음."
                     " 측정이 끝난 뒤 돌릴 것")
    return {"ready": True, "level": level, "notes": notes, "installed": installed, "loaded": loaded}


def main(argv=None):
    from app import config
    ap = argparse.ArgumentParser(description="평가 판정 모델 사전 점검(모델 호출 없음)")
    ap.add_argument("--model", default="qwen3:8b")
    ap.add_argument("--base-url", default=os.getenv("JUDGE_BASE_URL", config.LLM_BASE_URL))
    a = ap.parse_args(argv)
    r = check(a.model, a.base_url.rstrip("/"), config.LLM_MODEL)
    print(f"판정 모델 {a.model} @ {a.base_url}: {['준비됨', '준비됨(경고)', '판정 불가'][r['level']]}")
    print(f"  적재 중: {r['loaded'] or '없음'}")
    for n in r["notes"]:
        print(f"  - {n}")
    sys.exit(r["level"])


if __name__ == "__main__":
    main()
