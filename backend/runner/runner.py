"""Code execution sandbox (PRD COD-05, TRD §8).

Runs in its own container with `network_mode: none`. The API drops job files into
/sandbox/jobs/<id>.json; this loop runs each test in a fresh `python -I` subprocess as an
unprivileged user with CPU/memory limits and writes /sandbox/results/<id>.json.
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

HARNESS = r'''
import json, sys
sys.setrecursionlimit(10000)
ns = {}
exec(compile(open(sys.argv[1], encoding="utf-8").read(), "solution.py", "exec"), ns)
fn = ns.get(sys.argv[2])
cases = json.load(open(sys.argv[3], encoding="utf-8"))
out = []
for c in cases:
    try:
        got = fn(*c["args"])
        out.append({"ok": got == c["expected"], "got": repr(got)[:300]})
    except Exception as e:
        out.append({"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]})
print("__RESULT__" + json.dumps(out))
'''


def limits():
    resource.setrlimit(resource.RLIMIT_AS, (MEM_LIMIT, MEM_LIMIT))
    resource.setrlimit(resource.RLIMIT_CPU, (TIME_LIMIT, TIME_LIMIT))
    resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
    resource.setrlimit(resource.RLIMIT_FSIZE, (1024 * 1024, 1024 * 1024))
    os.setgid(NOBODY)
    os.setuid(NOBODY)


def run_job(job):
    with tempfile.TemporaryDirectory() as d:
        os.chmod(d, 0o755)
        paths = {}
        for name, content in (("solution.py", job["code"]), ("harness.py", HARNESS), ("cases.json", json.dumps(job["tests"]))):
            p = os.path.join(d, name)
            with open(p, "w", encoding="utf-8") as f:
                f.write(content)
            os.chmod(p, 0o644)
            paths[name] = p
        try:
            proc = subprocess.run([sys.executable, "-I", paths["harness.py"], paths["solution.py"], job["func"], paths["cases.json"]],
                                  cwd=d, capture_output=True, text=True, timeout=TIME_LIMIT, preexec_fn=limits)
        except subprocess.TimeoutExpired:
            return {"status": "timeout", "results": [], "stderr": f"시간 초과 ({TIME_LIMIT}초)"}
        marker = proc.stdout.rfind("__RESULT__")
        if marker < 0 and proc.returncode in (-24, -9):  # SIGXCPU / SIGKILL: CPU time limit
            return {"status": "timeout", "results": [], "stderr": f"시간 초과 ({TIME_LIMIT}초)"}
        if marker < 0:
            err = proc.stderr.strip().splitlines()
            return {"status": "error", "results": [], "stderr": "\n".join(err[-6:])[:1200] or "실행 실패(메모리 초과 가능)"}
        return {"status": "ok", "results": json.loads(proc.stdout[marker + 10:]),
                "stdout": proc.stdout[:marker][-1500:], "stderr": proc.stderr[-800:]}


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
