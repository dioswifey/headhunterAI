# headhunterAI 음성 서비스(Qwen3-ASR-0.6B · Qwen3-TTS-12Hz-0.6B-CustomVoice). 기본 = CPU.
# GPU 서버에서는 --build-arg TORCH_INDEX=https://download.pytorch.org/whl/cu124 로 빌드하고 SPEECH_DEVICE=cuda:0.
FROM python:3.12-slim
ARG TORCH_INDEX=https://download.pytorch.org/whl/cpu

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg sox libsndfile1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
RUN pip install --no-cache-dir --index-url ${TORCH_INDEX} torch==2.6.* torchaudio==2.6.*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && pip install --no-cache-dir --no-deps qwen-asr==0.0.6 qwen-tts==0.1.1
COPY app.py .

# 모델 가중치는 볼륨(/models)에 받아 둔다 — 컨테이너를 다시 만들어도 다시 받지 않는다
ENV HF_HOME=/models TZ=Asia/Seoul PYTHONUNBUFFERED=1
EXPOSE 8090
# 로컬 Docker = IPv4(0.0.0.0). Railway 내부망은 IPv6라 Railway 변수 SPEECH_HOST=:: 로 바꾼다
CMD ["sh", "-c", "uvicorn app:app --host ${SPEECH_HOST:-0.0.0.0} --port ${PORT:-8090}"]
