"""Regenerates packaging/app.ico (a placeholder: swap in your own artwork any time)."""
import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))


def draw(size):
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    m = max(1, size // 16)
    d.ellipse((m, m, size - m, size - m), fill=(46, 125, 50, 255))
    r = size * 0.18
    c = size / 2
    d.ellipse((c - r, c - r, c + r, c + r), fill=(255, 255, 255, 255))
    return im


if __name__ == "__main__":
    sizes = [16, 24, 32, 48, 64, 128, 256]
    draw(256).save(os.path.join(HERE, "app.ico"), sizes=[(s, s) for s in sizes])
    print("wrote app.ico")
