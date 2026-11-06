"""Code execution sandbox (PRD COD-05, TRD §8).

Runs in its own container with `network_mode: none` (local) or as an HTTP service (Railway). Each test case
runs in a fresh `python -I` subprocess as an unprivileged user with CPU/memory limits; the child only gets the
inputs, the parent compares with the expected output. Hidden-case output/error text is never returned.
"""
import json
import os
import resource
import subprocess
import sys
import tempfile
import time

JOBS, RESULTS = "/sandbox/jobs", "/sandbox/results"
TIME_LIMIT = 10           # seconds per problem run
MEM_LIMIT = 256 * 1024 * 1024
NOBODY = 65534

# 테스트 1건 = 프로세스 1개. 자식에게는 입력(args)만 넘기고 기대 출력은 넘기지 않는다 → 비교는 부모(run_job)가 한다.
# (2026-11-06 보안 수정: 예전에는 비공개 테스트까지 cases.json으로 같은 폴더에 두어 지원자 코드가 읽을 수 있었다)
HARNESS = r"""
import json, sys
sys.setrecursionlimit(10000)
args = json.loads(sys.stdin.read())
sys.stdin = None
ns = {}
exec(compile(open(sys.argv[1], encoding="utf-8").read(), "solution.py", "exec"), ns)
fn = ns.get(sys.argv[2])
try:
    got = fn(*args)
    try:
        enc = json.dumps(got)
        back = json.loads(enc)
        same = back == got and type(back) is type(got)
    except Exception:
        enc, same = None, False
    res = {"got": repr(got)[:300], "json": enc if same else None}
except Exception as e:
    res = {"error": type(e).__name__ + ": " + str(e)[:200], "etype": type(e).__name__}
sys.stdout.flush()
sys.__stdout__.write("\n__RESULT__" + json.dumps(res))
"""
STARTUP_ALLOWANCE = 0.15   # 테스트 1건당 파이썬 기동 시간 여유(초)


def limits(cpu):
    def f():
        resource.setrlimit(resource.RLIMIT_AS, (MEM_LIMIT, MEM_LIMIT))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
        resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
        resource.setrlimit(resource.RLIMIT_FSIZE, (1024 * 1024, 1024 * 1024))
        os.setgid(NOBODY)
        os.setuid(NOBODY)
    return f


def _one(d, paths, func, args, budget):
    """케이스 1건 실행 → ('ok'|'timeout'|'crash', 결과 dict, stdout, stderr)."""
    try:
        proc = subprocess.run([sys.executable, "-I", paths["harness.py"], paths["solution.py"], func],
                              input=json.dumps(args), cwd=d, capture_output=True, text=True,
                              timeout=budget, preexec_fn=limits(max(1, int(budget + 0.999))))
    except subprocess.TimeoutExpired:
        return "timeout", None, "", ""
    marker = proc.stdout.rfind("\n__RESULT__")
    if marker < 0:
        if proc.returncode in (-24, -9):  # SIGXCPU / SIGKILL: CPU 시간 제한
            return "timeout", None, "", ""
        return "crash", None, proc.stdout, proc.stderr
    try:
        res = json.loads(proc.stdout[marker + 11:])
    except json.JSONDecodeError:
        return "crash", None, proc.stdout, proc.stderr
    return "ok", res, proc.stdout[:marker], proc.stderr


