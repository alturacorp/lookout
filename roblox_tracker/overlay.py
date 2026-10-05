"""Live overlay (click-through layer on the game) and share view (mirror window with the labels drawn on it)."""
import ctypes
import tkinter as tk

from PIL import Image, ImageTk


FONT = "Segoe UI"


def _chip(c, x, y, lines, fill, tag, cw, ch):
    """A label: first line bold (the name), optional second line smaller (tags/flags). (x, y) = bottom-left anchor.
    Returns its rectangle. Kept on screen."""
    ts = [c.create_text(0, 0, text=t, fill="white" if i == 0 else "#f0f0f0", anchor="nw", tags=(tag,),
                        font=(FONT, 12, "bold") if i == 0 else (FONT, 9)) for i, t in enumerate(lines)]
    boxes = [c.bbox(t) for t in ts]
    w = max(b[2] - b[0] for b in boxes) + 14
    h = sum(b[3] - b[1] for b in boxes) + 8
    x = min(max(4, x), max(4, cw - w - 4))
    y = min(max(4, y - h), max(4, ch - h - 4))
    top = y + 4
    for t, b in zip(ts, boxes):
        c.coords(t, x + 7, top)
        top += b[3] - b[1]
    bg = c.create_rectangle(x, y, x + w, y + h, fill=fill, outline="white", width=1, tags=(tag,))
    c.tag_lower(bg, ts[0])
    return (x, y, x + w, y + h)


def paint(c, scene, hud, corner, cw, ch, k=1.0, ox=0.0, oy=0.0, swap=True):
    """Draw the scene (frame coords -> canvas via k/ox/oy) and the HUD.

    What you see:  solid coloured box = the area read as that person (dashed = an estimate, no avatar detector hit);
                   thin white box = the nametag text that was read;  grey dashed box = a name seen but not a player yet;
                   faint grey area = ignored (the player list / leaderboard).
    New items are drawn first and the previous frame is removed afterwards, so the overlay doesn't flicker.
    Returns canvas-space rectangles holding text (OCR must ignore them)."""
    rects, new = [], "new"

    def X(v): return ox + v * k
    def Y(v): return oy + v * k
    def box(b, **kw): return c.create_rectangle(X(b[0]), Y(b[1]), X(b[2]), Y(b[3]), tags=(new,), **kw)

    for z in scene.get("zones", []):
        box(z["box"], outline="#7d7d7d", dash=(3, 5), width=1)
        r = _chip(c, X(z["box"][0]), Y(z["box"][1]) + 20, [z["label"]], "#4a4a4a", new, cw, ch)
        rects.append(r)
    for cd in scene.get("cands", []):
        t = cd["tag"]
        pad = 3
        box((t[0] - pad, t[1] - pad, t[2] + pad, t[3] + pad), outline="#9fa8da" if cd.get("ok") else "#8e8e8e", dash=(4, 3), width=2)
        rects.append(_chip(c, X(t[0] - pad), Y(t[1] - pad), ["?" + cd["name"]], "#5c5c7a", new, cw, ch))
    for p in scene.get("people", []):
        b, col = p["body"], p["col"]
        box(b, outline=col, width=3, dash=(9, 5) if p.get("approx") else ())
        if p.get("tag") is not None:
            t = p["tag"]
            box((t[0] - 2, t[1] - 2, t[2] + 2, t[3] + 2), outline="white", width=1)
        lines = [p["name"]] + ([p["extra"]] if p.get("extra") else [])
        rects.append(_chip(c, X(b[0]), Y(b[1]), lines, col, new, cw, ch))

    if hud is not None and corner.lower() != "off":
        n = len(scene.get("people", []))
        lines = [f"TRACKER  -  {n} on screen", "solid box: seen   dashed: estimated   grey: unconfirmed"] + hud
        pad, lh = 8, 17
        ts = [c.create_text(0, 0, text=ln, anchor="nw", tags=(new,),
                            fill="#ffffff" if i == 0 else "#9aa0a6" if i == 1 else "#d8d8d8",
                            font=(FONT, 10, "bold") if i == 0 else (FONT, 8 if i == 1 else 9)) for i, ln in enumerate(lines)]
        w = max(c.bbox(t)[2] - c.bbox(t)[0] for t in ts) + 2 * pad
        h = lh * len(lines) + 2 * pad
        x = 12 if "left" in corner.lower() else cw - w - 12
        y = 12 if "top" in corner.lower() else ch - h - 12
        bg = c.create_rectangle(x, y, x + w, y + h, fill="#1b1b1b", outline="#4caf50", width=2, tags=(new,))
        c.tag_lower(bg, ts[0])
        for i, t in enumerate(ts):
            c.coords(t, x + pad, y + pad + i * lh)
        rects.append((x - 3, y - 3, x + w + 3, y + h + 3))
    if swap:
        c.delete("cur")
        c.itemconfigure(new, tags=("cur",))
    return rects


