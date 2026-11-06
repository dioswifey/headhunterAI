"""음성 서비스 클라이언트 (v0.9, TRD v0.6 15장) — STT = Qwen3-ASR, TTS = Qwen3-TTS (backend/speech 컨테이너).

- 외부 음성 API는 쓰지 않는다. 실패하면 None을 돌려주고, 부르는 쪽이 대체 경로(비상 입력란·브라우저 음성)로 간다.
- 답변 음성은 텍스트로 바꾼 뒤 저장하지 않는다(PRD v0.9 INT-03). TTS 캐시에는 질문 문장 음성만 둔다.
"""
import hashlib
import os
import threading

import httpx

from . import config


class SpeechError(Exception):
    pass


def enabled():
    return bool(config.SPEECH_URL)


def _headers():
    return {"x-speech-token": config.SPEECH_TOKEN} if config.SPEECH_TOKEN else {}


def transcribe(audio: bytes, lang: str = "ko") -> str:
    """녹음 → 글. 실패하면 SpeechError."""
    if not enabled():
        raise SpeechError("음성 서비스 미설정")
    try:
        r = httpx.post(config.SPEECH_URL + "/stt", params={"lang": lang}, content=audio, headers=_headers(), timeout=config.STT_TIMEOUT)
        r.raise_for_status()
        return (r.json().get("text") or "").strip()
    except Exception as e:
        raise SpeechError(f"{type(e).__name__}: {str(e)[:200]}") from e


def _tts_path(text, lang):
    h = hashlib.sha256(f"{lang}|{text}".encode("utf-8")).hexdigest()[:32]
    return os.path.join(config.TTS_DIR, h + ".wav")


def synthesize(text: str, lang: str = "ko") -> bytes:
    """질문 문장 → wav. 캐시를 먼저 본다. 실패하면 SpeechError."""
    text = (text or "").strip()
    if not text:
        raise SpeechError("빈 문장")
    p = _tts_path(text, lang)
    if os.path.exists(p):
        with open(p, "rb") as f:
            return f.read()
    if not enabled():
        raise SpeechError("음성 서비스 미설정")
    try:
        r = httpx.post(config.SPEECH_URL + "/tts", json={"text": text, "language": lang}, headers=_headers(), timeout=config.TTS_TIMEOUT)
        r.raise_for_status()
        data = r.content
    except Exception as e:
        raise SpeechError(f"{type(e).__name__}: {str(e)[:200]}") from e
    os.makedirs(config.TTS_DIR, exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, p)
    return data


def cached(text: str, lang: str = "ko") -> bool:
    return os.path.exists(_tts_path((text or "").strip(), lang))


def prewarm(texts, lang="ko"):
    """응시 시작 때 기본 질문 음성을 미리 만들어 둔다(백그라운드, 실패는 무시 → 그 질문은 브라우저 음성)."""
    if not enabled():
        return

    def run():
        for t in texts:
            try:
                synthesize(t, lang)
            except SpeechError:
                pass
    threading.Thread(target=run, daemon=True).start()


def health():
    if not enabled():
        return {"enabled": False}
    try:
        r = httpx.get(config.SPEECH_URL + "/health", timeout=3)
        return {"enabled": True, **r.json()}
    except Exception as e:
        return {"enabled": True, "ok": False, "error": type(e).__name__}
