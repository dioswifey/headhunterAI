"""headhunterAI 음성 서비스 — Qwen3-ASR(STT) 로컬 모델 + TTS(PRD v0.9 INT-01·INT-03, TRD v0.6 15장).

- STT(답변 음성 인식)는 항상 로컬 Qwen3-ASR, 지원자 데이터를 외부로 보내지 않는다.
- TTS(질문 음성 합성)는 TTS_PROVIDER로 고른다(2026-11-10 사용자 결정 — 로컬 Qwen3-TTS 기본 화자가 느리고 기계음 같아서):
    "qwen"       기본. 로컬 Qwen3-TTS(CustomVoice, 화자 TTS_SPEAKER)
    "qwen_clone" 로컬 Qwen3-TTS **Base** 모델(TTS_BASE_MODEL)로, 미리 만들어 둔 참조 음성(TTS_CLONE_REF_PATH)의
                 목소리 톤을 복제해 생성한다. 면접 중에는 100% 로컬이지만, 6GB GPU에 자리가 없어 CPU로 도는
                 이 PC에서는 한 문장에 8분 넘게 걸려 실사용 불가로 확인됨(2026-11-10) — GPU 여유 생기면 재검토.
    "openai"     외부 API(실시간, OpenAI `gpt-4o-mini-tts`). 질문 문장이 그때그때 외부로 나가고 매 호출 과금됨.
                 **지금 쓰는 경로**(2026-11-10) — 처리방침·동의서 반영 전까지는 실사용 지원자에게 켜지 않는다.
    "elevenlabs" 외부 API(실시간). openai와 같은 이유로 실사용 지원자에게 켜지 않는다.
  ElevenLabs는 "qwen_clone"용 참조 음성 샘플을 한 번만 만드는 데도 쓸 수 있다(POST /tts/clone-ref, 지금은 안 씀).
- POST /stt   본문 = 녹음 파일(브라우저 webm/opus 등) → {"text", "language", "sec"}
- POST /tts   {"text", "speaker"?, "language"?} → audio/wav
- POST /tts/clone-ref  {"text"?} → ElevenLabs로 참조 음성 샘플 1개를 만들어 TTS_CLONE_REF_PATH에 저장(1회성 준비 작업)
- GET  /health  → 모델 적재 상태
- 토큰: SPEECH_TOKEN이 있으면 x-speech-token 헤더가 같아야 한다(Railway 내부망에서도 검사).
- 음성 파일은 메모리에서만 처리하고 디스크에 남기지 않는다(변환용 임시 파일은 바로 지운다. 단 클론 참조 샘플은 목소리
  톤만 담긴 고정 문장이라 TTS_CLONE_REF_PATH에 계속 둔다).
"""
import io
import os
import subprocess
import tempfile
import threading
import time

import httpx
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

TTS_PROVIDER = os.getenv("TTS_PROVIDER", "qwen")         # qwen | qwen_clone | elevenlabs | openai
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_TTS_MODEL = os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
OPENAI_TTS_VOICE = os.getenv("OPENAI_TTS_VOICE", "alloy")
OPENAI_TTS_SPEED = float(os.getenv("OPENAI_TTS_SPEED", "1.3"))
OPENAI_TTS_INSTRUCTIONS = os.getenv("OPENAI_TTS_INSTRUCTIONS") or (
    "매우 차분하고 낮은 톤으로, 감정 기복 없이 담담하게 말하세요. 신뢰감 있고 진지한 전문 면접관처럼, "
    "서두르지 않고 침착하게 질문을 읽으세요. 밝거나 들뜬 느낌은 전혀 없어야 합니다."
)


def _tts_openai(text: str):
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY 미설정")
    r = httpx.post(
        "https://api.openai.com/v1/audio/speech",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
        json={"model": OPENAI_TTS_MODEL, "voice": OPENAI_TTS_VOICE, "input": text, "response_format": "wav",
              "speed": OPENAI_TTS_SPEED, "instructions": OPENAI_TTS_INSTRUCTIONS},
        timeout=30,
    )
    r.raise_for_status()
    return r.content
