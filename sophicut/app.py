"""SophiCut local server: upload -> local AI transcription -> transcript-based cut editing."""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import traceback
import uuid
import webbrowser
from pathlib import Path
from urllib.parse import unquote

from flask import Flask, abort, jsonify, request, send_file

from . import REPO, __version__, editing, media, subrender, transcriber

ROOT = Path(__file__).resolve().parent
FROZEN = getattr(sys, "frozen", False)  # installed build (SophiCut.exe)
APPDATA = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "SophiCut"


def _data_dir() -> Path:
    if os.environ.get("SOPHICUT_DATA"):
        return Path(os.environ["SOPHICUT_DATA"])
    return APPDATA / "data" if FROZEN else ROOT.parent / "workspace"


DATA = _data_dir()
PROJECTS = DATA / "projects"
PROJECTS.mkdir(parents=True, exist_ok=True)

app = Flask(__name__, static_folder=str(ROOT / "static"), static_url_path="/static")
_lock = threading.RLock()
STATUS: dict[str, dict] = {}
PID_RE = re.compile(r"^[a-f0-9]{12}$")


# ---------- project storage ----------
def pdir(pid: str) -> Path:
    if not PID_RE.match(pid):
        abort(404)
    d = PROJECTS / pid
    if not d.is_dir():
        abort(404)
    return d


def load(pid: str) -> dict:
    with _lock:
        return json.loads((pdir(pid) / "project.json").read_text(encoding="utf-8"))


def save(p: dict) -> None:
    with _lock:
        f = PROJECTS / p["id"] / "project.json"
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(p, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, f)


def mutate(pid: str, fn) -> dict:
    with _lock:
        p = load(pid)
        fn(p)
        save(p)
        return p


def set_status(pid: str, job: str, **kw) -> None:
    with _lock:
        STATUS.setdefault(pid, {}).setdefault(job, {}).update(kw)


def get_status(pid: str) -> dict:
    with _lock:
        return json.loads(json.dumps(STATUS.get(pid, {})))


# ---------- background jobs ----------
def _job(fn):
    def wrapper(*a):
        threading.Thread(target=fn, args=a, daemon=True).start()
    return wrapper


@_job
def run_transcribe(pid: str, model: str) -> None:
    set_status(pid, "transcribe", state="running", progress=0, message="준비 중…", error=None)
    try:
        p = load(pid)
        src = str(PROJECTS / pid / p["source"])
        t0 = time.time()
        segs, device = transcriber.transcribe(
            src, model, "ko", lambda f, m: set_status(pid, "transcribe", progress=f, message=m))
        clips = editing.build_clips(segs, p["meta"]["duration"])

        def apply(p):
            p["clips"] = clips
            p["transcribed"] = True
            p["model"] = model
            p["device"] = device
        mutate(pid, apply)
        set_status(pid, "transcribe", state="done", progress=1,
                   message=f"받아쓰기 완료 ({device.upper()}, {time.time() - t0:.0f}초)")
    except Exception as e:
        traceback.print_exc()
        set_status(pid, "transcribe", state="error", error=str(e), message=str(e))


@_job
def run_proxy(pid: str) -> None:
    set_status(pid, "preview", state="running", progress=0)
    try:
        p = load(pid)
        media.make_proxy(str(PROJECTS / pid / p["source"]), str(PROJECTS / pid / "preview.mp4"), p["meta"],
                         lambda f: set_status(pid, "preview", progress=f))
        mutate(pid, lambda p: p.update(preview="preview.mp4"))
        set_status(pid, "preview", state="done", progress=1)
    except Exception as e:
        traceback.print_exc()
        set_status(pid, "preview", state="error", error=str(e))


