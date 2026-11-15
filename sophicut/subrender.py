"""Renders styled subtitle lines to transparent PNGs with Pretendard.

All style lengths are in "1080p pixels" and scale with the video height, the
same units the browser preview uses (see .sub-stage in style.css), so the
burned-in result matches what the editor shows.
"""
from __future__ import annotations

import copy
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageFont

FONT_DIR = Path(__file__).resolve().parent / "fonts"
WEIGHTS = {400: "Regular", 500: "Medium", 600: "SemiBold", 700: "Bold", 800: "ExtraBold", 900: "Black"}

DEFAULT_STYLE = {
    "weight": 700, "size": 64, "color": "#ffffff", "italic": False, "spacing": 0, "lineHeight": 1.3,
    "posY": 0.9, "maxChars": 32,
    "outline": {"on": True, "color": "#000000", "width": 5},
    "bg": {"on": False, "color": "#000000", "opacity": 0.65, "pad": 18, "radius": 10},
    "highlight": {"on": False, "color": "#ffe14d"},
    "shadow": {"on": False, "color": "#000000", "opacity": 0.7, "x": 0, "y": 4, "blur": 8},
}


def merge_style(style: dict | None) -> dict:
    out = copy.deepcopy(DEFAULT_STYLE)
    for k, v in (style or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update(v)
        else:
            out[k] = v
    return out


_font_cache: dict[tuple[int, int], ImageFont.FreeTypeFont] = {}


def font(weight: int, px: int) -> ImageFont.FreeTypeFont:
    w = min(WEIGHTS, key=lambda k: abs(k - int(weight)))
    key = (w, px)
    if key not in _font_cache:
        _font_cache[key] = ImageFont.truetype(str(FONT_DIR / f"Pretendard-{WEIGHTS[w]}.otf"), px)
    return _font_cache[key]


def _rgba(hexcolor: str, alpha: float = 1.0):
    r, g, b = ImageColor.getrgb(hexcolor)[:3]
    return (r, g, b, max(0, min(255, round(alpha * 255))))


def _width(text: str, f, spacing: float) -> float:
    if not text:
        return 0.0
    return sum(f.getlength(ch) for ch in text) + spacing * (len(text) - 1) if spacing else f.getlength(text)


def wrap(text: str, f, spacing: float, maxw: float) -> list[str]:
    lines, cur = [], ""
    for word in text.split(" "):
        cand = f"{cur} {word}" if cur else word
        if cur and _width(cand, f, spacing) > maxw:
            lines.append(cur)
            cur = word
        else:
            cur = cand
    if cur:
        lines.append(cur)
    return lines or [""]


def _draw_line(d: ImageDraw.ImageDraw, x, y, text, f, spacing, fill, stroke=0, stroke_fill=None):
    """Baseline-anchored text; char by char when letter spacing is set."""
    if not spacing:
        d.text((x, y), text, font=f, fill=fill, anchor="ls", stroke_width=stroke, stroke_fill=stroke_fill)
        return
    for ch in text:
        d.text((x, y), ch, font=f, fill=fill, anchor="ls", stroke_width=stroke, stroke_fill=stroke_fill)
        x += f.getlength(ch) + spacing


def render(text: str, style: dict, W: int, H: int) -> Image.Image:
    st = merge_style(style)
    s = H / 1080.0
    px = max(8, round(st["size"] * s))
    f = font(st["weight"], px)
    spacing = st["spacing"] * s
    asc, desc = f.getmetrics()
    boxed = st["bg"]["on"] or st["highlight"]["on"]
    pad_x = st["bg"]["pad"] * s if boxed else 0
    pad_y = st["bg"]["pad"] * 0.4 * s if boxed else 0
    lines = wrap(text, f, spacing, W * 0.9 - 2 * pad_x)
    pitch = st["lineHeight"] * px
    box_h = asc + desc + 2 * pad_y
    block_h = pitch * (len(lines) - 1) + box_h
    bottom = st["posY"] * H
    top = bottom - block_h

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    deco = ImageDraw.Draw(img)
    text_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    td = ImageDraw.Draw(text_layer)
    stroke = round(st["outline"]["width"] * s) if st["outline"]["on"] else 0

    placed = []
    for i, line in enumerate(lines):
        tw = _width(line, f, spacing)
        bx0 = (W - tw) / 2 - pad_x
        by0 = top + i * pitch
        bx1, by1 = bx0 + tw + 2 * pad_x, by0 + box_h
        if st["bg"]["on"]:
            deco.rounded_rectangle((bx0, by0, bx1, by1), radius=st["bg"]["radius"] * s,
                                   fill=_rgba(st["bg"]["color"], st["bg"]["opacity"]))
        if st["highlight"]["on"]:
            deco.rectangle((bx0, by0 + box_h * 0.55, bx1, by1), fill=_rgba(st["highlight"]["color"]))
        placed.append((bx0 + pad_x, by0 + pad_y + asc, line))

    if stroke:
        for x, y, line in placed:
            _draw_line(td, x, y, line, f, spacing, _rgba(st["outline"]["color"]), stroke, _rgba(st["outline"]["color"]))
    for x, y, line in placed:
        _draw_line(td, x, y, line, f, spacing, _rgba(st["color"]))

    if st["italic"]:
        k, cy = 0.22, top + block_h / 2
        text_layer = text_layer.transform((W, H), Image.Transform.AFFINE, (1, k, -k * cy, 0, 1, 0),
                                          resample=Image.Resampling.BICUBIC)

    if st["shadow"]["on"]:
        sh = st["shadow"]
        alpha = text_layer.getchannel("A").point(lambda a: round(a * sh["opacity"]))
        shadow = Image.new("RGBA", (W, H), _rgba(sh["color"]))
        shadow.putalpha(alpha)
        if sh["blur"]:
            shadow = shadow.filter(ImageFilter.GaussianBlur(sh["blur"] * s / 2))
        img.alpha_composite(shadow, (round(sh["x"] * s), round(sh["y"] * s)))

    img.alpha_composite(text_layer)
    return img


def render_track(lines, style: dict, W: int, H: int, total: float, workdir: Path) -> Path:
    """Writes PNGs + an ffmpeg concat list that forms a transparent subtitle stream."""
    workdir.mkdir(parents=True, exist_ok=True)
    blank = workdir / "blank.png"
    Image.new("RGBA", (W, H), (0, 0, 0, 0)).save(blank)
    entries: list[tuple[Path, float]] = []
    t = 0.0
    for i, (start, end, text) in enumerate(lines):
        start, end = max(start, t), min(end, total)
        if end - start < 0.04:
            continue
        if start - t > 0.001:
            entries.append((blank, start - t))
        png = workdir / f"s{i:05}.png"
        render(text, style, W, H).save(png, optimize=False, compress_level=1)
        entries.append((png, end - start))
        t = end
    entries.append((blank, max(total - t, 0.04)))
    body = "".join(f"file '{p.as_posix()}'\nduration {d:.3f}\n" for p, d in entries)
    body += f"file '{entries[-1][0].as_posix()}'\n"  # concat demuxer needs the last file repeated
    lst = workdir / "subs.txt"
    lst.write_text(body, encoding="utf-8")
    return lst