TTS_BASE_MODEL = os.getenv("TTS_BASE_MODEL", "Qwen/Qwen3-TTS-12Hz-0.6B-Base")   # qwen_clone 전용 체크포인트
TTS_CLONE_REF_PATH = os.getenv("TTS_CLONE_REF_PATH", "/models/clone_ref.wav")
TTS_CLONE_REF_TEXT = os.getenv("TTS_CLONE_REF_TEXT", "안녕하세요. 이것은 목소리 톤을 복제하기 위한 샘플 문장입니다. 오늘 면접에 참여해 주셔서 감사합니다.")
ELEVEN_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVEN_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "")
ELEVEN_MODEL = os.getenv("ELEVENLABS_MODEL", "eleven_multilingual_v2")
ELEVEN_LANG = {"ko": "ko", "en": "en"}


def _tts_elevenlabs(text: str, language: str):
    if not (ELEVEN_API_KEY and ELEVEN_VOICE_ID):
        raise RuntimeError("ELEVENLABS_API_KEY/ELEVENLABS_VOICE_ID 미설정")
    r = httpx.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVEN_VOICE_ID}",
        headers={"xi-api-key": ELEVEN_API_KEY},
        params={"output_format": "pcm_24000"},
        json={"text": text, "model_id": ELEVEN_MODEL,
              "language_code": ELEVEN_LANG.get(language, "ko"),
              "voice_settings": {"stability": 0.5, "similarity_boost": 0.8}},
        timeout=30,
    )
    r.raise_for_status()
    pcm = np.frombuffer(r.content, dtype=np.int16)
    buf = io.BytesIO()
    sf.write(buf, pcm, 24000, format="WAV", subtype="PCM_16")
    return buf.getvalue()

if DEVICE == "cpu":
    torch.set_num_threads(max(1, int(os.getenv("SPEECH_THREADS", str(os.cpu_count() or 2)))))
_DT = {"float32": torch.float32, "float16": torch.float16, "bfloat16": torch.bfloat16}
_default_dt = "float32" if DEVICE == "cpu" else "float16"
# GTX 16xx(Turing)는 TTS를 float16으로 돌리면 샘플링에서 NaN → CUDA assert가 난다(2026-11-09 실측) → TTS_DTYPE=float32
ASR_DTYPE = _DT[os.getenv("ASR_DTYPE", _default_dt)]
TTS_DTYPE = _DT[os.getenv("TTS_DTYPE", _default_dt)]

app = FastAPI(title="headhunterAI speech")
_models, _locks, _errors = {}, {"asr": threading.Lock(), "tts": threading.Lock(), "tts_clone": threading.Lock()}, {}


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
            elif kind == "tts_clone":
                from qwen_tts import Qwen3TTSModel
                m = Qwen3TTSModel.from_pretrained(TTS_BASE_MODEL, dtype=TTS_DTYPE, device_map=DEVICE)
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


_TTS_KIND = {"qwen": "tts", "qwen_clone": "tts_clone"}   # elevenlabs는 로컬 모델이 없음


@app.on_event("startup")
def _preload():
    if os.getenv("SPEECH_PRELOAD", "1") == "1":
        kinds = ("asr",) + ((_TTS_KIND[TTS_PROVIDER],) if TTS_PROVIDER in _TTS_KIND else ())

        def run():
            for k in kinds:
                try:
                    _load(k)
                except Exception as e:
                    print(f"[speech] {k} 미리 적재 실패: {e}", flush=True)
        threading.Thread(target=run, daemon=True).start()


