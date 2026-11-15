"""Local speech-to-text with faster-whisper (GPU when available, CPU fallback)."""
from __future__ import annotations

import os
import site
import threading

_DLL_READY = False
_MODELS: dict[tuple[str, str], object] = {}
_MODEL_LOCK = threading.Lock()

MODELS = ["large-v3-turbo", "large-v3", "medium", "small", "base"]


def _prepare_cuda_dlls() -> None:
    """pip's nvidia-* wheels put cuBLAS/cuDNN DLLs in site-packages/nvidia/*/bin."""
    global _DLL_READY
    if _DLL_READY or os.name != "nt":
        return
    _DLL_READY = True
    roots = list(site.getsitepackages())
    try:
        roots.append(site.getusersitepackages())
    except Exception:
        pass
    for root in roots:
        for sub in ("cublas", "cudnn", "cuda_nvrtc", "cuda_runtime"):
            d = os.path.join(root, "nvidia", sub, "bin")
            if os.path.isdir(d):
                try:
                    os.add_dll_directory(d)
                except OSError:
                    pass
                os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")


def gpu_available() -> bool:
    _prepare_cuda_dlls()
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


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
            progress(min(seg.end / duration, 0.999), f"전사 중… {len(out)}개 문장")
    return out


def transcribe(path: str, model_name: str = "large-v3-turbo", language: str = "ko", progress=lambda p, m: None):
    """Returns (segments, device). Each segment: {text, words:[{text,start,end,prob}]}"""
    devices = ["cuda", "cpu"] if gpu_available() else ["cpu"]
    last_err = None
    for device in devices:
        try:
            progress(0.0, f"AI 모델 불러오는 중 ({model_name}, {device.upper()}) — 처음에는 다운로드로 시간이 걸립니다")
            model = _load(model_name, device)
            progress(0.0, "전사 시작…")
            return _run(model, path, language, progress), device
        except Exception as e:  # GPU libs missing / OOM -> retry on CPU
            last_err = e
            with _MODEL_LOCK:
                _MODELS.pop((model_name, device), None)
    raise RuntimeError(f"전사 실패: {last_err}")
