# Railway 배포용 api 이미지 (빌드 컨텍스트 = 저장소 루트). 로컬 개발은 backend/Dockerfile + docker-compose.yml
FROM python:3.11-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-kor tesseract-ocr-eng \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY backend/db ./db
COPY main /web/main
COPY admin /web/admin
COPY applicant /web/applicant
COPY shared /web/shared
COPY images /web/images
ENV TZ=Asia/Seoul PYTHONUNBUFFERED=1 SCHEMA_PATH=/srv/db/schema.sql
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
