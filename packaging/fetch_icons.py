"""Download the app's icons from Google's Material Symbols (https://fonts.google.com/icons) and turn them into PNGs.

    python packaging/fetch_icons.py            # needs internet; writes roblox_tracker/assets/icons/*.png
    python packaging/fetch_icons.py --offline-test

Why a script: the icons are Google's, so they're fetched from Google rather than copied around. Each icon becomes a
transparent PNG in two colours (dark text for the light theme, light text for the dark theme) at two sizes.
Only Pillow is needed: Material icons are a single filled path, which the small renderer below draws (even-odd fill).
"""
import math
import os
import re
import sys
import urllib.request

from PIL import Image, ImageChops, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "roblox_tracker", "assets", "icons")
URL = "https://fonts.gstatic.com/s/i/short-term/release/materialsymbolsoutlined/{name}/default/24px.svg"

# name used in the app -> Material Symbols name
ICONS = {
    "scan": "radar", "teach": "school", "add": "person_add", "spotted": "visibility", "overlay": "layers",
    "regions": "crop_free", "settings": "settings", "cloud": "cloud_sync", "setup": "health_and_safety",
    "cleanup": "cleaning_services", "ai": "psychology", "train": "model_training", "save": "save", "delete": "delete",
    "ignore": "block", "summary": "summarize", "confirm": "check", "dismiss": "close", "search": "search",
    "flag": "flag", "refresh": "refresh", "folder": "folder_open", "update": "system_update",
}
SIZES = (18, 24)
COLOURS = {"light": (31, 31, 31), "dark": (232, 232, 232)}
SS = 8      # supersampling for smooth edges

NUM = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")
CMD = re.compile(r"([MmLlHhVvCcSsQqTtAaZz])([^MmLlHhVvCcSsQqTtAaZz]*)")