class Overlay:
    """Transparent, click-through, never-focused, always-on-top layer covering the whole monitor."""
    KEY = "#010101"

    def __init__(self, root, mon):
        self.mon = mon
        self.w = tk.Toplevel(root)
        self.w.overrideredirect(True)
        self.w.attributes("-topmost", True)
        self.w.attributes("-transparentcolor", self.KEY)
        self.w.geometry(f"{mon['width']}x{mon['height']}+{mon['left']}+{mon['top']}")
        self.c = tk.Canvas(self.w, bg=self.KEY, highlightthickness=0, bd=0)
        self.c.pack(fill="both", expand=True)
        self.w.update_idletasks()
        self._make_click_through()

    def _make_click_through(self):
        try:
            from ctypes import wintypes
            u = ctypes.windll.user32
            u.GetParent.argtypes, u.GetParent.restype = [wintypes.HWND], wintypes.HWND
            u.GetWindowLongW.argtypes, u.GetWindowLongW.restype = [wintypes.HWND, ctypes.c_int], ctypes.c_long
            u.SetWindowLongW.argtypes, u.SetWindowLongW.restype = [wintypes.HWND, ctypes.c_int, ctypes.c_long], ctypes.c_long
            u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
            h = u.GetParent(self.w.winfo_id()) or self.w.winfo_id()
            # layered + click-through + tool window (no taskbar/alt-tab) + never takes focus from Roblox
            u.SetWindowLongW(h, -20, u.GetWindowLongW(h, -20) | 0x80000 | 0x20 | 0x80 | 0x08000000)
            u.SetWindowPos(h, wintypes.HWND(-1), 0, 0, 0, 0, 0x33)     # topmost, no move/size/activate, apply styles
        except Exception:
            pass

    def draw(self, scene, hud, corner):
        self.n = getattr(self, "n", 0) + 1
        if self.n % 20 == 1:
            self.w.attributes("-topmost", True)                        # Roblox must not be able to bury us
            self.w.lift()
        return paint(self.c, scene, hud, corner, self.mon["width"], self.mon["height"])


class ShareView:
    """A normal window showing a live copy of the game screen with the labels drawn on it. Share THIS window
    (or its monitor). Double-click / F11 = fullscreen, Esc = back."""

    def __init__(self, root, on_close):
        self.w = tk.Toplevel(root)
        self.w.title("Tracker share view - share this window")
        self.w.geometry("960x540+80+80")
        self.w.configure(bg="black")
        self.c = tk.Canvas(self.w, bg="black", highlightthickness=0)
        self.c.pack(fill="both", expand=True)
        self.ph, self.full = None, False
        self.w.protocol("WM_DELETE_WINDOW", on_close)
        for seq in ("<Double-Button-1>", "<F11>"):
            self.w.bind(seq, self.toggle)
        self.w.bind("<Escape>", lambda e: self.toggle() if self.full else None)

    def toggle(self, *_):
        self.full = not self.full
        self.w.attributes("-fullscreen", self.full)

    def rect_on(self, mon):
        """This window's rectangle in the scanned monitor's pixel space (so OCR can ignore it)."""
        x, y = self.w.winfo_rootx() - mon["left"], self.w.winfo_rooty() - mon["top"]
        return (x, y, x + self.w.winfo_width(), y + self.w.winfo_height())

    def draw(self, img, scene, hud, corner, warn):
        cw, ch = self.c.winfo_width(), self.c.winfo_height()
        if cw < 50 or ch < 50:
            return
        W, H = img.size
        k = min(cw / W, ch / H)
        nw, nh = max(1, int(W * k)), max(1, int(H * k))
        ox, oy = (cw - nw) // 2, (ch - nh) // 2
        im = img.resize((nw, nh), Image.BILINEAR)
        if self.ph is None or (self.ph.width(), self.ph.height()) != (nw, nh):
            self.ph = ImageTk.PhotoImage(im)
        else:
            self.ph.paste(im)
        self.c.delete("all")
        self.c.create_image(ox, oy, anchor="nw", image=self.ph)
        paint(self.c, scene, hud, corner, cw, ch, k, ox, oy, swap=False)
        if warn:
            self.c.create_text(cw // 2, 14, text="This window is on the monitor being scanned - drag it to another monitor "
                               "or the picture repeats inside itself", fill="#ff5252", font=("Segoe UI", 10, "bold"))