@_job
def run_export(pid: str, subtitles: bool = False) -> None:
    set_status(pid, "export", state="running", progress=0, file=None, error=None)
    try:
        p = load(pid)
        ranges = editing.kept_ranges(p["clips"])
        out_dir = PROJECTS / pid / "exports"
        out_dir.mkdir(exist_ok=True)
        tag = "편집_자막" if subtitles else "편집"
        name = f"{Path(p['name']).stem}_{tag}_{time.strftime('%m%d_%H%M%S')}.mp4"
        style = subrender.merge_style(p.get("style"))
        tm = editing.TimeMap(ranges)
        images = []
        for o in p.get("overlays", []):
            f = PROJECTS / pid / "assets" / Path(o["file"]).name
            if f.is_file():
                images.append({**o, "path": str(f), "start": tm(o["start"]), "end": tm(o["end"])})
        subs = (editing.subtitle_lines(p["clips"], int(style["maxChars"])), style) if subtitles else None
        media.export_cut(str(PROJECTS / pid / p["source"]), ranges, str(out_dir / name), p["meta"],
                         lambda f: set_status(pid, "export", progress=f), subtitles=subs, images=images)
        set_status(pid, "export", state="done", progress=1, file=name)
    except Exception as e:
        traceback.print_exc()
        set_status(pid, "export", state="error", error=str(e))


# ---------- routes ----------
@app.get("/")
def index():
    return send_file(ROOT / "static" / "index.html")


@app.get("/api/system")
def system():
    gpu = transcriber.gpu_available()
    hw = gpu or transcriber.gpu_hardware()
    return jsonify(app="sophicut", version=__version__, gpu=gpu, gpu_hardware=hw,
                   gpu_download_mb=transcriber.GPU_DOWNLOAD_MB, gpu_job=get_status("_app").get("gpu", {}),
                   ffmpeg=media.ffmpeg_ok(), models=transcriber.MODELS,
                   default_model="large-v3-turbo" if gpu else "small")


# ---------- app lifecycle: update check, GPU download, heartbeat, quit ----------
_update_cache: dict = {}


def _ver(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3])


@app.get("/api/update")
def check_update():
    """Asks GitHub for the latest release (cached for an hour)."""
    now = time.time()
    if not _update_cache or now - _update_cache["t"] > 3600:
        info = {"current": __version__, "latest": None, "available": False}
        try:
            import urllib.request
            req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/latest",
                                         headers={"Accept": "application/vnd.github+json",
                                                  "User-Agent": "SophiCut-update-check"})
            with urllib.request.urlopen(req, timeout=5) as r:
                rel = json.load(r)
            latest = rel.get("tag_name", "").lstrip("v")
            setup = next((a["browser_download_url"] for a in rel.get("assets", [])
                          if a["name"].lower().endswith(".exe")), None)
            info.update(latest=latest, available=_ver(latest) > _ver(__version__),
                        url=setup or rel.get("html_url"), page=rel.get("html_url"),
                        notes=(rel.get("body") or "")[:2000])
        except Exception as e:  # offline, no release yet, ... -> just no banner
            info["error"] = str(e)
        _update_cache.update(t=now, info=info)
    return jsonify(_update_cache["info"])


def run_gpu_install() -> None:
    def work():
        set_status("_app", "gpu", state="running", progress=0, message="준비 중…", error=None)
        try:
            transcriber.install_gpu_libs(lambda f, m: set_status("_app", "gpu", progress=f, message=m))
            set_status("_app", "gpu", state="done", progress=1, message="GPU 가속을 켰습니다.")
        except Exception as e:
            traceback.print_exc()
            set_status("_app", "gpu", state="error", error=str(e), message=str(e))
    threading.Thread(target=work, daemon=True).start()


@app.post("/api/gpu/install")
def gpu_install():
    if get_status("_app").get("gpu", {}).get("state") == "running":
        return jsonify(ok=True)
    if not transcriber.gpu_hardware():
        return jsonify(error="NVIDIA 그래픽카드를 찾지 못했습니다."), 400
    run_gpu_install()
    return jsonify(ok=True)


@app.get("/api/gpu/status")
def gpu_status():
    return jsonify(get_status("_app").get("gpu", {}))


_last_beat = {"t": None}


@app.post("/api/heartbeat")
def heartbeat():
    _last_beat["t"] = time.time()
    return jsonify(ok=True)


def _busy() -> bool:
    with _lock:
        return any(j.get("state") == "running" for jobs in STATUS.values() for j in jobs.values())


@app.post("/api/quit")
def quit_app():
    if _busy() and not (request.get_json(silent=True) or {}).get("force"):
        return jsonify(error="아직 작업 중입니다(받아쓰기·내보내기). 끝난 뒤에 종료해 주세요."), 409
    threading.Timer(0.5, lambda: os._exit(0)).start()
    return jsonify(ok=True)