def _arc(p0, rx, ry, phi, fa, fs, p1, steps=24):
    """SVG endpoint arc -> points (W3C implementation notes, F.6.5)."""
    (x1, y1), (x2, y2) = p0, p1
    if rx == 0 or ry == 0 or p0 == p1:
        return [p1]
    rx, ry, phi = abs(rx), abs(ry), math.radians(phi)
    c, s = math.cos(phi), math.sin(phi)
    dx, dy = (x1 - x2) / 2, (y1 - y2) / 2
    x1p, y1p = c * dx + s * dy, -s * dx + c * dy
    lam = x1p ** 2 / rx ** 2 + y1p ** 2 / ry ** 2
    if lam > 1:
        rx, ry = rx * math.sqrt(lam), ry * math.sqrt(lam)
    num = rx ** 2 * ry ** 2 - rx ** 2 * y1p ** 2 - ry ** 2 * x1p ** 2
    den = rx ** 2 * y1p ** 2 + ry ** 2 * x1p ** 2
    co = math.sqrt(max(0, num / den)) * (-1 if fa == fs else 1)
    cxp, cyp = co * rx * y1p / ry, -co * ry * x1p / rx
    cx, cy = c * cxp - s * cyp + (x1 + x2) / 2, s * cxp + c * cyp + (y1 + y2) / 2

    def ang(ux, uy, vx, vy):
        a = math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
        return a
    t1 = ang(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dt = ang((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not fs and dt > 0:
        dt -= 2 * math.pi
    elif fs and dt < 0:
        dt += 2 * math.pi
    pts = []
    for i in range(1, steps + 1):
        t = t1 + dt * i / steps
        x, y = rx * math.cos(t), ry * math.sin(t)
        pts.append((c * x - s * y + cx, s * x + c * y + cy))
    return pts


def _bez(pts, steps=16):
    out = []
    for i in range(1, steps + 1):
        t = i / steps
        q = pts
        while len(q) > 1:
            q = [((1 - t) * a[0] + t * b[0], (1 - t) * a[1] + t * b[1]) for a, b in zip(q, q[1:])]
        out.append(q[0])
    return out


def flatten(d):
    """SVG path data -> list of closed polylines [(x, y), ...]."""
    subs, cur, pos, start, last_c, last_q = [], [], (0.0, 0.0), (0.0, 0.0), None, None
    for cmd, args in CMD.findall(d):
        nums = [float(n) for n in NUM.findall(args)]
        rel, c = cmd.islower(), cmd.upper()
        if c == "Z":
            if cur:
                subs.append(cur)
            cur, pos, last_c, last_q = [], start, None, None
            continue
        size = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7}[c]
        for i in range(0, max(len(nums), size), size):
            a = nums[i:i + size]
            if len(a) < size:
                break
            ox, oy = pos if rel else (0.0, 0.0)
            if c == "M":
                if cur:
                    subs.append(cur)
                pos = (a[0] + ox, a[1] + oy)
                start, cur = pos, [pos]
                c = "L"                                  # further pairs after M are implicit lineto
                last_c = last_q = None
            elif c == "L":
                pos = (a[0] + ox, a[1] + oy); cur.append(pos); last_c = last_q = None
            elif c == "H":
                pos = (a[0] + (pos[0] if rel else 0), pos[1]); cur.append(pos); last_c = last_q = None
            elif c == "V":
                pos = (pos[0], a[0] + (pos[1] if rel else 0)); cur.append(pos); last_c = last_q = None
            elif c in "CS":
                if c == "C":
                    p1, p2, p3 = (a[0] + ox, a[1] + oy), (a[2] + ox, a[3] + oy), (a[4] + ox, a[5] + oy)
                else:
                    p1 = (2 * pos[0] - last_c[0], 2 * pos[1] - last_c[1]) if last_c else pos
                    p2, p3 = (a[0] + ox, a[1] + oy), (a[2] + ox, a[3] + oy)
                cur += _bez([pos, p1, p2, p3]); pos, last_c, last_q = p3, p2, None
            elif c in "QT":
                if c == "Q":
                    p1, p2 = (a[0] + ox, a[1] + oy), (a[2] + ox, a[3] + oy)
                else:
                    p1 = (2 * pos[0] - last_q[0], 2 * pos[1] - last_q[1]) if last_q else pos
                    p2 = (a[0] + ox, a[1] + oy)
                cur += _bez([pos, p1, p2]); pos, last_q, last_c = p2, p1, None
            elif c == "A":
                end = (a[5] + ox, a[6] + oy)
                cur += _arc(pos, a[0], a[1], a[2], int(a[3]), int(a[4]), end); pos = end; last_c = last_q = None
    if cur:
        subs.append(cur)
    return subs


def parse_svg(svg):
    vb = re.search(r'viewBox="([^"]+)"', svg)
    x, y, w, h = (float(v) for v in vb.group(1).replace(",", " ").split()) if vb else (0, 0, 24, 24)
    d = " ".join(re.findall(r'<path[^>]*?\sd="([^"]+)"', svg))
    return (x, y, w, h), d


def render(svg, size, rgb):
    """-> RGBA image, `size` px square, icon filled with `rgb` on transparent. Even-odd fill via XOR of sub-paths."""
    (vx, vy, vw, vh), d = parse_svg(svg)
    big = size * SS
    k = big / max(vw, vh)
    mask = Image.new("L", (big, big), 0)
    for sub in flatten(d):
        if len(sub) < 3:
            continue
        layer = Image.new("L", (big, big), 0)
        ImageDraw.Draw(layer).polygon([((px - vx) * k, (py - vy) * k) for px, py in sub], fill=255)
        mask = ImageChops.logical_xor(mask.convert("1"), layer.convert("1")).convert("L")
    mask = mask.resize((size, size), Image.LANCZOS)
    im = Image.new("RGBA", (size, size), rgb + (0,))
    im.putalpha(mask)
    return im


def save_all(svg, key):
    os.makedirs(OUT, exist_ok=True)
    for mode, rgb in COLOURS.items():
        for size in SIZES:
            render(svg, size, rgb).save(os.path.join(OUT, f"{key}_{mode}_{size}.png"))


def main():
    if "--offline-test" in sys.argv:
        svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 -960 960 960"><path d="M382-240 154-468l57-57 171 171 367-367 57 57-424 424Z"/></svg>'
        render(svg, 24, (0, 0, 0)).save(os.path.join(HERE, "icon_test.png"))
        print("wrote packaging/icon_test.png")
        return 0
    bad = []
    for key, name in ICONS.items():
        try:
            req = urllib.request.Request(URL.format(name=name), headers={"User-Agent": "icon-fetch"})
            svg = urllib.request.urlopen(req, timeout=30).read().decode("utf-8")
            save_all(svg, key)
            print("ok  ", key, "<-", name)
        except Exception as e:
            bad.append(key)
            print("FAIL", key, "<-", name, e)
    if bad:
        print(f"\n{len(bad)} icon(s) failed. The app still works without them (buttons just show text).")
        return 1
    print(f"\nWrote {len(ICONS) * len(COLOURS) * len(SIZES)} PNGs to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
