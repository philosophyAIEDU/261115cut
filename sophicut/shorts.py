"""Long-form -> topic-based short clips, fully on-device (no AI service).

The transcript paragraphs are the units. Between neighbouring paragraphs we score how
likely the topic changes (TextTiling-style word overlap of the paragraphs before vs.
after, the length of the pause, and spoken signposts such as "다음으로"), then a small
dynamic program cuts the talk into clips of the requested length that end on the
strongest topic changes. Times are source times; deleted words stay deleted.
"""
from __future__ import annotations

import copy
import math
import re
import statistics
import uuid
from collections import Counter

from . import editing

MODES = {
    # min, max, target length of a clip in output seconds
    "short": (15.0, 60.0, 40.0),   # YouTube Shorts · Reels · TikTok (<= 1 min)
    "long": (15.0, 180.0, 75.0),   # YouTube Shorts up to 3 min: whole topics stay together
}

JOSA = sorted("""은 는 이 가 을 를 의 에 에서 에게 께 으로 로 와 과 도 만 까지 부터 보다 처럼 한테 이랑 랑
이나 나 이라고 라고 이라는 라는 이란 란 에는 에서는 으로는 로는 과는 와는 이에요 예요 입니다 이죠 죠 이고
요 이에 에도 에서도 으로도 로도 하고 들은 들이 들을 들의 들""".split(), key=len, reverse=True)
STOP = set("""그래서 그리고 그런데 근데 그러면 그럼 그러니까 하지만 이제 이렇게 저렇게 그렇게 이런 저런 그런 이거 저거 그거
여기 저기 거기 우리 저희 제가 내가 여러분 이것 저것 그것 정말 진짜 너무 아주 많이 조금 그냥 사실 약간 일단 이게 그게
있는 있고 있어요 있습니다 없는 없고 없어요 합니다 해요 하는 했어요 했습니다 하면 해서 하고 되는 되고 돼요 됩니다 같은
같아요 같습니다 것을 것이 거예요 거죠 건데 거든요 수가 수도 때문 때문에 다음 이번 오늘 지금 아까 그때 한번 하나 어떤 무슨
뭔가 이렇 저렇 그렇 네네 예예 음음 어어""".split())
MARKERS = ("다음으로", "다음은", "두 번째", "세 번째", "네 번째", "첫 번째", "마지막으로", "이번에는", "그럼 이제",
           "자 이제", "자 그러면", "자 그럼", "그러면 이제", "이제 다음", "결론적으로", "정리하면", "정리를 하면",
           "본론", "또 하나", "다른 하나", "한편")


def _stem(w: str) -> str:
    for j in JOSA:
        if len(w) > len(j) + 1 and w.endswith(j):
            return w[: -len(j)]
    return w


def keywords(text: str) -> list[str]:
    out = []
    for w in re.findall(r"[가-힣A-Za-z0-9]+", text.lower()):
        w = _stem(w)
        if len(w) >= 2 and w not in STOP and not w.isdigit():
            out.append(w)
    return out


def _features(text: str) -> Counter:
    """Stems plus 2-letter stem prefixes, so 생각해요/생각하는 still overlap."""
    c = Counter()
    for w in keywords(text):
        c[w] += 1.0
        if len(w) >= 3 and re.match(r"[가-힣]", w):
            c[w[:2]] += 0.5
    return c