def _watchdog(idle_limit: float = 180) -> None:
    """Installed build has no console window: exit once no browser tab has pinged for a while."""
    started = time.time()
    while True:
        time.sleep(15)
        last = _last_beat["t"] or started
        if time.time() - last > idle_limit and not _busy():
            os._exit(0)


@app.get("/api/projects")
def list_projects():
    items = []
    for f in PROJECTS.glob("*/project.json"):
        try:
            p = json.loads(f.read_text(encoding="utf-8"))
            items.append({k: p.get(k) for k in ("id", "name", "created", "transcribed")} |
                         {"duration": p["meta"]["duration"]})
        except Exception:
            continue
    items.sort(key=lambda x: x["created"] or 0, reverse=True)
    return jsonify(items)


@app.post("/api/projects")
def create_project():
    name = unquote(request.headers.get("X-Filename", "video.mp4"))
    name = re.sub(r'[\\/:*?"<>|]', "_", name)[-120:] or "video.mp4"
    ext = Path(name).suffix.lower() or ".mp4"
    model = request.args.get("model", "large-v3-turbo")
    if model not in transcriber.MODELS:
        abort(400)
    pid = uuid.uuid4().hex[:12]
    d = PROJECTS / pid
    d.mkdir()
    src = d / f"source{ext}"
    with open(src, "wb") as f:
        while chunk := request.stream.read(1 << 20):
            f.write(chunk)
    try:
        meta = media.probe(str(src))
    except Exception as e:
        import shutil
        shutil.rmtree(d, ignore_errors=True)
        return jsonify(error=str(e)), 400
    p = {"id": pid, "name": name, "source": src.name, "preview": None, "meta": meta,
         "created": time.time(), "transcribed": False, "clips": [], "overlays": [],
         "style": subrender.merge_style(None)}
    if not media.needs_proxy(str(src), meta):
        p["preview"] = src.name
    save(p)
    if not p["preview"]:
        run_proxy(pid)
    run_transcribe(pid, model)
    return jsonify(id=pid)


@app.get("/api/projects/<pid>")
def get_project(pid):
    p = load(pid)
    p["style"] = subrender.merge_style(p.get("style"))
    p.setdefault("overlays", [])
    return jsonify(project=p, status=get_status(pid))


@app.get("/fonts/<path:fname>")
def fonts(fname):
    f = (subrender.FONT_DIR / fname).resolve()
    if f.parent != subrender.FONT_DIR or not f.is_file():
        abort(404)
    return send_file(f, max_age=86400 * 30)


# ---------- images ----------
IMG_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}


@app.post("/api/projects/<pid>/assets")
def upload_asset(pid):
    d = pdir(pid) / "assets"
    d.mkdir(exist_ok=True)
    ext = Path(unquote(request.headers.get("X-Filename", "image.png"))).suffix.lower()
    if ext not in IMG_EXTS:
        return jsonify(error="PNG, JPG, WEBP, GIF, BMP 이미지만 넣을 수 있습니다."), 400
    name = uuid.uuid4().hex[:10] + ext
    (d / name).write_bytes(request.get_data())
    try:
        from PIL import Image
        with Image.open(d / name) as im:
            w, h = im.size
    except Exception:
        (d / name).unlink(missing_ok=True)
        return jsonify(error="이미지를 열 수 없습니다."), 400
    return jsonify(file=name, width=w, height=h)


@app.get("/api/projects/<pid>/assets/<fname>")
def get_asset(pid, fname):
    f = pdir(pid) / "assets" / Path(fname).name
    if not f.is_file():
        abort(404)
    return send_file(f, max_age=86400)


# ---------- thumbnails ----------
_thumb_sem = threading.Semaphore(3)


@app.get("/api/projects/<pid>/thumb")
def thumb(pid):
    p = load(pid)
    if not p["meta"]["has_video"]:
        abort(404)
    t = max(0.0, float(request.args.get("t", 0)))
    d = pdir(pid) / "thumbs"
    d.mkdir(exist_ok=True)
    f = d / f"{int(t * 10)}.jpg"
    if not f.is_file():
        src = pdir(pid) / (p.get("preview") or p["source"])
        with _thumb_sem:
            media._run(["ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", str(src), "-frames:v", "1",
                        "-vf", "scale=240:-2", "-q:v", "6", str(f)])
    if not f.is_file():
        abort(404)
    return send_file(f, max_age=86400)