def run_job(job):
    """job = {code, func, tests:[{args, expected, public?}]}.
    public이 False인 케이스(비공개 테스트)는 통과 여부와 오류 종류만 돌려주고, 반환값·출력·오류 문구는 버린다
    (지원자가 print·예외 메시지로 비공개 입력을 빼내지 못하게). public 키가 없으면 공개로 본다(내부 검증 에이전트용)."""
    tests = job.get("tests") or []
    deadline = time.time() + TIME_LIMIT + STARTUP_ALLOWANCE * len(tests)
    out, stdout_pub, stderr_pub = [], [], []
    with tempfile.TemporaryDirectory() as d:
        os.chmod(d, 0o755)
        paths = {}
        for name, content in (("solution.py", job["code"]), ("harness.py", HARNESS)):
            p = os.path.join(d, name)
            with open(p, "w", encoding="utf-8") as f:
                f.write(content)
            os.chmod(p, 0o644)
            paths[name] = p
        for i, c in enumerate(tests):
            public = c.get("public", True) is not False
            budget = deadline - time.time()
            if budget <= 0:
                return {"status": "timeout", "results": [], "stderr": f"시간 초과 ({TIME_LIMIT}초)"}
            kind, res, so, se = _one(d, paths, job["func"], c["args"], budget)
            if kind == "timeout":
                return {"status": "timeout", "results": [], "stderr": f"시간 초과 ({TIME_LIMIT}초)"}
            if kind == "crash":
                if i == 0:  # 첫 케이스부터 실행이 안 되면 프로그램 오류(문법 오류·메모리 초과 등)
                    err = se.strip().splitlines()
                    msg = ("\n".join(err[-6:])[:1200] if public else "") or "실행 실패(메모리 초과 가능)"
                    return {"status": "error", "results": [], "stderr": msg}
                out.append({"ok": False, "error": "실행 실패" if not public else (se.strip().splitlines() or ["실행 실패"])[-1][:200]})
                continue
            if "error" in res:
                r = {"ok": False, "error": res["error"] if public else res.get("etype", "Error")}
            else:
                ok = res.get("json") is not None and json.loads(res["json"]) == c.get("expected")
                r = {"ok": ok, "got": res["got"]} if public else {"ok": ok}
            out.append(r)
            if public:
                stdout_pub.append(so)
                stderr_pub.append(se)
    return {"status": "ok", "results": out, "stdout": "".join(stdout_pub)[-1500:], "stderr": "".join(stderr_pub)[-800:]}


def serve_http(port):
    """Railway용: POST /run {code, func, tests} → 결과 JSON. x-runner-token 헤더가 RUNNER_TOKEN과 같아야 한다.
    한 번에 하나씩 실행(동시 실행 제한). 네트워크 차단은 없다(Railway 제약)."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import socket
    token = os.getenv("RUNNER_TOKEN", "")
    lock = threading.Lock()

    class H(BaseHTTPRequestHandler):
        def _send(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code); self.send_header("content-type", "application/json"); self.send_header("content-length", str(len(body)))
            self.end_headers(); self.wfile.write(body)

        def do_GET(self):
            self._send(200, {"ok": True})

        def do_POST(self):
            if self.path != "/run" or not token or self.headers.get("x-runner-token") != token:
                return self._send(403, {"status": "error", "results": [], "stderr": "forbidden"})
            n = min(int(self.headers.get("content-length") or 0), 200_000)
            try:
                job = json.loads(self.rfile.read(n))
            except json.JSONDecodeError:
                return self._send(400, {"status": "error", "results": [], "stderr": "bad request"})
            with lock:
                self._send(200, run_job(job))

        def log_message(self, *a):
            pass

    class S(ThreadingHTTPServer):
        address_family = socket.AF_INET6  # Railway 내부망은 IPv6

        def server_bind(self):
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            super().server_bind()

    print(f"sandbox runner http :{port}", flush=True)
    S(("::", port), H).serve_forever()


def main():
    if os.getenv("RUNNER_HTTP_PORT"):
        return serve_http(int(os.environ["RUNNER_HTTP_PORT"]))
    os.makedirs(JOBS, exist_ok=True)
    os.makedirs(RESULTS, exist_ok=True)
    print("sandbox runner ready", flush=True)
    while True:
        for name in sorted(os.listdir(JOBS)):
            if not name.endswith(".json"):
                continue
            src = os.path.join(JOBS, name)
            try:
                with open(src, encoding="utf-8") as f:
                    job = json.load(f)
                os.remove(src)
            except (OSError, json.JSONDecodeError):
                continue
            res = run_job(job)
            tmp = os.path.join(RESULTS, name + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(res, f)
            os.replace(tmp, os.path.join(RESULTS, name))
        time.sleep(0.1)


if __name__ == "__main__":
    main()
