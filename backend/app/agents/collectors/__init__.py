"""코딩 문제 수집기(사이트 어댑터) 목록 — 코딩 문제 수집 에이전트(`agents/coding_problem_agent.py`)가 쓴다.

**자동 등록**: 이 폴더의 `*.py`(밑줄로 시작하는 파일 제외) 중 `SITE` 딕셔너리가 있는 모듈을 사이트로 등록한다.
사이트를 늘리려면 파일 하나만 추가한다(번역 스킬과 같은 방식). 어댑터가 지켜야 할 계약:

    SITE = {"name": "programmers", "hosts": ["school.programmers.co.kr"], "label": "프로그래머스"}
    list_ids(fetcher, limit, kinds) -> [source_id, ...]      # 목록(사이트가 허용하는 경로만)
    problem_url(source_id, kind) -> str                       # 문제 원본 주소
    parse(html, source_id) -> dict                            # 원문 그대로의 필드(가공은 에이전트가 한다)
        {title, statement, difficulty, level_num, category, languages[], starter, examples_header[], examples_rows[[셀 문자열]]}

공통 규칙: 요청은 `fetcher`(robots.txt 확인·사이트별 간격·차단 감지)로만 보낸다.
403·429 같은 차단 응답을 받으면 우회하지 않고 그 사이트 수집을 멈춘다.
"""
import importlib
import pkgutil

_REG = {}


def sites():
    if not _REG:
        for m in pkgutil.iter_modules(__path__):
            if m.name.startswith("_"):
                continue
            mod = importlib.import_module(f"{__name__}.{m.name}")
            if hasattr(mod, "SITE"):
                _REG[mod.SITE["name"]] = mod
    return _REG


def get(name):
    s = sites()
    if name not in s:
        raise ValueError(f"unknown site: {name} (registered: {sorted(s)})")
    return s[name]
