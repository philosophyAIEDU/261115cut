"""Smoke test for a built SophiCut app (used by CI).

    python packaging/smoke_test.py <path to SophiCut executable> <test video>

Starts the app headless, uploads the video, transcribes it with the small "base" model,
cuts the first word, exports an MP4 with burned-in subtitles and an SRT, then quits.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

EXE, VIDEO = sys.argv[1], sys.argv[2]
PORT = 7899
BASE = f"http://127.0.0.1:{PORT}"


def call(path, data=None, method=None, headers=None, timeout=120):
    r = urllib.request.Request(BASE + path, data=data, method=method, headers=headers or {})
    with urllib.request.urlopen(r, timeout=timeout) as f:
        return json.load(f)


def post(path, body):
    return call(path, json.dumps(body).encode(), "POST", {"Content-Type": "application/json"})


def wait(pid, job, limit=900):
    t0 = time.time()
    while time.time() - t0 < limit:
        call("/api/heartbeat", b"", "POST")
        st = call(f"/api/projects/{pid}/status").get(job, {})
        if st.get("state") in ("done", "error"):
            return st
        time.sleep(2)
    raise SystemExit(f"{job} timed out")


data = tempfile.mkdtemp(prefix="sophicut_smoke_")
env = dict(os.environ, SOPHICUT_PORT=str(PORT), SOPHICUT_NO_BROWSER="1", SOPHICUT_DATA=data)
proc = subprocess.Popen([EXE], env=env)
try:
    for _ in range(90):
        try:
            sysinfo = call("/api/system")
            break
        except Exception:
            if proc.poll() is not None:
                raise SystemExit(f"app exited early with code {proc.returncode}")
            time.sleep(1)
    else:
        raise SystemExit("app did not start")
    print("system:", sysinfo)
    assert sysinfo["ffmpeg"], "bundled ffmpeg not found"
    assert urllib.request.urlopen(BASE + "/").status == 200

    with open(VIDEO, "rb") as f:
        pid = call("/api/projects?model=base", f.read(), "POST", {"X-Filename": "smoke.mp4"})["id"]
    st = wait(pid, "transcribe")
    print("transcribe:", ascii(st))
    assert st["state"] == "done", st

    p = call(f"/api/projects/{pid}")["project"]
    words = [t for c in p["clips"] for t in c["tokens"] if t["type"] == "word"]
    print("paragraphs:", len(p["clips"]), "words:", len(words))
    if len(words) > 1:
        words[0]["deleted"] = True
        call(f"/api/projects/{pid}", json.dumps({"clips": p["clips"]}).encode(), "PUT",
             {"Content-Type": "application/json"})

    post(f"/api/projects/{pid}/export", {"kind": "mp4sub"})
    st = wait(pid, "export")
    print("export:", ascii(st))
    assert st["state"] == "done", st
    out = os.path.join(data, "projects", pid, "exports", st["file"])
    assert os.path.getsize(out) > 10_000, "exported file too small"
    print("srt:", ascii(post(f"/api/projects/{pid}/export", {"kind": "srt"})))
    print("quit:", post("/api/quit", {}))
    proc.wait(timeout=20)
    print("SMOKE TEST PASSED")
finally:
    if proc.poll() is None:
        proc.kill()
