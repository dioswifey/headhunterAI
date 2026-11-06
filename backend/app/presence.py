"""공고 '지금 n명이 보고 있어요' — 실측만 (2026-11-05 사용자 결정: 가짜 숫자 금지, 적을 때는 숨김).

- 공고 상세 화면이 열려 있는 동안(탭이 보일 때만) 브라우저가 30초마다 신호를 보낸다.
- 최근 WINDOW초 안에 신호를 보낸 **서로 다른 사람**을 센다. 사람 = 접속 IP의 해시(하루마다 바뀌는 소금, 원래 IP는 저장 안 함).
  같은 사람이 탭을 여러 개 열어도 1명. 로그인과 무관(공고는 로그인 없이 본다).
- 저장: Redis 정렬 집합 `presence:job:{id}` (멤버 = IP 해시, 점수 = 마지막 신호 시각), 키는 WINDOW 뒤 자동 삭제.
- **VIEWERS_MIN(기본 10) 미만이면 서버가 숫자를 내보내지 않는다** → 화면은 아무것도 표시하지 않는다.
- Redis 장애면 조용히 '표시 안 함'(공고 화면은 그대로 동작).
"""
import hashlib
import os
import time
from datetime import date

from . import vectorstore

WINDOW = 120                                      # 초
MIN_SHOW = int(os.getenv("VIEWERS_MIN", "10"))
KEY = "presence:job:{}"


def _who(ip):
    salt = f"{date.today().isoformat()}:{os.getenv('PRESENCE_SALT', 'hh-presence')}"
    return hashlib.sha256(f"{salt}:{ip}".encode()).hexdigest()[:20]


def _visible(n):
    return n if n >= MIN_SHOW else None


def beat(job_id, ip):
    """신호 1회 → 지금 보는 사람 수(표시 기준 미만이면 None)."""
    try:
        r = vectorstore.client()
        k, now = KEY.format(int(job_id)), time.time()
        p = r.pipeline()
        p.zadd(k, {_who(ip): now})
        p.zremrangebyscore(k, 0, now - WINDOW)
        p.zcard(k)
        p.expire(k, WINDOW + 30)
        n = p.execute()[2]
        return _visible(int(n))
    except Exception:
        return None


def counts(job_ids):
    """목록 화면용 {job_id: n} — 표시 기준 이상인 공고만."""
    out = {}
    try:
        r = vectorstore.client()
        now = time.time()
        p = r.pipeline()
        for j in job_ids:
            p.zcount(KEY.format(int(j)), now - WINDOW, "+inf")
        for j, n in zip(job_ids, p.execute()):
            v = _visible(int(n))
            if v:
                out[int(j)] = v
    except Exception:
        return {}
    return out


def client_ip(request):
    fwd = request.headers.get("x-forwarded-for", "")
    return (fwd.split(",")[0].strip() if fwd else "") or (request.client.host if request.client else "unknown")
