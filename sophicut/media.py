"""ffmpeg helpers: probing, browser preview proxy, cut export."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

NO_WINDOW = 0x08000000 if os.name == "nt" else 0
BROWSER_VCODECS = {"h264", "vp8", "vp9", "av1"}
BROWSER_ACODECS = {"aac", "mp3", "opus", "vorbis", None}
BROWSER_EXTS = {".mp4", ".m4v", ".mov", ".webm"}


def _candidate_dirs():
    """PATH entries from the registry (Explorer may hold a stale PATH) + winget installs."""
    if os.name != "nt":
        return []
    dirs = []
    try:
        import winreg

        for hive, key in ((winreg.HKEY_CURRENT_USER, r"Environment"),
                          (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
            try:
                with winreg.OpenKey(hive, key) as k:
                    val, _ = winreg.QueryValueEx(k, "Path")
                    dirs += [os.path.expandvars(p) for p in val.split(";") if p]
            except OSError:
                pass
    except ImportError:
        pass
    local = os.environ.get("LOCALAPPDATA", "")
    dirs.append(os.path.join(local, "Microsoft", "WinGet", "Links"))
    pk = Path(local, "Microsoft", "WinGet", "Packages")
    if pk.is_dir():
        dirs += [str(p.parent) for p in pk.glob("*FFmpeg*/**/bin/ffmpeg.exe")]
    dirs += [r"C:\ffmpeg\bin", r"C:\Program Files\ffmpeg\bin"]
    return dirs


def _bundled_dir() -> str | None:
    """ffmpeg shipped with the installed app (Windows: next to SophiCut.exe, macOS: inside the .app)."""
    import sys

    if getattr(sys, "frozen", False):
        exe = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
        for base in (os.path.dirname(sys.executable), getattr(sys, "_MEIPASS", "")):
            d = os.path.join(base, "ffmpeg")
            if base and os.path.isfile(os.path.join(d, exe)):
                return d
    return None


def ensure_ffmpeg() -> bool:
    """Makes ffmpeg/ffprobe callable by name; returns False if not installed."""
    b = _bundled_dir()
    if b:
        if not os.environ.get("PATH", "").startswith(b + os.pathsep):
            os.environ["PATH"] = b + os.pathsep + os.environ.get("PATH", "")
        return True
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return True
    for d in _candidate_dirs():
        if os.path.isfile(os.path.join(d, "ffmpeg.exe")) and os.path.isfile(os.path.join(d, "ffprobe.exe")):
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
            return True
    return False


def ffmpeg_ok() -> bool:
    return ensure_ffmpeg()


def _run(args, **kw):
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          creationflags=NO_WINDOW, **kw)


def probe(path: str) -> dict:
    r = _run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path])
    if r.returncode != 0:
        raise RuntimeError("영상 정보를 읽을 수 없습니다: " + r.stderr.strip()[-300:])
    info = json.loads(r.stdout)
    v = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    if not a:
        raise RuntimeError("오디오 트랙이 없는 파일입니다.")
    return {
        "duration": float(info["format"].get("duration") or 0),
        "vcodec": v and v["codec_name"],
        "acodec": a["codec_name"],
        "width": v and v.get("width"),
        "height": v and v.get("height"),
        "pix_fmt": v and v.get("pix_fmt"),
        "has_video": bool(v),
        "fps": _fps(v),
    }


def _fps(v) -> float:
    for key in ("avg_frame_rate", "r_frame_rate"):
        try:
            n, d = (v or {}).get(key, "0/0").split("/")
            if float(d) and 1 <= float(n) / float(d) <= 240:
                return round(float(n) / float(d), 3)
        except (ValueError, ZeroDivisionError):
            pass
    return 30.0


def needs_proxy(path: str, meta: dict) -> bool:
    if Path(path).suffix.lower() not in BROWSER_EXTS:
        return True
    if meta["has_video"] and (meta["vcodec"] not in BROWSER_VCODECS or meta["pix_fmt"] not in ("yuv420p", "yuvj420p")):
        return True
    return meta["acodec"] not in BROWSER_ACODECS


def _ffmpeg_progress(args, duration, cb, base=0.0, span=1.0, cwd=None):
    """Runs ffmpeg with -progress and reports fraction via cb."""
    p = subprocess.Popen(args[:1] + ["-progress", "pipe:1", "-nostats"] + args[1:], cwd=cwd,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    err_tail: list[str] = []

    import threading

    def drain():
        for line in p.stderr:
            err_tail.append(line)
            del err_tail[:-30]

    t = threading.Thread(target=drain, daemon=True)
    t.start()
    for line in p.stdout:
        if line.startswith("out_time_us=") and duration:
            try:
                cb(base + span * min(int(line.split("=")[1]) / 1e6 / duration, 1.0))
            except ValueError:
                pass
    p.wait()
    t.join(timeout=2)
    if p.returncode != 0:
        raise RuntimeError("ffmpeg 오류: " + "".join(err_tail)[-600:])


def make_proxy(src: str, dst: str, meta: dict, cb):
    tmp = dst + ".part.mp4"
    args = ["ffmpeg", "-y", "-i", src]
    if meta["has_video"]:
        args += ["-map", "0:v:0", "-vf", "scale=-2:'min(720,ih)'", "-c:v", "libx264", "-preset", "ultrafast",
                 "-crf", "26", "-pix_fmt", "yuv420p", "-g", "30"]
    args += ["-map", "0:a:0", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", tmp]
    _ffmpeg_progress(args, meta["duration"], cb)
    os.replace(tmp, dst)


def _nvenc_ok() -> bool:
    r = _run(["ffmpeg", "-hide_banner", "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.2",
              "-c:v", "h264_nvenc", "-f", "null", "-"])
    return r.returncode == 0


SHORT_W, SHORT_H = 1080, 1920


def export_cut(src: str, ranges, out: str, meta: dict, cb, subtitles=None, images=(), layout=None):
    """Encodes each kept range to an intermediate (h264 + PCM), then concatenates.

    Per-piece encoding keeps memory flat regardless of the number of cuts and
    is frame-accurate; audio is re-encoded once at the end so there are no AAC
    priming gaps at the joins. A 6 ms fade at each join prevents clicks.

    subtitles: (lines, style) -> rendered to PNGs and overlaid (output times).
    images: [{path, start, end, x, y, w, opacity}] in output time / frame fractions.
    layout: None (keep the frame) or a vertical 1080x1920 short: "crop" fills the screen by
            cutting the sides, "fit" shows the whole frame over a blurred copy of itself.
    """
    if layout and not meta["has_video"]:
        raise RuntimeError("영상이 없는 파일(소리만 있는 파일)은 세로 숏폼으로 만들 수 없습니다.")
    if not ranges:
        raise RuntimeError("남은 구간이 없습니다. 삭제를 일부 복원해 주세요.")
    total = sum((e - s) / sp for s, e, sp in ranges)
    fps = meta.get("fps") or 30.0
    images = [im for im in images if im["end"] - im["start"] > 0.04] if meta["has_video"] else []
    burn = meta["has_video"] and bool(subtitles and subtitles[0])
    compose = burn or bool(images) or bool(layout)
    split = 0.6 if compose else 0.95  # share of progress for the per-piece pass
    x264 = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18"]
    venc = ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "20"] if (meta["has_video"] and _nvenc_ok()) else x264

    def encode(build, dur, base, span, cwd=None):
        """Runs ffmpeg; if the GPU encoder fails (driver, size limit, busy), retries once on the CPU."""
        nonlocal venc
        try:
            _ffmpeg_progress(build(venc), dur, cb, base=base, span=span, cwd=cwd)
        except RuntimeError:
            if venc is x264:
                raise
            venc = x264
            _ffmpeg_progress(build(venc), dur, cb, base=base, span=span, cwd=cwd)
    work = tempfile.mkdtemp(prefix="sophicut_")
    try:
        listing = []
        done = 0.0
        for i, (s, e, sp) in enumerate(ranges):
            d = (e - s) / sp  # output duration of this piece
            piece = os.path.join(work, f"p{i:05}.mkv")
            fade = min(0.006, d / 4)

            def piece_args(vc, s=s, e=e, sp=sp, d=d, fade=fade, piece=piece):
                args = ["ffmpeg", "-y", "-ss", f"{s:.3f}", "-t", f"{e - s:.3f}", "-i", src]
                if meta["has_video"]:
                    args += ["-map", "0:v:0"]
                    # speed effect retimes frames; fps keeps every piece at the same constant rate
                    vf = (f"setpts=PTS/{sp:.4f}," if sp != 1 else "") + f"fps={fps}"
                    args += ["-vf", vf, *vc, "-pix_fmt", "yuv420p"]
                tempo = f"atempo={sp:.4f}," if sp != 1 else ""  # atempo keeps the pitch
                args += ["-map", "0:a:0", "-af",
                         f"{tempo}afade=t=in:d={fade:.4f},afade=t=out:st={max(d - fade, 0):.4f}:d={fade:.4f}",
                         "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", piece]
                return args
            encode(piece_args, d, split * done / total, split * d / total)
            done += d
            listing.append(f"file '{piece.replace(chr(92), '/')}'")
        Path(work, "list.txt").write_text(chr(10).join(listing), encoding="utf-8")
        tmp_out = os.path.abspath(out + ".part.mp4")
        args = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "list.txt"]
        vpos = None  # index in args where the video encoder options go
        if compose:
            W, H = int(meta["width"]), int(meta["height"])
            graph, cur, n = [], "[0:v]", 1
            for k, im in enumerate(images):
                args += ["-loop", "1", "-i", im["path"]]
                iw = max(2, int(W * im["w"]) // 2 * 2)
                graph.append(f"[{n}:v]scale={iw}:-2,format=rgba,"
                             f"colorchannelmixer=aa={float(im.get('opacity', 1)):.3f}[im{k}]")
                graph.append(f"{cur}[im{k}]overlay=x=main_w*{im['x']:.4f}-overlay_w/2:"
                             f"y=main_h*{im['y']:.4f}-overlay_h/2:"
                             f"enable='between(t,{im['start']:.3f},{im['end']:.3f})'[v{k}]")
                cur, n = f"[v{k}]", n + 1
            unit = None
            if layout:  # images are placed on the original frame, then the frame is made vertical
                OW, OH = SHORT_W, SHORT_H
                if layout == "fit":
                    graph.append(f"{cur}split[rfa][rfb]")
                    graph.append(f"[rfa]scale={OW // 4}:{OH // 4}:force_original_aspect_ratio=increase,"
                                 f"crop={OW // 4}:{OH // 4},boxblur=10:2,eq=brightness=-0.06,scale={OW}:{OH}[rbg]")
                    graph.append(f"[rfb]scale={OW}:{OH}:force_original_aspect_ratio=decrease[rfg]")
                    graph.append("[rbg][rfg]overlay=(W-w)/2:(H-h)/2,setsar=1[rf]")
                else:
                    graph.append(f"{cur}scale={OW}:{OH}:force_original_aspect_ratio=increase,"
                                 f"crop={OW}:{OH},setsar=1[rf]")
                cur, W, H, unit = "[rf]", OW, OH, OW
            if burn:
                cb(split)
                from . import subrender
                sub_list = subrender.render_track(subtitles[0], subtitles[1], W, H, total, Path(work, "subs"), unit)
                args += ["-f", "concat", "-safe", "0", "-i", str(sub_list)]
                graph.append(f"[{n}:v]format=rgba[subs]")
                graph.append(f"{cur}[subs]overlay=0:0:eof_action=pass[vs]")
                cur = "[vs]"
            graph.append(f"{cur}format=yuv420p[vout]")
            args += ["-filter_complex", ";".join(graph), "-map", "[vout]", "-map", "0:a"]
            vpos = len(args)
        elif meta["has_video"]:
            args += ["-c:v", "copy"]
        args += ["-c:a", "aac", "-b:a", "192k", "-t", f"{total:.3f}", "-movflags", "+faststart", tmp_out]
        final = args
        encode(lambda vc: final if vpos is None else final[:vpos] + vc + final[vpos:],
               total, split, 1 - split, cwd=work)
        os.replace(tmp_out, out)
    finally:
        shutil.rmtree(work, ignore_errors=True)