# ---------- subtitle style presets ----------
BUILTIN_PRESETS = [
    {"id": "b1", "name": "기본 테두리", "style": {"weight": 700, "color": "#ffffff",
     "outline": {"on": True, "color": "#000000", "width": 5}}},
    {"id": "b2", "name": "반투명 박스", "style": {"weight": 600, "color": "#ffffff", "outline": {"on": False},
     "bg": {"on": True, "color": "#000000", "opacity": 0.6, "pad": 18, "radius": 10}}},
    {"id": "b3", "name": "노랑 강조", "style": {"weight": 800, "color": "#ffe14d",
     "outline": {"on": True, "color": "#111111", "width": 6}}},
    {"id": "b4", "name": "형광펜", "style": {"weight": 700, "color": "#111111", "outline": {"on": False},
     "highlight": {"on": True, "color": "#ffe14d"}, "bg": {"pad": 10}}},
    {"id": "b5", "name": "흰 박스", "style": {"weight": 700, "color": "#111111", "outline": {"on": False},
     "bg": {"on": True, "color": "#ffffff", "opacity": 1, "pad": 18, "radius": 8}}},
    {"id": "b6", "name": "굵은 예능", "style": {"weight": 900, "color": "#ffffff", "size": 72,
     "outline": {"on": True, "color": "#1b1b1b", "width": 8},
     "shadow": {"on": True, "color": "#000000", "opacity": 0.6, "x": 0, "y": 6, "blur": 6}}},
    {"id": "b7", "name": "부드러운 그림자", "style": {"weight": 500, "color": "#ffffff", "outline": {"on": False},
     "shadow": {"on": True, "color": "#000000", "opacity": 0.85, "x": 0, "y": 3, "blur": 10}}},
    {"id": "b8", "name": "컬러 박스", "style": {"weight": 700, "color": "#ffffff", "outline": {"on": False},
     "bg": {"on": True, "color": "#5b5cf0", "opacity": 1, "pad": 18, "radius": 12}}},
]
PRESET_FILE = DATA / "presets.json"


