# Railway 배포용 코드 실행기 (HTTP). 로컬은 backend/runner/Dockerfile(네트워크 차단 컨테이너)
FROM python:3.11-slim
WORKDIR /runner
COPY backend/runner/runner.py .
ENV PYTHONUNBUFFERED=1
CMD ["sh", "-c", "RUNNER_HTTP_PORT=${PORT:-8080} python runner.py"]
