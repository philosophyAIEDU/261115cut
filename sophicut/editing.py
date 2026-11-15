"""Transcript <-> timeline model.

Tokens tile the whole timeline with no holes: every token covers [start, end)
and token[i].end == token[i+1].start. Deleting a token removes exactly that
slice of media, so cut ranges are simply the runs of non-deleted tokens.
"""
from __future__ import annotations

import uuid

GAP_THRESHOLD = 0.4  # seconds of silence that become their own "gap" token


def _id() -> str:
    return uuid.uuid4().hex[:8]


def build_clips(segments: list[dict], duration: float, gap_threshold: float = GAP_THRESHOLD) -> list[dict]:
    clips: list[dict] = []
    flat: list[dict] = []  # tokens in order, ends filled afterwards
    prev_end = 0.0  # actual end of the previous spoken word

    for seg in segments:
        clip = {"id": _id(), "tokens": []}
        for w in seg["words"]:
            start = max(float(w["start"]), flat[-1]["start"] + 0.01 if flat else 0.0)
            if start - prev_end >= gap_threshold:
                gap = {"type": "gap", "text": "", "start": prev_end if flat else 0.0, "deleted": False}
                clip["tokens"].append(gap)
                flat.append(gap)
            elif not flat:
                start = 0.0  # absorb a tiny leading silence into the first word
            raw = w["text"]
            tok = {
                "type": "word",
                "text": raw.strip(),
                "space": raw[:1].isspace() or not clip["tokens"],
                "start": start,
                "deleted": False,
                "prob": round(float(w.get("prob", 1.0)), 3),
            }
            clip["tokens"].append(tok)
            flat.append(tok)
            prev_end = max(prev_end, float(w["end"]), start)
        if clip["tokens"]:
            clips.append(clip)

    if not flat:
        gap = {"type": "gap", "text": "", "start": 0.0, "deleted": False}
        clips = [{"id": _id(), "tokens": [gap]}]
        flat = [gap]
    elif duration - prev_end >= gap_threshold:
        gap = {"type": "gap", "text": "", "start": prev_end, "deleted": False}
        clips[-1]["tokens"].append(gap)
        flat.append(gap)

    for a, b in zip(flat, flat[1:]):
        a["end"] = b["start"]
    flat[-1]["end"] = max(duration, flat[-1]["start"])
    for t in flat:
        t["start"] = round(t["start"], 3)
        t["end"] = round(t["end"], 3)
    return clips


def flat_tokens(clips: list[dict]) -> list[dict]:
    return [t for c in clips for t in c["tokens"]]


def clip_speed(c: dict) -> float:
    try:
        return min(4.0, max(0.5, float(c.get("speed") or 1.0)))
    except (TypeError, ValueError):
        return 1.0


def kept_ranges(clips: list[dict], min_len: float = 0.04) -> list[tuple[float, float, float]]:
    """Runs of kept tokens as (source_start, source_end, speed); runs break where speed changes."""
    ranges: list[list[float]] = []
    for c in clips:
        sp = clip_speed(c)
        for t in c["tokens"]:
            if t.get("deleted"):
                continue
            if ranges and abs(ranges[-1][1] - t["start"]) < 0.002 and ranges[-1][2] == sp:
                ranges[-1][1] = t["end"]
            else:
                ranges.append([t["start"], t["end"], sp])
    return [(s, e, sp) for s, e, sp in ranges if e - s >= min_len]


class TimeMap:
    """Maps source time -> output time for the kept ranges."""

    def __init__(self, ranges):
        self.ranges = ranges
        self.offsets = []
        acc = 0.0
        for s, e, sp in ranges:
            self.offsets.append(acc)
            acc += (e - s) / sp
        self.total = acc

    def __call__(self, t: float) -> float:
        for (s, e, sp), off in zip(self.ranges, self.offsets):
            if t < s:
                return off
            if t <= e:
                return off + (t - s) / sp
        return self.total


def _join(tokens) -> str:
    out = ""
    for t in tokens:
        out += (" " if out and t.get("space") else "") + t["text"]
    return out.strip()


def subtitle_lines(clips: list[dict], max_chars: int = 36):
    tm = TimeMap(kept_ranges(clips))
    lines = []
    for c in clips:
        words = [t for t in c["tokens"] if t["type"] == "word" and not t.get("deleted") and t["text"].strip()]
        chunk: list[dict] = []
        for w in words:
            if chunk and len(_join(chunk + [w])) > max_chars:
                lines.append(chunk)
                chunk = []
            chunk.append(w)
        if chunk:
            lines.append(chunk)
    result = []
    for chunk in lines:
        s, e = tm(chunk[0]["start"]), tm(chunk[-1]["end"])
        if e - s > 0.05:
            result.append((s, e, _join(chunk)))
    return result


def _srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def to_srt(clips: list[dict], max_chars: int = 36) -> str:
    parts = []
    for i, (s, e, text) in enumerate(subtitle_lines(clips, max_chars), 1):
        parts.append(f"{i}\n{_srt_time(s)} --> {_srt_time(e)}\n{text}\n")
    return "\n".join(parts)


def to_txt(clips: list[dict]) -> str:
    rows = []
    for c in clips:
        line = _join([t for t in c["tokens"] if t["type"] == "word" and not t.get("deleted")])
        if line:
            rows.append(line)
    return "\n".join(rows) + "\n"
