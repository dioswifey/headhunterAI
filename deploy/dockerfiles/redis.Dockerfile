# Railway 배포용 Redis 8 (질문 은행 벡터 저장소, Query Engine 내장). 로컬은 docker-compose.yml의 redis 서비스
# 비밀번호는 Railway 변수 REDIS_PASSWORD(무작위 값, 대시보드에서만 확인). 공개 도메인·TCP 프록시를 열지 않는다(내부망 전용)
FROM redis:8.2.1
CMD ["sh", "-c", "exec redis-server --bind '* -::*' --protected-mode no --appendonly yes --dir /data --requirepass \"$REDIS_PASSWORD\""]
