"""수정 가능한 시스템 프롬프트 (v0.6). 사용자가 이 폴더의 .md를 직접 고친다.

- 파일을 고치면 `docker compose up -d --build api`로 반영(백엔드 이미지에 포함).
- {중괄호} 자리는 코드가 채운다. 없는 값은 빈칸이 된다. 중괄호 자체를 쓰려면 {{ }}.
- 바꾼 뒤에는 config.VERSIONS["prompt"]를 올려 결과에 버전이 남게 한다(ADLC 1.2).
"""
from pathlib import Path

DIR = Path(__file__).parent


class _Blank(dict):
    def __missing__(self, key):
        return ""


def load(name, **values):
    text = (DIR / f"{name}.md").read_text(encoding="utf-8")
    return text.format_map(_Blank({k: ("" if v is None else str(v)) for k, v in values.items()}))