@app.get("/health")
def health():
    if TTS_PROVIDER == "openai":
        tts_ok, tts_model = bool(OPENAI_API_KEY), OPENAI_TTS_MODEL
    elif TTS_PROVIDER == "elevenlabs":
        tts_ok, tts_model = bool(ELEVEN_API_KEY and ELEVEN_VOICE_ID), ELEVEN_MODEL
    elif TTS_PROVIDER == "qwen_clone":
        tts_ok, tts_model = ("tts_clone" in _models and os.path.exists(TTS_CLONE_REF_PATH)), TTS_BASE_MODEL
    else:
        tts_ok, tts_model = "tts" in _models, TTS_MODEL
    return {"ok": True, "device": DEVICE, "asr": "asr" in _models, "tts": tts_ok,
            "asr_model": ASR_MODEL, "tts_provider": TTS_PROVIDER, "tts_model": tts_model,
            "clone_ref": os.path.exists(TTS_CLONE_REF_PATH), "errors": _errors}


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
    t = time.time()
    if TTS_PROVIDER == "openai":
        try:
            wav = _tts_openai(text)
        except Exception as e:
            raise HTTPException(502, f"openai tts 실패: {type(e).__name__}: {str(e)[:200]}") from e
        return Response(wav, media_type="audio/wav", headers={"x-took": f"{time.time() - t:.1f}"})
    if TTS_PROVIDER == "elevenlabs":
        try:
            wav = _tts_elevenlabs(text, body.language)
        except Exception as e:
            raise HTTPException(502, f"elevenlabs tts 실패: {type(e).__name__}: {str(e)[:200]}") from e
        return Response(wav, media_type="audio/wav", headers={"x-took": f"{time.time() - t:.1f}"})
    if TTS_PROVIDER == "qwen_clone":
        if not os.path.exists(TTS_CLONE_REF_PATH):
            raise HTTPException(500, "복제할 참조 음성이 없습니다. 먼저 POST /tts/clone-ref로 만드세요.")
        m = _load("tts_clone")
        ref_text_path = TTS_CLONE_REF_PATH + ".txt"
        ref_text = open(ref_text_path, encoding="utf-8").read() if os.path.exists(ref_text_path) else TTS_CLONE_REF_TEXT
        # ICL 모드(음색 + 발화 맥락까지 이어받음): x_vector_only_mode=False는 ref_text가 참조 음성 내용과 정확히 같아야 함
        wavs, sr = m.generate_voice_clone(text=text, language=LANG.get(body.language, "Korean"),
                                          ref_audio=TTS_CLONE_REF_PATH, ref_text=ref_text, x_vector_only_mode=False)
        buf = io.BytesIO()
        sf.write(buf, wavs[0], sr, format="WAV", subtype="PCM_16")
        return Response(buf.getvalue(), media_type="audio/wav", headers={"x-took": f"{time.time() - t:.1f}"})
    m = _load("tts")
    wavs, sr = m.generate_custom_voice(text=text, language=LANG.get(body.language, "Korean"), speaker=body.speaker or TTS_SPEAKER)
    buf = io.BytesIO()
    sf.write(buf, wavs[0], sr, format="WAV", subtype="PCM_16")
    return Response(buf.getvalue(), media_type="audio/wav", headers={"x-took": f"{time.time() - t:.1f}"})


class CloneRefIn(BaseModel):
    text: str = ""


@app.post("/tts/clone-ref")
def make_clone_ref(body: CloneRefIn, x_speech_token: str = Header("")):
    """ElevenLabs로 목소리 톤 샘플을 1회 생성해 TTS_CLONE_REF_PATH에 저장한다(1회성 준비 작업, 면접 중에는 호출 안 함)."""
    _check(x_speech_token)
    text = (body.text or TTS_CLONE_REF_TEXT).strip()
    try:
        wav = _tts_elevenlabs(text, "ko")
    except Exception as e:
        raise HTTPException(502, f"elevenlabs tts 실패: {type(e).__name__}: {str(e)[:200]}") from e
    os.makedirs(os.path.dirname(TTS_CLONE_REF_PATH) or ".", exist_ok=True)
    with open(TTS_CLONE_REF_PATH, "wb") as f:
        f.write(wav)
    with open(TTS_CLONE_REF_PATH + ".txt", "w", encoding="utf-8") as f:
        f.write(text)
    return {"ok": True, "path": TTS_CLONE_REF_PATH, "bytes": len(wav)}
