"""F1: freeze the screen, show what the app sees, let the user teach it avatars and draw chat regions."""
import time
import tkinter as tk

import mss
import numpy as np
from PIL import Image, ImageTk
from rapidocr_onnxruntime import RapidOCR

from .config import MIN_CONF, NAME_RE
from .dialogs import ask_name
from .scene import find_lists, inside_any
from .vision import candidates, crop, embed, near, suggest


def toggle(app):
    """Open the teach window, or close it if it is already open."""
    if time.time() - app.f1_t < 0.6:           # ignore double-fire (global + window hotkey)
        return
    app.f1_t = time.time()
    if app.fz is not None and app.fz.winfo_exists():
        app.fz.destroy()
        return
    with mss.mss() as sct:
        frame = np.array(sct.grab(sct.monitors[app.mon.current() + 1]))[:, :, :3]
    app.status.config(text="Freezing… reading names")
    app.root.update_idletasks()
    if app.teach_ocr is None:
        app.teach_ocr = RapidOCR()
    res, _ = app.teach_ocr(frame)
    tags = {}
    for b, text, conf in res or []:
        t = text.strip().lstrip("@")
        if float(conf) >= MIN_CONF and NAME_RE.match(t):
            tags[t] = (int(b[0][0]), int(b[0][1]), int(b[2][0]), int(b[2][1]))
    known = app.known_names()
    boxes = app.detector(frame)
    pb = boxes if app.discover.get() else []
    app.list_zones.update(find_lists(list(tags.items())), app.scan_n)
    skip = app.ignore_rects(frame) + app.list_zones.rects()
    ftags = {n: b for n, b in tags.items() if not inside_any(b, skip) and (n in known or any(near(b, p) for p in pb))}
    cands = []
    for box, n, _approx in candidates(frame, ftags, boxes, app.prefs["box_scale"] / 100):
        try:
            who, sim = app.gallery.match(embed(crop(frame, box)))
        except Exception:
            who, sim = None, 0.0
        cands.append((box, n, who, sim))
    app.fz = TeachWindow(app, frame, tags, ftags, cands).win