def _user_presets() -> list:
    try:
        return json.loads(PRESET_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


@app.get("/api/presets")
def presets():
    items = [{**x, "builtin": True, "style": subrender.merge_style(x["style"])} for x in BUILTIN_PRESETS]
    items += [{**x, "builtin": False} for x in _user_presets()]
    return jsonify(items)


@app.post("/api/presets")
def add_preset():
    body = request.get_json(force=True)
    item = {"id": uuid.uuid4().hex[:8], "name": str(body.get("name") or "내 서식")[:30],
            "style": subrender.merge_style(body.get("style"))}
    with _lock:
        items = _user_presets() + [item]
        PRESET_FILE.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    return jsonify(item)


@app.delete("/api/presets/<preset_id>")
def del_preset(preset_id):
    with _lock:
        items = [x for x in _user_presets() if x["id"] != preset_id]
        PRESET_FILE.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    return jsonify(ok=True)


@app.get("/api/projects/<pid>/status")
def status(pid):
    pdir(pid)
    return jsonify(get_status(pid))


@app.put("/api/projects/<pid>")
def put_project(pid):
    body = request.get_json(force=True)
    upd = {k: body[k] for k in ("clips", "overlays", "style") if k in body}
    if not upd or not all(isinstance(upd.get(k, []), list) for k in ("clips", "overlays")):
        abort(400)
    mutate(pid, lambda p: p.update(upd))
    return jsonify(ok=True)


@app.delete("/api/projects/<pid>")
def delete_project(pid):
    import shutil
    d = pdir(pid)
    shutil.rmtree(d, ignore_errors=True)
    STATUS.pop(pid, None)
    return jsonify(ok=True)


@app.post("/api/projects/<pid>/transcribe")
def retranscribe(pid):
    pdir(pid)
    model = (request.get_json(silent=True) or {}).get("model", "large-v3-turbo")
    if model not in transcriber.MODELS:
        abort(400)
    if get_status(pid).get("transcribe", {}).get("state") == "running":
        return jsonify(error="이미 받아쓰는 중입니다."), 409
    run_transcribe(pid, model)
    return jsonify(ok=True)


@app.get("/api/projects/<pid>/media")
def media_file(pid):
    p = load(pid)
    if not p.get("preview"):
        abort(404)
    return send_file(pdir(pid) / p["preview"], conditional=True)


@app.post("/api/projects/<pid>/export")
def export(pid):
    kind = (request.get_json(silent=True) or {}).get("kind", "mp4")
    p = load(pid)
    if kind in ("mp4", "mp4sub"):
        if get_status(pid).get("export", {}).get("state") == "running":
            return jsonify(error="이미 내보내는 중입니다."), 409
        run_export(pid, kind == "mp4sub")
        return jsonify(ok=True)
    if kind not in ("srt", "txt"):
        abort(400)
    out_dir = pdir(pid) / "exports"
    out_dir.mkdir(exist_ok=True)
    text = editing.to_srt(p["clips"], int(subrender.merge_style(p.get("style"))["maxChars"])) if kind == "srt" else editing.to_txt(p["clips"])
    name = f"{Path(p['name']).stem}_편집.{kind}"
    (out_dir / name).write_text(text, encoding="utf-8-sig" if kind == "srt" else "utf-8")
    return jsonify(ok=True, file=name)


@app.get("/api/projects/<pid>/exports/<path:fname>")
def download(pid, fname):
    f = (pdir(pid) / "exports" / fname).resolve()
    if f.parent != (pdir(pid) / "exports").resolve() or not f.is_file():
        abort(404)
    return send_file(f, as_attachment=True, download_name=fname, conditional=True)


@app.post("/api/projects/<pid>/reveal")
def reveal(pid):
    d = pdir(pid) / "exports"
    d.mkdir(exist_ok=True)
    if os.name == "nt":
        os.startfile(d)  # noqa: S606 - local desktop app
    return jsonify(ok=True, path=str(d))


def _alert(msg: str) -> None:
    print("\n  " + msg.replace("\n", "\n  ") + "\n")
    if FROZEN and os.name == "nt":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, msg, "소피컷", 0x40)


def _probe_port(port: int) -> str:
    """'free', 'sophicut' (already running) or 'busy' (another program)."""
    import socket
    import urllib.request
    with socket.socket() as sk:
        if sk.connect_ex(("127.0.0.1", port)) != 0:
            return "free"
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=2) as r:
            return "sophicut" if json.load(r).get("app") == "sophicut" else "busy"
    except Exception:
        return "busy"


@app.get("/api/ping")
def ping():
    return jsonify(app="sophicut", version=__version__)


def main():
    if os.environ.get("SOPHICUT_PORT"):
        ports = [int(os.environ["SOPHICUT_PORT"])]
    else:
        ports = list(range(7860, 7870))
    if not media.ensure_ffmpeg():
        _alert("ffmpeg를 찾을 수 없습니다.\n명령 프롬프트에서 다음을 실행한 뒤 다시 시작해 주세요:\n"
               "    winget install Gyan.FFmpeg")
        return
    port = None
    for p in ports:
        state = _probe_port(p)
        if state == "sophicut":  # already running -> just open it
            print(f"\n  소피컷이 이미 실행 중입니다 → http://127.0.0.1:{p}\n")
            webbrowser.open(f"http://127.0.0.1:{p}")
            return
        if state == "free":
            port = p
            break
    if port is None:
        _alert("소피컷을 시작할 빈 포트를 찾지 못했습니다. 컴퓨터를 다시 시작한 뒤 실행해 주세요.")
        return
    url = f"http://127.0.0.1:{port}"
    print(f"\n  소피컷 {__version__} 실행 중 → {url}\n  (종료하려면 이 창에서 Ctrl+C)\n")
    if FROZEN or os.environ.get("SOPHICUT_AUTO_EXIT"):
        threading.Thread(target=_watchdog, daemon=True).start()
    if not os.environ.get("SOPHICUT_NO_BROWSER"):
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=port, threaded=True, debug=False, load_dotenv=False)
