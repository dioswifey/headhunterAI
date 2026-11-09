"""headhunterAI 음성 서비스 — Qwen3-ASR(STT) · Qwen3-TTS(TTS) 로컬 모델 (PRD v0.9 INT-01·INT-03, TRD v0.6 15장).

- 외부 음성 API를 쓰지 않는다. 모델 가중치는 첫 기동 때 Hugging Face에서 받아 HF_HOME(볼륨)에 둔다.
- POST /stt   본문 = 녹음 파일(브라우저 webm/opus 등) → {"text", "language", "sec"}
- POST /tts   {"text", "speaker"?, "language"?} → audio/wav
- GET  /health  → 모델 적재 상태
- 토큰: SPEECH_TOKEN이 있으면 x-speech-token 헤더가 같아야 한다(Railway 내부망에서도 검사).
- 음성 파일은 메모리에서만 처리하고 디스크에 남기지 않는다(변환용 임시 파일은 바로 지운다).
"""
import io
import os
import subprocess
import tempfile
import threading
import time

import numpy as np
import soundfile as sf
import torch
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

ASR_MODEL = os.getenv("ASR_MODEL", "Qwen/Qwen3-ASR-0.6B")
TTS_MODEL = os.getenv("TTS_MODEL", "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice")
TTS_SPEAKER = os.getenv("TTS_SPEAKER", "Sohee")          # 한국어 여성 화자(모델 기본 제공 9명 중)
DEVICE = os.getenv("SPEECH_DEVICE", "cpu")               # cpu | cuda:0
TOKEN = os.getenv("SPEECH_TOKEN", "")
MAX_AUDIO_BYTES = int(os.getenv("STT_MAX_BYTES", str(20 * 1024 * 1024)))
MAX_AUDIO_SEC = int(os.getenv("STT_MAX_SEC", "200"))     # 답변 제한 시간(최대 180초) + 여유
MAX_TTS_CHARS = int(os.getenv("TTS_MAX_CHARS", "400"))
LANG = {"ko": "Korean", "en": "English"}

if DEVICE == "cpu":
    torch.set_num_threads(max(1, int(os.getenv("SPEECH_THREADS", str(os.cpu_count() or 2)))))
_DT = {"float32": torch.float32, "float16": torch.float16, "bfloat16": torch.bfloat16}
_default_dt = "float32" if DEVICE == "cpu" else "float16"
# GTX 16xx(Turing)는 TTS를 float16으로 돌리면 샘플링에서 NaN → CUDA assert가 난다(2026-11-09 실측) → TTS_DTYPE=float32
ASR_DTYPE = _DT[os.getenv("ASR_DTYPE", _default_dt)]
TTS_DTYPE = _DT[os.getenv("TTS_DTYPE", _default_dt)]

app = FastAPI(title="headhunterAI speech")
_models, _locks, _errors = {}, {"asr": threading.Lock(), "tts": threading.Lock()}, {}


def _load(kind):
    with _locks[kind]:
        if kind in _models:
            return _models[kind]
        t = time.time()
        try:
            if kind == "asr":
                from qwen_asr import Qwen3ASRModel
                m = Qwen3ASRModel.from_pretrained(ASR_MODEL, dtype=ASR_DTYPE, device_map=DEVICE,
                                                  max_inference_batch_size=1, max_new_tokens=512)
            else:
                from qwen_tts import Qwen3TTSModel
                m = Qwen3TTSModel.from_pretrained(TTS_MODEL, dtype=TTS_DTYPE, device_map=DEVICE)
        except Exception as e:  # 모델 내려받기·적재 실패 → 다음 요청 때 다시 시도
            _errors[kind] = f"{type(e).__name__}: {str(e)[:300]}"
            raise
        _errors.pop(kind, None)
        _models[kind] = m
        print(f"[speech] {kind} 모델 적재 {time.time() - t:.1f}초 ({DEVICE})", flush=True)
        return m


def _check(token):
    if TOKEN and token != TOKEN:
        raise HTTPException(403, "forbidden")


@app.on_event("startup")
def _preload():
    if os.getenv("SPEECH_PRELOAD", "1") == "1":
        def run():
            for k in ("asr", "tts"):
                try:
                    _load(k)
                except Exception as e:
                    print(f"[speech] {k} 미리 적재 실패: {e}", flush=True)
        threading.Thread(target=run, daemon=True).start()


@app.get("/health")
def health():
    return {"ok": True, "device": DEVICE, "asr": "asr" in _models, "tts": "tts" in _models,
            "asr_model": ASR_MODEL, "tts_model": TTS_MODEL, "errors": _errors}


def _decode(raw: bytes):
    """어떤 형식이든 ffmpeg로 16kHz 모노 float32로 바꾼다."""
    with tempfile.NamedTemporaryFile(suffix=".bin") as f:
        f.write(raw)
        f.flush()
        p = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", f.name, "-t", str(MAX_AUDIO_SEC),
                            "-ac", "1", "-ar", "16000", "-f", "f32le", "-"], capture_output=True, timeout=60)
    if p.returncode != 0:
        raise HTTPException(400, "오디오를 읽을 수 없습니다.")
    return np.frombuffer(p.stdout, dtype=np.float32)


@app.post("/stt")
async def stt(request: Request, lang: str = "", x_speech_token: str = Header("")):
    _check(x_speech_token)
    raw = await request.body()
    if not raw:
        raise HTTPException(400, "빈 오디오")
    if len(raw) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "오디오가 너무 큽니다.")
    audio = _decode(raw)
    sec = len(audio) / 16000
    if sec < 0.3 or float(np.abs(audio).max(initial=0)) < 1e-3:  # 거의 무음
        return {"text": "", "language": None, "sec": round(sec, 1)}
    m = _load("asr")
    t = time.time()
    r = m.transcribe(audio=(audio, 16000), language=LANG.get(lang))
    return {"text": (r[0].text or "").strip(), "language": r[0].language, "sec": round(sec, 1), "took": round(time.time() - t, 1)}


class TTSIn(BaseModel):
    text: str
    speaker: str = ""
    language: str = "ko"


@app.post("/tts")
def tts(body: TTSIn, x_speech_token: str = Header("")):
    _check(x_speech_token)
    text = (body.text or "").strip()[:MAX_TTS_CHARS]
    if not text:
        raise HTTPException(400, "빈 문장")
    m = _load("tts")
    t = time.time()
    wavs, sr = m.generate_custom_voice(text=text, language=LANG.get(body.language, "Korean"), speaker=body.speaker or TTS_SPEAKER)
    buf = io.BytesIO()
    sf.write(buf, wavs[0], sr, format="WAV", subtype="PCM_16")
    return Response(buf.getvalue(), media_type="audio/wav", headers={"x-took": f"{time.time() - t:.1f}"})