class TeachWindow:
    def __init__(self, app, frame, tags, ftags, cands):
        self.app, self.frame, self.tags, self.cands = app, frame, tags, cands
        self.H, self.W = frame.shape[:2]
        win = self.win = tk.Toplevel(app.root)
        win.attributes("-fullscreen", True)
        win.attributes("-topmost", True)
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        self.sw = sw
        self.k = min(sw / self.W, sh / self.H)
        ph = ImageTk.PhotoImage(Image.fromarray(np.ascontiguousarray(frame[:, :, ::-1])).resize(
            (int(self.W * self.k), int(self.H * self.k))))
        cv = self.cv = tk.Canvas(win, width=sw, height=sh, bg="black", highlightthickness=0)
        cv.pack()
        cv.ph = ph
        cv.create_image(0, 0, anchor="nw", image=ph)

        for b in ftags.values():
            self.draw(b, "", "#00bcd4")
        for r in app.rects(frame):
            self.draw(r, "chat region (right-click to remove)", "#e91e63")
        for r in app.ignore_rects(frame):
            self.draw(r, "ignored area (Ctrl + right-click to remove)", "#9e9e9e")
        for r in app.list_zones.rects():
            self.draw(r, "player list (ignored automatically)", "#9e9e9e")
        for box, n, who, sim in cands:
            self.draw(box, f"{who} {sim:.0%}" if who else (f"{n}?" if n else "unknown"), "#4caf50" if who else "#ff9800")
        cv.create_text(sw // 2, 16, fill="white", font=("Segoe UI", 12, "bold"),
                       text="FROZEN: drag a box round an avatar (or click a box) to teach it  |  right-drag = chat region  |  "
                            "Ctrl + right-drag = area to ignore (leaderboard)  |  right-click a box = remove it  |  Esc / F1 = close")
        cv.bind("<ButtonPress-1>", self.left_down)
        cv.bind("<B1-Motion>", self.left_move)
        cv.bind("<ButtonRelease-1>", self.left_up)
        cv.bind("<ButtonPress-3>", self.right_down)
        cv.bind("<B3-Motion>", self.right_move)
        cv.bind("<ButtonRelease-3>", self.right_up)
        cv.create_window(sw - 95, 24, window=tk.Button(win, text="✕ Close (Esc / F1)", command=win.destroy))
        for w in (win, cv):
            w.bind("<Escape>", lambda e: win.destroy())
        win.protocol("WM_DELETE_WINDOW", win.destroy)
        cv.focus_set()
        win.focus_force()

    def draw(self, box, label, colour):
        x1, y1, x2, y2 = (int(v * self.k) for v in box)
        self.cv.create_rectangle(x1, y1, x2, y2, outline=colour, width=2)
        if label:
            self.cv.create_text(x1 + 3, max(10, y1 - 9), anchor="w", text=label, fill=colour, font=("Segoe UI", 11, "bold"))

    # ---- left mouse: teach an avatar
    def left_down(self, e):
        self.p = (e.x, e.y)
        self.r = self.cv.create_rectangle(e.x, e.y, e.x, e.y, outline="yellow", width=2)

    def left_move(self, e):
        self.cv.coords(self.r, *self.p, e.x, e.y)

    def left_up(self, e):
        k, app = self.k, self.app
        x0, y0 = self.p
        self.cv.delete(self.r)
        if abs(e.x - x0) < 8 and abs(e.y - y0) < 8:      # click: snap to a detected box
            hit = [c for c in self.cands if c[0][0] * k <= e.x <= c[0][2] * k and c[0][1] * k <= e.y <= c[0][3] * k]
            if not hit:
                return
            box, n, who, _ = min(hit, key=lambda c: (c[0][2] - c[0][0]) * (c[0][3] - c[0][1]))
        else:
            box = tuple(int(v / k) for v in (min(x0, e.x), min(y0, e.y), max(x0, e.x), max(y0, e.y)))
            n = who = None
        box = (max(0, box[0]), max(0, box[1]), min(self.W, box[2]), min(self.H, box[3]))
        if box[2] - box[0] < 10 or box[3] - box[1] < 10:
            return
        known = [p for (p,) in app.db.q("SELECT name FROM players")]
        name = ask_name(self.win, who or n or suggest(box, self.tags), known)
        if name:
            app.gallery.add(name, crop(self.frame, box), "manual")
            app.db.event(name, "learned look")
            self.draw(box, f"learned {name}", "#4caf50")
            app.status.config(text=f"Learned {name} ({app.gallery.count(name)} looks)")
            app.refresh()

    # ---- right mouse: add / remove chat regions
    def right_down(self, e):
        self.rp, self.ignore = (e.x, e.y), bool(e.state & 0x4)         # Ctrl held = ignored area instead of chat region
        self.rr = self.cv.create_rectangle(e.x, e.y, e.x, e.y, outline="#9e9e9e" if self.ignore else "#e91e63", width=2, dash=(4, 2))

    def right_move(self, e):
        self.cv.coords(self.rr, *self.rp, e.x, e.y)

    def right_up(self, e):
        W, H, k = self.W, self.H, self.k
        x0, y0 = self.rp
        self.cv.delete(self.rr)
        small = abs(e.x - x0) < 8 and abs(e.y - y0) < 8
        frac = None if small else tuple(min(1, max(0, v)) for v in (
            min(x0, e.x) / (W * k), min(y0, e.y) / (H * k), max(x0, e.x) / (W * k), max(y0, e.y) / (H * k)))
        pt = (e.x / (W * k), e.y / (H * k))
        if self.ignore:
            self.app.set_ignore_zone(frac, pt)
        else:
            self.app.set_chat_region(frac, pt)
        if frac:
            self.draw((frac[0] * W, frac[1] * H, frac[2] * W, frac[3] * H),
                      "ignored area saved" if self.ignore else "chat region saved", "#9e9e9e" if self.ignore else "#e91e63")