def _cos(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    dot = sum(v * b.get(k, 0.0) for k, v in a.items())
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


def _units(clips: list[dict]) -> list[dict]:
    """One unit per paragraph that still has spoken words."""
    units = []
    for ci, c in enumerate(clips):
        words = [t for t in c["tokens"] if t["type"] == "word" and not t.get("deleted") and t["text"].strip()]
        if not words:
            continue
        text = editing._join(words)
        units.append({"ci": ci, "start": words[0]["start"], "end": words[-1]["end"], "text": text,
                      "feat": _features(text)})
    return units


def _boundaries(units: list[dict], window: int = 3) -> list[float]:
    """b[k] in 0..1 = how good a cut between unit k-1 and unit k is (b[0] unused)."""
    n = len(units)
    sims = [1.0] * n
    for k in range(1, n):
        left, right = Counter(), Counter()
        for u in units[max(0, k - window):k]:
            left.update(u["feat"])
        for u in units[k:k + window]:
            right.update(u["feat"])
        sims[k] = _cos(left, right)
    depth = [0.0] * n
    for k in range(1, n):  # TextTiling depth: how far the similarity dips below its neighbours
        lp = max(sims[max(1, k - window):k + 1])
        rp = max(sims[k:min(n, k + window + 1)])
        depth[k] = (lp - sims[k]) + (rp - sims[k])
    top = max(depth[1:], default=0) or 1.0
    out = [0.0] * n
    for k in range(1, n):
        pause = min(1.0, max(0.0, units[k]["start"] - units[k - 1]["end"]) / 2.0)
        head = re.sub(r"[^가-힣A-Za-z0-9 ]", "", units[k]["text"])[:14]
        marker = 1.0 if any(head.startswith(m) or head.startswith("자 " + m) for m in MARKERS) else 0.0
        out[k] = 0.55 * depth[k] / top + 0.25 * pause + 0.2 * marker
    return out


def _title(units: list[dict], limit: int = 40) -> str:
    """The sentence closest to the clip's overall vocabulary."""
    centroid = Counter()
    for u in units:
        centroid.update(u["feat"])
    best = max(units, key=lambda u: (_cos(u["feat"], centroid), len(u["text"]) >= 8))
    text = best["text"].strip()
    lead = r"^(자\s+)?(그럼\s+|그러면\s+|그래서\s+|이제\s+|그리고\s+)*"
    marker = "|".join(re.escape(m) + "로?" for m in sorted(MARKERS, key=len, reverse=True))
    trimmed = re.sub(lead + f"({marker})?\\s*", "", text, count=1).strip()  # drop "다음으로", "자 이제", ...
    if len(trimmed) >= 6:
        text = trimmed
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return (cut or text[:limit]) + "…"


def _tags(units: list[dict], doc_freq: Counter, n_units: int, k: int = 3) -> list[str]:
    tf = Counter()
    for u in units:
        tf.update(keywords(u["text"]))
    verbish = ("면", "요", "다", "서", "고", "게", "지", "죠", "까", "는데", "지만", "니다", "어서", "해서", "했", "하는", "되는", "있는")
    scored = {w: c * math.log(1 + n_units / (1 + doc_freq[w])) for w, c in tf.items()
              if (c >= 2 or len(units) < 3) and not w.endswith(verbish)}
    return [w for w, _ in sorted(scored.items(), key=lambda x: -x[1])[:k]]


def suggest(clips: list[dict], mode: str = "short") -> list[dict]:
    lo, hi, target = MODES.get(mode, MODES["short"])
    units = _units(clips)
    if not units:
        return []
    tm = editing.TimeMap(editing.kept_ranges(clips))
    ostart = [tm(u["start"]) for u in units]
    oend = [tm(u["end"]) for u in units]
    b = _boundaries(units)
    n = len(units)
    # a cut only pays off where the topic changes more than at a typical paragraph break
    typical = (statistics.mean(b[1:]) + statistics.pstdev(b[1:])) if n > 2 else 0.0
    gain = [2.5 * (x - typical) for x in b]

    def seg_score(i: int, j: int) -> float:
        d = oend[j] - ostart[i]
        s = -0.15 * ((d - target) / target) ** 2  # mild: topic changes decide, length only nudges
        if d < lo:
            s -= 1.5 + 2.0 * (lo - d) / lo
        if d > hi and i != j:
            return -math.inf
        return s

    best = [-math.inf] * (n + 1)
    back = [0] * (n + 1)
    best[0] = 0.0
    for j in range(n):
        for i in range(j, -1, -1):
            if i != j and oend[j] - ostart[i] > hi:
                break
            if best[i] == -math.inf:
                continue
            sc = best[i] + seg_score(i, j) + (gain[i] if i > 0 else 0.0)
            if sc > best[j + 1]:
                best[j + 1], back[j + 1] = sc, i
    segs, j = [], n
    while j > 0:
        i = back[j]
        segs.append((i, j - 1))
        j = i
    segs.reverse()

    doc_freq = Counter()
    for u in units:
        doc_freq.update(set(keywords(u["text"])))
    out = []
    for i, j in segs:
        us = units[i:j + 1]
        dur = oend[j] - ostart[i]
        if dur < lo * 0.5:  # leftover scrap (e.g. a short outro) -> not worth uploading on its own
            continue
        out.append({"id": uuid.uuid4().hex[:8], "start": round(us[0]["start"], 3), "end": round(us[-1]["end"], 3),
                    "title": _title(us), "tags": _tags(us, doc_freq, n), "on": True})
    return out


def restrict(clips: list[dict], start: float, end: float) -> list[dict]:
    """Copy of the transcript where everything outside [start, end) is cut."""
    out = copy.deepcopy(clips)
    for c in out:
        for t in c["tokens"]:
            if t["end"] <= start + 1e-3 or t["start"] >= end - 1e-3:
                t["deleted"] = True
            else:  # a range set by hand may start/end inside a word: cut exactly there
                t["start"], t["end"] = max(t["start"], start), min(t["end"], end)
    return out


def safe_name(text: str, limit: int = 40) -> str:
    text = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", text).strip().rstrip(".… ")
    return (text[:limit].strip() or "숏폼")
