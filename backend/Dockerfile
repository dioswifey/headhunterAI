FROM python:3.11-slim

# Tesseract OCR (한국어+영어) — PRD OCR-01, TRD §5
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-kor tesseract-ocr-eng \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY db ./db

ENV TZ=Asia/Seoul PYTHONUNBUFFERED=1 SCHEMA_PATH=/srv/db/schema.sql
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
