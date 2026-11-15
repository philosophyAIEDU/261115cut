"""Local speech-to-text with faster-whisper (GPU when available, CPU fallback)."""
from __future__ import annotations

import os
import site
import threading

_DLL_READY = False
_MODELS: dict[tuple[str, str], object] = {}
_MODEL_LOCK = threading.Lock()

MODELS = ["large-v3-turbo", "large-v3", "medium", "small", "base"]

# GPU libraries downloaded on demand by the installed app (not bundled: ~1.3GB).
CUDA_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "SophiCut", "cuda")
GPU_WHEELS = [  # (package, version) - versions verified with ctranslate2 4.x
    ("nvidia-cublas-cu12", "12.9.2.10"),
    ("nvidia-cudnn-cu12", "9.28.1.4"),
    ("nvidia-cuda-nvrtc-cu12", "12.9.86"),
]
GPU_DOWNLOAD_MB = 1330


def _add_dll_dir(d: str) -> None:
    if not os.path.isdir(d):
        return
    try:
        os.add_dll_directory(d)
    except (OSError, AttributeError):
        pass
    os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")


def _prepare_cuda_dlls(force: bool = False) -> None:
    """pip's nvidia-* wheels put cuBLAS/cuDNN DLLs in site-packages/nvidia/*/bin."""
    global _DLL_READY
    if (_DLL_READY and not force) or os.name != "nt":
        return
    _DLL_READY = True
    _add_dll_dir(CUDA_DIR)
    roots = []
    try:
        roots += list(site.getsitepackages())
        roots.append(site.getusersitepackages())
    except Exception:  # frozen apps may ship a reduced site module
        pass
    for root in roots:
        for sub in ("cublas", "cudnn", "cuda_nvrtc", "cuda_runtime"):
            _add_dll_dir(os.path.join(root, "nvidia", sub, "bin"))


def gpu_hardware() -> bool:
    """An NVIDIA GPU + driver is present (CUDA libraries may still be missing)."""
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def _libs_loadable() -> bool:
    if os.name != "nt":
        return True
    import ctypes

    try:
        for dll in ("cublas64_12.dll", "cudnn64_9.dll"):
            ctypes.WinDLL(dll)
        return True
    except OSError:
        return False


def gpu_available() -> bool:
    """GPU can actually be used for transcription."""
    _prepare_cuda_dlls()
    return gpu_hardware() and _libs_loadable()


def install_gpu_libs(progress=lambda f, m: None) -> None:
    """Downloads the NVIDIA wheels from PyPI and extracts only their DLLs into CUDA_DIR."""
    import json
    import shutil
    import tempfile
    import urllib.request
    import zipfile

    os.makedirs(CUDA_DIR, exist_ok=True)
    tmpdir = tempfile.mkdtemp(prefix="sophicut_gpu_")
    try:
        urls = []
        for pkg, ver in GPU_WHEELS:
            with urllib.request.urlopen(f"https://pypi.org/pypi/{pkg}/{ver}/json", timeout=30) as r:
                info = json.load(r)
            f = next(u for u in info["urls"] if u["filename"].endswith("win_amd64.whl"))
            urls.append((f["url"], f["size"], f["filename"]))
        total = sum(sz for _, sz, _ in urls) or 1
        done = 0
        for url, size, fname in urls:
            path = os.path.join(tmpdir, fname)
            with urllib.request.urlopen(url, timeout=60) as r, open(path, "wb") as out:
                while chunk := r.read(1 << 20):
                    out.write(chunk)
                    done += len(chunk)
                    progress(min(done / total, 0.99) * 0.95,
                             f"GPU 가속 파일 내려받는 중… {done >> 20} / {total >> 20} MB")
            with zipfile.ZipFile(path) as z:
                for m in z.namelist():
                    if m.lower().endswith(".dll") and "/bin/" in m:
                        with z.open(m) as src, open(os.path.join(CUDA_DIR, os.path.basename(m)), "wb") as dst:
                            shutil.copyfileobj(src, dst)
            os.remove(path)
        progress(0.99, "확인 중…")
        _prepare_cuda_dlls(force=True)
        if not _libs_loadable():
            raise RuntimeError("GPU 파일을 불러오지 못했습니다. 그래픽카드 드라이버를 최신으로 업데이트해 주세요.")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _load(name: str, device: str):
    from faster_whisper import WhisperModel

    key = (name, device)
    with _MODEL_LOCK:
        if key not in _MODELS:
            compute = "float16" if device == "cuda" else "int8"
            _MODELS[key] = WhisperModel(name, device=device, compute_type=compute)
        return _MODELS[key]


def _decode(path: str):
    """Decode audio to 16 kHz mono float32 with ffmpeg (avoids PyAV version issues)."""
    import subprocess

    import numpy as np

    r = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", path, "-map", "0:a:0", "-ac", "1", "-ar", "16000",
         "-f", "f32le", "-"],
        capture_output=True, creationflags=0x08000000 if os.name == "nt" else 0)
    if r.returncode != 0:
        raise RuntimeError("오디오 추출 실패: " + r.stderr.decode("utf-8", "replace")[-300:])
    return np.frombuffer(r.stdout, dtype=np.float32)


def _run(model, path: str, language: str, progress):
    segments, info = model.transcribe(
        _decode(path),
        language=language,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 400},
        beam_size=5,
        condition_on_previous_text=False,
    )
    duration = info.duration or 0.0
    out = []
    for seg in segments:
        words = [
            {"text": w.word, "start": float(w.start), "end": float(w.end), "prob": float(w.probability)}
            for w in (seg.words or [])
            if w.word and w.word.strip()
        ]
        if words:
            out.append({"text": seg.text.strip(), "words": words})
        if duration:
            progress(min(seg.end / duration, 0.999), f"받아쓰는 중… {len(out)}문장")
    return out


def transcribe(path: str, model_name: str = "large-v3-turbo", language: str = "ko", progress=lambda p, m: None):
    """Returns (segments, device). Each segment: {text, words:[{text,start,end,prob}]}"""
    devices = ["cuda", "cpu"] if gpu_available() else ["cpu"]
    last_err = None
    for device in devices:
        try:
            progress(0.0, f"AI 모델 불러오는 중 ({model_name}, {device.upper()}) — 처음에는 다운로드로 시간이 걸립니다")
            model = _load(model_name, device)
            progress(0.0, "받아쓰기 시작…")
            return _run(model, path, language, progress), device
        except Exception as e:  # GPU libs missing / OOM -> retry on CPU
            last_err = e
            with _MODEL_LOCK:
                _MODELS.pop((model_name, device), None)
    raise RuntimeError(f"받아쓰기 실패: {last_err}")
