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
    pass


class OllamaProvider:
    def __init__(self, base_url, model):
        self.base_url = base_url.rstrip("/")
        self.model = model

    def chat_json(self, system, user, schema, timeout=None, temperature=0.2):
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "format": schema,
            "stream": False,
            "think": False,
            "options": {"temperature": temperature, "num_ctx": 6144},
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
def chat_json(system, user, schema, timeout=None, temperature=0.2):
    return provider.chat_json(system, user, schema, timeout=timeout, temperature=temperature)


def model_version():
    return f"{config.LLM_PROVIDER}:{config.LLM_MODEL}"
