"""Draws the SophiCut mark (same as sophicut/static/logo.svg) into a Windows .ico."""
import sys

from PIL import Image, ImageDraw

S = 1024  # draw big, then downsample for smooth edges
INK = (11, 42, 32, 255)


def mark() -> Image.Image:
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    k = S / 64
    d.rounded_rectangle([0, 0, S - 1, S - 1], radius=16 * k, fill=(46, 211, 162, 255))

    def bar(x, y, w, color=INK):
        d.rounded_rectangle([x * k, y * k, (x + w) * k, (y + 7) * k], radius=3.5 * k, fill=color)

    bar(13, 16, 38)
    bar(13, 28.5, 14)
    bar(35, 28.5, 16, (36, 164, 126, 255))  # ink at 28% over mint
    bar(13, 41, 28)
    d.line([30.5 * k, 25 * k, 32 * k, 39 * k], fill=(255, 255, 255, 255), width=int(3 * k))
    return im


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "sophicut.ico"
    import os
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    if out.lower().endswith(".png"):  # macOS: PyInstaller turns a 1024px PNG into .icns
        mark().save(out)
    else:
        mark().resize((256, 256), Image.LANCZOS).save(out, sizes=[(s, s) for s in sizes])
    print("wrote", out)
