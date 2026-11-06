"""LLM provider interface (TRD §5.1).

Every AI call goes through `chat_json`, so swapping qwen3:4b (dev, Ollama) for Qwen3-8B (prod)
only changes LLM_MODEL / LLM_BASE_URL. Callers always handle LLMError with a rule-based fallback.
"""
import json
import re

import httpx
from langsmith import traceable

from . import config


class LLMError(Exception):
    def __init__(self, msg="", usage=None):
        super().__init__(msg)
        self.usage = usage          # 외부 API가 토큰을 쓰고 실패한 경우의 사용량(비용 기록용)


class OllamaProvider:
    def __init__(self, base_url, model):
        self.base_url = base_url.rstrip("/")
        self.model = model

    def chat_json(self, system, user, schema, timeout=None, temperature=0.2, model=None, max_tokens=None, num_ctx=None):
        opts = {"temperature": temperature, "num_ctx": num_ctx or 6144}
        if max_tokens:
            opts["num_predict"] = max_tokens   # 출력 상한: 소형 모델이 같은 값을 끝없이 반복하는 것을 막는다
        payload = {
            "model": model or self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "format": schema,
            "stream": False,
            "think": False,
            "options": opts,
            "keep_alive": "30m",
        }
        try:
            r = httpx.post(f"{self.base_url}/api/chat", json=payload, timeout=timeout or config.LLM_TIMEOUT)
            r.raise_for_status()
            text = r.json()["message"]["content"]
        except Exception as e:  # network, timeout, 5xx
            raise LLMError(f"llm request failed: {type(e).__name__}") from e
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise LLMError("llm returned invalid json") from e

    def health(self):
        try:
            r = httpx.get(f"{self.base_url}/api/tags", timeout=3)
            names = [m["name"] for m in r.json().get("models", [])]
            return {"ok": self.model in names, "model": self.model}
        except Exception:
            return {"ok": False, "model": self.model}


provider = OllamaProvider(config.LLM_BASE_URL, config.LLM_MODEL)


# LANGSMITH_TRACING=false(기본·운영)이면 traceable은 아무것도 보내지 않는다 (TRD v0.2 §13.4)
@traceable(name="llm.chat_json", run_type="llm", metadata={"model": config.LLM_MODEL})
def chat_json(system, user, schema, timeout=None, temperature=0.2, model=None, max_tokens=None, num_ctx=None):
    return provider.chat_json(system, user, schema, timeout=timeout, temperature=temperature, model=model,
                              max_tokens=max_tokens, num_ctx=num_ctx)


# 임베딩(질문 은행 벡터 저장소, v0.7). 로컬 Ollama 임베딩 모델만 쓴다(외부 임베딩 API 금지)
@traceable(name="llm.embed", run_type="embedding", metadata={"model": config.EMBED_MODEL})
def embed(texts, batch=16, timeout=120):
    out = []
    for i in range(0, len(texts), batch):
        try:
            r = httpx.post(f"{config.EMBED_BASE_URL.rstrip('/')}/api/embed",
                           json={"model": config.EMBED_MODEL, "input": texts[i:i + batch], "keep_alive": "30m"}, timeout=timeout)
            r.raise_for_status()
            out.extend(r.json()["embeddings"])
        except Exception as e:
            raise LLMError(f"embed request failed: {type(e).__name__}") from e
    return out


def model_version():
    return f"{config.LLM_PROVIDER}:{config.LLM_MODEL}"


# ---------------- 코딩 문제은행 생성 전용 외부 API (2026-11-05 사용자 결정) ----------------
# 예외 규칙: OpenAI API는 **자체 코딩 문제 생성(생성·수정·예시 설명)에만** 쓴다. 지원자 데이터는 보내지 않는다
# (보내는 것은 출제 명세와 모델 자신이 만든 초안뿐). 검증 AI 검토·문제 조회·채점·면접·번역 등은 로컬 Qwen 그대로.
# 키는 .env의 OPENAI_API_KEY에만. 응답의 usage(토큰 수)를 돌려줘 비용을 기록한다.
def openai_chat_json(system, user, schema, timeout=300, temperature=0.4, max_tokens=8000, model=None):
    """반환: (dict, usage{prompt_tokens, completion_tokens, model}). 실패 → LLMError."""
    if not config.OPENAI_API_KEY:
        raise LLMError("OPENAI_API_KEY 없음")
    sys_msg = (system + "\n\n[출력 형식] 아래 JSON 스키마의 키를 모두 가진 JSON 객체 하나만 출력한다. 설명·코드 블록 표시 없이.\n"
               + json.dumps(schema, ensure_ascii=False))
    model = (model or config.OPENAI_MODEL).strip()
    body = {"model": model, "messages": [{"role": "system", "content": sys_msg}, {"role": "user", "content": user}],
            "response_format": {"type": "json_object"}, "max_completion_tokens": max_tokens}
    if config.OPENAI_SEND_TEMPERATURE:
        body["temperature"] = temperature
    url, hdr = f"{config.OPENAI_BASE_URL.rstrip('/')}/chat/completions", {"Authorization": f"Bearer {config.OPENAI_API_KEY}"}
    try:
        r = httpx.post(url, json=body, timeout=timeout, headers=hdr)
        if r.status_code == 400 and "temperature" in r.text and "temperature" in body:
            body.pop("temperature")                  # 추론 계열(gpt-5.x 등)은 temperature를 받지 않는다 → 빼고 다시
            r = httpx.post(url, json=body, timeout=timeout, headers=hdr)
        if r.status_code >= 400:
            raise LLMError(f"openai {r.status_code}: {r.text[:200]}")
        d = r.json()
    except LLMError:
        raise
    except Exception as e:
        raise LLMError(f"openai request failed: {type(e).__name__}") from e
    u = d.get("usage") or {}
    usage = {"model": d.get("model") or model, "prompt_tokens": u.get("prompt_tokens", 0),
             "completion_tokens": u.get("completion_tokens", 0)}
    ch = (d.get("choices") or [{}])[0]
    text = (ch.get("message") or {}).get("content") or ""
    try:
        out = json.loads(text)
    except Exception as e:
        # 원인 기록: finish_reason=length면 출력 한도에서 잘린 것
        raise LLMError(f"openai returned invalid json (finish_reason={ch.get('finish_reason')}, "
                       f"out_tokens={usage['completion_tokens']}, tail={text[-80:]!r})", usage=usage) from e
    missing = [k for k in schema.get("required", []) if k not in out]
    if missing:
        raise LLMError(f"openai json missing keys: {missing[:5]}", usage=usage)
    return out, usage
