"""Button icons (Google Material Symbols, see packaging/fetch_icons.py). Missing files just mean text-only buttons."""
import os

from PIL import Image, ImageTk

from . import theme

DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "icons")
_cache = {}


def get(name, size=18):
    """PhotoImage for the current theme, or None if that icon hasn't been fetched."""
    mode = "dark" if theme.colours() is theme.PALETTES["dark"] else "light"
    key = (name, size, mode)
    if key not in _cache:
        path = os.path.join(DIR, f"{name}_{mode}_{size}.png")
        try:
            _cache[key] = ImageTk.PhotoImage(Image.open(path)) if os.path.exists(path) else None
        except Exception:
            _cache[key] = None
    return _cache[key]


def available():
    return os.path.isdir(DIR) and any(f.endswith(".png") for f in os.listdir(DIR))
