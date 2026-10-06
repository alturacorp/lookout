"""Finds the Roblox window, so scanning can be limited to it instead of the whole monitor.

Windows only. The game window is the visible, non-minimised top-level window titled "Roblox" (class WINDOWSCLIENT). The rectangle
returned is its CLIENT area (the game picture, without the title bar and borders) in virtual-screen pixels, the same space mss uses.

This reads where the window is on the screen and then captures that part of the screen. If another window is on top of Roblox, the
part that covers it is what gets read, so keep Roblox in front while scanning. Nothing else about other windows is read or stored."""
import ctypes
import logging
import sys

log = logging.getLogger("tracker")

MIN_W, MIN_H = 320, 240          # smaller than this is not a game window (a launcher, a dialog)


def supported():
    return sys.platform == "win32"


def _windows():
    """[(hwnd, title, class)] for visible top-level windows. Isolated so tests can replace it."""
    from ctypes import wintypes
    u = ctypes.windll.user32
    out = []
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _):
        if u.IsWindowVisible(hwnd) and not u.IsIconic(hwnd):
            n = u.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(hwnd, buf, n + 1)
            cls = ctypes.create_unicode_buffer(256)
            u.GetClassNameW(hwnd, cls, 256)
            out.append((hwnd, buf.value, cls.value))
        return True
    u.EnumWindows(proc(cb), 0)
    return out


def _client_rect(hwnd):
    """(left, top, right, bottom) of the window's client area in screen pixels, or None."""
    from ctypes import wintypes
    u = ctypes.windll.user32
    r = wintypes.RECT()
    if not u.GetClientRect(hwnd, ctypes.byref(r)):
        return None
    pt = wintypes.POINT(0, 0)
    if not u.ClientToScreen(hwnd, ctypes.byref(pt)):
        return None
    return (pt.x, pt.y, pt.x + (r.right - r.left), pt.y + (r.bottom - r.top))


def is_roblox(title, cls):
    return cls == "WINDOWSCLIENT" or title.strip() == "Roblox"


def find_roblox(windows=_windows, client_rect=_client_rect):
    """The game's client rectangle (left, top, right, bottom) in screen pixels, or None if it isn't open / is minimised."""
    best = None
    try:
        for hwnd, title, cls in windows():
            if not is_roblox(title, cls):
                continue
            r = client_rect(hwnd)
            if r and r[2] - r[0] >= MIN_W and r[3] - r[1] >= MIN_H:
                area = (r[2] - r[0]) * (r[3] - r[1])
                if best is None or area > best[0]:
                    best = (area, r)
    except Exception:
        log.exception("could not look for the Roblox window")
        return None
    return best[1] if best else None


def clip_to_monitor(rect, mon):
    """The part of `rect` (screen pixels) on monitor `mon` ({left, top, width, height}), as monitor-relative
    (x1, y1, x2, y2) pixels, or None if they don't overlap enough to be worth scanning."""
    if not rect:
        return None
    x1, y1 = max(rect[0], mon["left"]) - mon["left"], max(rect[1], mon["top"]) - mon["top"]
    x2, y2 = min(rect[2], mon["left"] + mon["width"]) - mon["left"], min(rect[3], mon["top"] + mon["height"]) - mon["top"]
    if x2 - x1 < MIN_W or y2 - y1 < MIN_H:
        return None
    return (int(x1), int(y1), int(x2), int(y2))


def shift(res, dx, dy):
    """OCR results (box, text, confidence) read from a cropped picture -> the same results in the full monitor's pixel space."""
    return [[[[p[0] + dx, p[1] + dy] for p in b], t, c] for b, t, c in (res or [])]
