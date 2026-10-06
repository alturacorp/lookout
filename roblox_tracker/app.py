"""Main window, scan loop, player tracking, chat intake and background workers (one class, no inheritance chain)."""
import difflib
import json
import logging
import math
import os
import queue
import re
import threading
import time
import types
import tkinter as tk
import uuid
from datetime import datetime, timedelta
from statistics import median
from tkinter import messagebox, ttk

import mss
import numpy as np
from PIL import Image, ImageTk
from rapidocr_onnxruntime import RapidOCR

from . import build_info, config, dialogs, icons, paths, teach, theme, tray, updater
from . import cloud as cloud_mod
from . import prefs as prefs_mod
from .ai import Clf, verdict
from .config import (BUILTIN, HIGH_SEVERITY, CHAT_RE, CORNERS, CROP_EVERY, DEFAULT_AI_OFF, GRACE, IDLE_SECS, IMG, MIN_CONF,
                     MOVE_PX, NAME_RE, OV_SHARE, OV_TOP, SEEDS, now)
from .overlay import Overlay, ShareView
from .scene import Candidates, Ease, ListZones, find_lists, frame_sig, inside_any, plausible, same_frame
from .store import Store
from .sync import Sync
from .textutil import download, file_owner, http, remove_file, skel
from .vision import Central, Detector, Gallery, candidates, colours, crop, embed, evidence_jpeg, near, person_box, quantize

log = logging.getLogger("tracker")


class App:
    def __init__(self, root, instance=None):
        self.root, self.instance = root, instance
        self.db = Store()
        self.prefs = prefs_mod.load(self.db)
        config.OLLAMA, config.MODEL = self.prefs["ollama"], self.prefs["model"]
        self.gallery = Gallery(self.db)
        self.alias_run, self.alias_boxes = {}, {}
        self.alias_run, self.alias_boxes = {}, {}                 # in-game name matches: consecutive scans, and where they were
        self.central, self._central_at, self._outfit_seen, self._frame_ref = Central(), 0, {}, None
        self.detector = Detector()
        self.clf = Clf()
        self.lexicon = self.load_lexicon()

        # ---- settings bound to the toolbar
        p = self.prefs
        self.auto = tk.BooleanVar(root, value=p["auto"])
        self.interval = tk.IntVar(root, value=p["interval"])
        self.ai_on = tk.BooleanVar(root, value=p["ai_on"])
        self.sens = tk.IntVar(root, value=p["sens"])
        self.discover = tk.BooleanVar(root, value=p["discover"])
        self.auto_add = tk.BooleanVar(root, value=p["auto_add"])
        self.overlay_on = tk.BooleanVar(root, value=False)       # always starts off
        self.show_sus = tk.BooleanVar(root, value=p["show_sus"])
        self.ov_flags = tk.BooleanVar(root, value=p["ov_flags"])
        self.ov_mode = tk.StringVar(root, value=p["ov_mode"])
        self.hud_corner = tk.StringVar(root, value=p["hud"])
        self.search = tk.StringVar(root)
        # Worker threads must not touch Tk variables. They read this plain copy, which the UI thread keeps current.
        self.opt = types.SimpleNamespace(auto=p["auto"], interval=p["interval"], discover=p["discover"], ai_on=p["ai_on"],
                                          sens=p["sens"], mon=0)
        for k in ("auto", "interval", "discover", "ai_on", "sens"):
            getattr(self, k).trace_add("write", lambda *_, k=k: self._mirror(k))
        # pref key -> Tk variable, saved automatically whenever one changes
        self.pref_vars = {"auto": self.auto, "interval": self.interval, "ai_on": self.ai_on, "sens": self.sens,
                          "discover": self.discover, "auto_add": self.auto_add, "show_sus": self.show_sus, "ov_flags": self.ov_flags,
                          "ov_mode": self.ov_mode, "hud": self.hud_corner}

        # ---- persisted settings
        self.ai_off = set(self.db.get("ai_off", sorted(DEFAULT_AI_OFF)))
        legacy = self.db.get("chat")                       # older versions stored a single chat region here
        self.regions = [tuple(r) for r in self.db.get("regions", [tuple(legacy)] if legacy else [])]
        self.ignore_zones = [tuple(r) for r in self.db.get("ignore_zones", [])]     # areas to never read names from

        # ---- runtime state (everything the scan thread touches must exist before the threads start)
        self.live, self.sel, self.track = set(), None, {}
        self._tabs_sig, self._restoring = None, False
        self.names, self.pend, self.scan_n = set(), {}, 0
        self._sk, self.tag_seen, self.rr_left = None, set(), 0
        self.last_auto, self.fz, self.f1_t = {}, None, 0
        self.ocr, self.teach_ocr = None, None              # scan-thread OCR engine / F1 window's own engine
        self.ov, self.ov_idx, self.ov_rects, self.ov_scene, self.ov_hud = None, None, [], None, None
        self.list_zones, self.cands = ListZones(), Candidates()      # leaderboards found on screen; names not yet players
        self.cand_state, self.cand_retry = {}, {}                    # name -> pending|ok|no|verified|error
        self.bodies, self.tag_boxes, self.frame_shape = {}, {}, (1080, 1920)
        self.ease, self._ov_dirty = Ease(0.45), True
        self.ov_mon, self.sv, self.sct, self._tick = None, None, None, 0
        self.trigger, self.photo = threading.Event(), []
        self.lookups, self.ai_jobs = queue.Queue(), queue.Queue()
        self.flag_ids = []
        self.tray, self._save_job, self._last_log = None, None, {}
        self.toasts = []
        self.cloud = cloud_mod.Cloud()
        self.sync_stop = threading.Event()
        self.sync = Sync(self.db, self.cloud, self.device_id(), enabled=lambda: self.prefs["cloud_on"],
                         on_alert=lambda row, where: self.root.after(0, self.show_alert, row, where))

        dev = build_info.VERSION.endswith("-dev")
        root.title("Lookout" + ("" if dev else f" {build_info.VERSION}"))
        self.restore_geometry()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.report_callback_exception = self.on_tk_error
        root.bind("<<ShowWindow>>", lambda e: self.show_window())
        root.bind("<<QuitApp>>", lambda e: self.quit())
        theme.apply(root, self.prefs["theme"])
        self.build_ui()
        self.restore_monitor()
        self.bind_hotkeys()
        self.refresh()
        self.clf.fit(self.db.q("SELECT text,label FROM examples") + SEEDS)    # pick up any new seed examples
        for v in self.pref_vars.values():
            v.trace_add("write", lambda *_: self.schedule_save())
        self.mon.bind("<<ComboboxSelected>>", lambda e: self.schedule_save(), add="+")
        self.apply_tray_pref()
        if self.instance:
            self.instance.listen(lambda: root.event_generate("<<ShowWindow>>", when="tail"))
        for target in (self.scan_loop, self.lookup_worker, self.ai_worker, lambda: self.sync.run(self.sync_stop)):
            threading.Thread(target=target, daemon=True).start()
        root.after(20000, self.auto_purge)                 # tidy old chat/timeline rows (Settings: "Keep history")
        if not self.prefs["setup_done"]:
            root.after(1000, self.first_run)
        if self.prefs["auto_update"] and updater.feed_url() and time.time() - self.prefs["last_update_check"] > 20 * 3600:
            root.after(8000, lambda: self.check_updates(manual=False))

    @staticmethod
    def load_lexicon():
        lex = {}
        path = paths.data_path("lexicon.json")
        if os.path.exists(path):
            with open(path) as f:
                lex = json.load(f)
        for k, v in BUILTIN.items():
            lex.setdefault(k, []).extend(v)
        return lex

    # ================================================================ UI
    # ---- small widget helpers (icons are optional: no PNG, no icon)
    def button(self, parent, text, command, icon=None, accent=False):
        img = icons.get(icon) if icon else None
        kw = {"text": text, "command": command}
        if img:
            kw.update(image=img, compound="left")
        if accent and theme.style_name("accent"):
            kw["style"] = theme.style_name("accent")
        b = ttk.Button(parent, **kw)
        b.image = img                                        # keep a reference or Tk drops the picture
        return b

    def switch(self, parent, text, var, command=None):
        kw = {"text": text, "variable": var}
        if command:
            kw["command"] = command
        if theme.style_name("switch"):
            kw["style"] = theme.style_name("switch")
        return ttk.Checkbutton(parent, **kw)

    def build_ui(self):
        root, B = self.root, self.button
        with mss.mss() as sct:
            n = len(sct.monitors) - 1

        def group(parent, title):
            g = ttk.LabelFrame(parent, text=title, padding=(8, 4, 8, 6))
            g.pack(side="left", padx=(0, 8), fill="y")
            return g

        # ---- row 1: scanning + finding players
        bar = ttk.Frame(root, padding=(10, 8, 10, 0))
        bar.pack(fill="x")
        g = group(bar, "Scanning")
        self.mon = ttk.Combobox(g, state="readonly", width=11, values=[f"Monitor {i + 1}" for i in range(n)])
        self.mon.current(0)
        self.mon.bind("<<ComboboxSelected>>", self._mirror_monitor)
        self.mon.pack(side="left")
        self.switch(g, "Auto-scan every", self.auto).pack(side="left", padx=(10, 4))
        ttk.Spinbox(g, from_=1, to=30, width=3, textvariable=self.interval).pack(side="left")
        ttk.Label(g, text="s").pack(side="left", padx=(2, 8))
        B(g, "Scan now", self.trigger.set, "scan", accent=True).pack(side="left", padx=2)
        B(g, "Teach (F1)", self.freeze, "teach").pack(side="left", padx=2)
        g = group(bar, "Finding players")
        self.switch(g, "Discover new players", self.discover).pack(side="left", padx=(0, 8))
        self.switch(g, "Add verified names", self.auto_add).pack(side="left", padx=(0, 8))
        B(g, "Add player…", lambda: dialogs.add_player_dialog(self), "add").pack(side="left", padx=2)
        self.spot_btn = B(g, "Spotted (0)", lambda: dialogs.spotted_dialog(self), "spotted")
        self.spot_btn.pack(side="left", padx=2)
        B(g, "Chat regions…", lambda: dialogs.regions_dialog(self), "regions").pack(side="left", padx=2)

        # ---- row 2: overlay + AI + app buttons
        bar2 = ttk.Frame(root, padding=(10, 6, 10, 0))
        bar2.pack(fill="x")
        g = group(bar2, "Live overlay")
        self.switch(g, "Show", self.overlay_on, self.update_overlay).pack(side="left", padx=(0, 6))
        cb = ttk.Combobox(g, state="readonly", width=18, values=[OV_TOP, OV_SHARE], textvariable=self.ov_mode)
        cb.pack(side="left", padx=4)
        cb.bind("<<ComboboxSelected>>", lambda e: self.update_overlay())
        ttk.Label(g, text="HUD").pack(side="left", padx=(6, 0))
        cb = ttk.Combobox(g, state="readonly", width=11, values=CORNERS, textvariable=self.hud_corner)
        cb.pack(side="left", padx=4)
        cb.bind("<<ComboboxSelected>>", lambda e: self.update_overlay())
        self.switch(g, "Flags", self.ov_flags, self.update_overlay).pack(side="left", padx=(6, 4))
        self.switch(g, "? flags", self.show_sus).pack(side="left")
        g = group(bar2, "AI chat flagging")
        self.switch(g, "On", self.ai_on).pack(side="left", padx=(0, 6))
        ttk.Label(g, text="min %").pack(side="left")
        ttk.Spinbox(g, from_=30, to=95, width=3, textvariable=self.sens).pack(side="left", padx=(2, 6))
        B(g, "Categories…", lambda: dialogs.ai_dialog(self), "ai").pack(side="left", padx=2)
        B(g, "Train…", lambda: dialogs.train_dialog(self), "train").pack(side="left", padx=2)
        menu = ttk.Frame(bar2)
        menu.pack(side="right", anchor="s")
        for txt, cmd, ic in (("Shared list…", dialogs.cloud_dialog, "cloud"), ("Setup check…", dialogs.setup_dialog, "setup"),
                             ("Data cleanup…", dialogs.cleanup_dialog, "cleanup"), ("Settings…", dialogs.settings_dialog, "settings")):
            B(menu, txt, lambda c=cmd: c(self), ic).pack(side="left", padx=2)

        # ---- row 3: status + search
        bar3 = ttk.Frame(root, padding=(12, 8, 10, 0))
        bar3.pack(fill="x")
        self.status = ttk.Label(bar3, text="Loading OCR…")
        self.status.pack(side="left")
        self.cloud_lbl = ttk.Label(bar3, text="", foreground="#666", cursor="hand2")
        self.cloud_lbl.pack(side="left", padx=(16, 0))
        self.cloud_lbl.bind("<Button-1>", lambda e: dialogs.watchlist_dialog(self) if self.cloud.signed_in else dialogs.cloud_dialog(self))
        self.root.after(2000, self.update_cloud_label)
        self.search.trace_add("write", lambda *_: self.refresh())
        ttk.Entry(bar3, textvariable=self.search, width=24).pack(side="right")
        ttk.Label(bar3, text="Search players").pack(side="right", padx=6)

        pan = ttk.PanedWindow(root, orient="horizontal")
        pan.pack(fill="both", expand=True, padx=10, pady=10)
        left = ttk.Frame(pan)
        pan.add(left, weight=3)
        cols = ("name", "tags", "last", "now")
        self.tree = ttk.Treeview(left, columns=cols, show="headings", selectmode="extended")   # shift/ctrl-click + Delete
        for c, w in zip(cols, (140, 220, 130, 80)):
            self.tree.heading(c, text={"last": "Last seen", "now": "Now"}.get(c, c.title()))
            self.tree.column(c, width=w)
        sb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        theme.row_height(self.tree)
        theme.tree_tags(self.tree)
        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        self.tree.bind("<Delete>", lambda e: self.delete())

        right = ttk.Frame(pan, padding=(14, 0, 0, 0))
        pan.add(right, weight=3)
        head = ttk.Frame(right)
        head.pack(fill="x")
        self.thumb = ttk.Label(head)
        self.thumb.pack(side="left")
        self.title = ttk.Label(head, text="Select a player", font=("Segoe UI", 14, "bold"), justify="left")
        self.title.pack(side="left", padx=12)
        ttk.Label(right, text="Manual tags (comma separated)").pack(anchor="w", pady=(8, 2))
        self.tags = ttk.Entry(right)
        self.tags.pack(fill="x")
        ttk.Label(right, text="Notes").pack(anchor="w", pady=(8, 2))
        self.notes = tk.Text(right, height=5, wrap="word")
        theme.style_text(self.notes)
        self.notes.pack(fill="x")
        row = ttk.Frame(right)
        row.pack(fill="x", pady=8)
        for txt, cmd, ic, acc in (("Save", self.save, "save", True), ("Delete", self.delete, "delete", False),
                                  ("Not a player", self.ignore, "ignore", False), ("AI summary", self.summarise, "summary", False)):
            B(row, txt, cmd, ic, accent=acc).pack(side="left", padx=(0, 6))
        nb = ttk.Notebook(right)
        nb.pack(fill="both", expand=True)
        self.chat_t = tk.Text(nb, wrap="word", state="disabled")
        self.time_t = tk.Text(nb, wrap="word", state="disabled")
        fl = ttk.Frame(nb)
        self.flag_l = tk.Listbox(fl)
        for w in (self.chat_t, self.time_t, self.flag_l):
            theme.style_text(w)
        self.flag_l.pack(fill="both", expand=True)
        fb = ttk.Frame(fl)
        fb.pack(fill="x", pady=(6, 0))
        B(fb, "Confirm", lambda: self.review("confirmed"), "confirm", accent=True).pack(side="left")
        B(fb, "Dismiss", lambda: self.review("dismissed"), "dismiss").pack(side="left", padx=6)
        for w, name in ((self.chat_t, "Chat"), (self.time_t, "Timeline"), (fl, "Flags")):
            nb.add(w, text=name)

    def bind_hotkeys(self):
        self.root.bind("<F1>", lambda e: self.freeze())
        self.root.bind("<<Freeze>>", lambda e: self.freeze())
        try:
            import keyboard   # optional: makes F1 work while Roblox is focused
            keyboard.add_hotkey("f1", lambda: self.root.event_generate("<<Freeze>>", when="tail"))
        except Exception:
            self.status.config(text="F1 only works while this window is focused (pip install keyboard for global)")

    def freeze(self):
        teach.toggle(self)

    # ---- saved settings, window lifecycle, tray, error reporting
    def restore_geometry(self):
        """Restore size always, position only if it lands on the primary screen (a monitor may have been unplugged)."""
        geom = self.prefs["geometry"]
        m = re.match(r"^(\d+)x(\d+)(?:\+(-?\d+)\+(-?\d+))?$", geom)
        if not m:
            self.root.geometry("1150x680")
            return
        w, h, x, y = m.group(1), m.group(2), m.group(3), m.group(4)
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        if x is not None and 0 <= int(x) < sw - 100 and 0 <= int(y) < sh - 100:
            self.root.geometry(f"{w}x{h}+{x}+{y}")
        else:
            self.root.geometry(f"{w}x{h}")

    def auto_purge(self):
        try:
            n = self.db.purge(self.prefs.get("keep_days", 0))
            if n:
                log.info("auto-purge removed %d old chat/timeline rows", n)
        except Exception:
            log.exception("auto-purge failed")
        self.root.after(24 * 3600 * 1000, self.auto_purge)

    def _mirror(self, k):
        try:
            setattr(self.opt, k, getattr(self, k).get())
        except tk.TclError:                                  # e.g. a spinbox that is momentarily empty
            pass

    def _mirror_monitor(self, *_):
        try:
            self.opt.mon = self.mon.current()
        except tk.TclError:
            pass

    def restore_monitor(self):
        n = len(self.mon.cget("values"))
        self.mon.current(self.prefs["monitor"] if self.prefs["monitor"] < n else 0)
        self._mirror_monitor()

    def schedule_save(self):
        if self._save_job is None:
            self._save_job = self.root.after(600, self._do_save)

    def _do_save(self):
        self._save_job = None
        self.save_prefs()

    def save_prefs(self):
        p = self.prefs
        for k, v in self.pref_vars.items():
            try:
                p[k] = v.get()
            except tk.TclError:                              # e.g. a spinbox that is momentarily empty
                pass
        try:
            p["monitor"] = self.mon.current()
            if self.root.state() == "normal":
                p["geometry"] = self.root.geometry()
        except tk.TclError:
            pass
        prefs_mod.save(self.db, p)

    def first_run(self):
        self.prefs["setup_done"] = True
        self.save_prefs()
        dialogs.welcome_dialog(self)

    def apply_tray_pref(self):
        want = self.prefs["tray"] and tray.available()
        if want and self.tray is None:
            self.tray = tray.Tray(self)
            if not self.tray.start():
                self.tray = None
        elif not want and self.tray is not None:
            self.tray.stop()
            self.tray = None

    def show_window(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def on_close(self):
        if self.tray is not None and self.tray.running:
            self.root.withdraw()                             # keep scanning in the background
        else:
            self.quit()

    def quit(self):
        try:
            self.save_prefs()
        except Exception:
            log.exception("saving settings on exit failed")
        self.sync_stop.set()
        self.sync.wake.set()
        self.close_overlays()
        if self.tray is not None:
            self.tray.stop()
        if self.instance:
            self.instance.release()
        self.root.destroy()

    # ---- updates
    def check_updates(self, manual=False):
        """Look for a newer release in the background. Automatic checks stay silent unless there is an update."""
        if not updater.feed_url():
            if manual:
                messagebox.showinfo("Updates", "This build has no update feed configured.")
            return

        def work():
            try:
                info = updater.check()
            except Exception as e:
                self.log_error("update", "update check failed")
                if manual:
                    self.root.after(0, lambda e=e: messagebox.showerror("Updates", f"Couldn't check for updates:\n\n{e}"))
                return
            self.root.after(0, lambda: self.on_update_result(info, manual))
        threading.Thread(target=work, daemon=True).start()

    def on_update_result(self, info, manual):
        self.prefs["last_update_check"] = int(time.time())
        self.schedule_save()
        if info is None:
            if manual:
                messagebox.showinfo("Updates", f"You're up to date (version {build_info.VERSION}).")
            return
        if not manual and info["version"] == self.prefs["skip_version"]:
            return
        dialogs.update_dialog(self, info)

    def log_error(self, key, msg):
        """Log the exception being handled, at most once a minute per source so a dead server can't flood the log."""
        t = time.time()
        if t - self._last_log.get(key, 0) > 60:
            self._last_log[key] = t
            log.exception(msg)

    def on_tk_error(self, exc, val, tb):
        log.error("Error in a UI callback", exc_info=(exc, val, tb))
        self.status.config(text=f"Error: {val}")

    # ---- player list / detail pane
    def autotags(self):
        out = {}
        for p, cat, st in self.db.q("SELECT player,cat,status FROM flags WHERE status!='dismissed'"):
            out.setdefault(p, set()).add(cat if st == "confirmed" else "?" + cat)
        for (p,) in self.db.q("SELECT name FROM players WHERE uid IS NOT NULL AND CAST(uid AS TEXT) IN (SELECT user_id FROM watchlist)"):
            out.setdefault(p, set()).add("WATCHLIST")
        return out

    def refresh(self):
        sel, top = self.tree.selection(), self.tree.yview()[0]
        q, auto = self.search.get().lower(), self.autotags()
        rows = self.db.q("SELECT name,tags,last_seen FROM players ORDER BY last_seen DESC")
        self.tree.delete(*self.tree.get_children())
        for name, tags, last in rows:
            alltags = ", ".join(filter(None, [tags, *sorted(auto.get(name, []))]))
            if q and q not in name.lower() and q not in alltags.lower():
                continue
            st = self.track.get(name, {})
            now_s = st.get("state", "live") if name in self.live else ""
            ttags = tuple(t for t, c in (("live", name in self.live), ("flag", name in auto)) if c)
            self.tree.insert("", "end", iid=name, values=(name, alltags, last, now_s), tags=ttags)
        self._restoring = True          # selection_set below fires <<TreeviewSelect>>; on_select must ignore it
        try:
            if self.sel and self.tree.exists(self.sel):
                self.tree.selection_set(self.sel)
                sig = self.tabs_signature(self.sel)
                if sig != self._tabs_sig:                  # only touch the text boxes when there is something new
                    self._tabs_sig = sig
                    self.fill_tabs()
            keep = [s for s in sel if self.tree.exists(s)]
            if len(keep) > 1:
                self.tree.selection_set(keep)              # keep multi-selections alive across the periodic refresh
        finally:
            self._restoring = False
        self.tree.yview_moveto(top)

    def tabs_signature(self, name):
        return (self.db.scalar("SELECT COUNT(*) FROM chat WHERE player=?", (name,)),
                self.db.scalar("SELECT COUNT(*) FROM events WHERE player=?", (name,)),
                self.db.scalar("SELECT COUNT(*), COALESCE(MAX(id),0), COUNT(DISTINCT status) FROM flags WHERE player=?", (name,)))

    def on_select(self, _):
        if getattr(self, "_restoring", False):
            return                      # the periodic refresh re-selecting a row: don't overwrite what's being typed
        s = self.tree.selection()
        if not s:
            return
        self.sel = s[0]
        self._tabs_sig = None
        r = self.db.q("SELECT tags,notes,first_seen,last_seen,seen,verified,colours FROM players WHERE name=?", (self.sel,))[0]
        self.title.config(text=f"{self.sel}{'  ✓' if r[5] else ''}\nfirst {r[2]}\nlast  {r[3]}  ({r[4]} visits)\n{r[6]}")
        self.tags.delete(0, "end")
        self.tags.insert(0, r[0])
        self.notes.delete("1.0", "end")
        self.notes.insert("1.0", r[1])
        p = f"{IMG}/thumb_{self.sel}.png"
        self.photo = [ImageTk.PhotoImage(Image.open(p).resize((90, 90)))] if os.path.exists(p) else []
        self.thumb.config(image=self.photo[0] if self.photo else "")
        self.fill_tabs()

    def fill_tabs(self):
        n = self.sel
        chat = [f"{t}  {x}" for t, x in self.db.q("SELECT t,text FROM chat WHERE player=? ORDER BY id DESC LIMIT 200", (n,))]
        events = [f"{t}  {k}  {d}" for t, k, d in self.db.q("SELECT t,kind,detail FROM events WHERE player=? ORDER BY id DESC LIMIT 200", (n,))]
        for w, rows in ((self.chat_t, chat), (self.time_t, events)):
            w.config(state="normal")
            w.delete("1.0", "end")
            w.insert("1.0", "\n".join(rows))
            w.config(state="disabled")
        self.flag_l.delete(0, "end")
        self.flag_ids = []
        for fid, cat, st, src, text in self.db.q(
                "SELECT f.id,f.cat,f.status,f.src,c.text FROM flags f JOIN chat c ON c.id=f.chat_id WHERE f.player=? ORDER BY f.id DESC", (n,)):
            self.flag_l.insert("end", f"[{st}] {cat} ({src}): {text}")
            self.flag_ids.append(fid)

    def save(self):
        if self.sel:
            tags = ", ".join(t.strip() for t in self.tags.get().split(",") if t.strip())
            self.db.q("UPDATE players SET tags=?, notes=? WHERE name=?", (tags, self.notes.get("1.0", "end").strip(), self.sel))
            self.refresh()

    def selected_names(self):
        return list(self.tree.selection()) or ([self.sel] if self.sel else [])

    def delete(self):
        sel = self.selected_names()
        if not sel:
            return
        if len(sel) > 1 and not messagebox.askyesno("Delete players", f"Delete {len(sel)} players with all their logs, looks and screenshots?"):
            return
        self.drop_players(sel)

    def ignore(self):
        sel = self.selected_names()
        for n in sel:
            self.db.q("INSERT OR IGNORE INTO ignored VALUES(?)", (n.lower(),))
        if sel:
            self.drop_players(sel)

    def review(self, status):
        """Confirm / Dismiss a flag. Doubles as training for the on-device classifier."""
        s = self.flag_l.curselection()
        if not s:
            return
        fid = self.flag_ids[s[0]]
        r = self.db.q("SELECT c.text,f.cat FROM flags f JOIN chat c ON c.id=f.chat_id WHERE f.id=?", (fid,))
        if r:
            self.learn(r[0][0], r[0][1] if status == "confirmed" else "none")
        self.db.q("UPDATE flags SET status=? WHERE id=?", (status, fid))
        if status == "confirmed":
            self.share_flag(fid)
        self.refresh()

    def update_cloud_label(self):
        """Small status in the main window: is the shared list on, who's watched, did the last sync work."""
        try:
            if not self.prefs["cloud_on"] or not self.cloud.ready:
                txt = ""
            elif not self.cloud.signed_in:
                txt = "Shared list: sign in"
            else:
                s = self.sync
                ok = {"ok": f"{s.watch_count()} on shared list", "offline": "shared list offline (retrying)",
                      "denied": "shared list: waiting for approval", "auth": "shared list: sign in again"}
                txt = ok.get(s.state, s.message)
            if txt != self.cloud_lbl.cget("text"):
                self.cloud_lbl.config(text=txt)
        except Exception:
            pass
        self.root.after(5000, self.update_cloud_label)

    def device_id(self):
        """Random id for this installation, used by the backend only to avoid alerting a device about its own sightings."""
        did = self.db.get("device_id")
        if not did:
            did = uuid.uuid4().hex
            self.db.put("device_id", did)
        return did

    def share_flag(self, fid):
        """Queue a flag you confirmed so the reviewers can look at it. They decide; this only reports."""
        if not (self.prefs["cloud_on"] and self.prefs["cloud_submit"] and self.cloud.signed_in):
            return
        r = self.db.q("SELECT f.player,f.cat,f.src,c.text,p.uid,p.verified FROM flags f JOIN chat c ON c.id=f.chat_id "
                      "LEFT JOIN players p ON p.name=f.player WHERE f.id=?", (fid,))
        if r:
            who, cat, src, text, uid, ver = r[0]
            self.sync.queue_flag(who, cat, src, text, uid if ver == 1 else None)
            self.sync.wake.set()
            self.status.config(text=f"Sent {who}'s flag to the reviewers")

    def check_watch(self, n, uid):
        if self.prefs["cloud_on"] and uid and self.track.get(n, {}).get("present"):
            self.sync.spotted(uid, n)

    def show_alert(self, row, where, ctx=None):
        """Small always-on-top notice. where: 'here' (their name tag matched), 'outfit' (outfit resembles a reference, unverified)
        or 'another device'. It says plainly that this is a match against the central database, and offers to send a cropped
        picture to the reviewers (only if you choose to). Closes itself; click to dismiss."""
        cats = ", ".join(c.replace("_", " ") for c in row["categories"]) or "watchlist"
        here, name_only = where == "here", where == "name"
        outfit = where in ("outfit", "name")                      # both are 'possible, unverified' matches (amber)
        if here:
            self.db.event(row["username"], "watchlist", cats)
            ctx = ctx or self.nametag_evidence(row)
        elif name_only:
            self.db.event(row["username"], "possible name match (unverified)", cats)
            ctx = None                                           # not exact enough to offer a picture as a name-tag match
        self.status.config(text=f"Central database match: {row['username']} ({cats})" + (" - possible, unverified" if outfit else ""))
        t = tk.Toplevel(self.root)
        t.overrideredirect(True)
        t.attributes("-topmost", True)
        bg = "#5b3a00" if outfit else "#7a1f1f"
        f = tk.Frame(t, bg=bg, padx=14, pady=10)
        f.pack()
        if name_only:
            title = f"Possible match with the central database: {row['username']}"
            how = "A name tag reads as an in-game name on the safety list. Display names aren't unique and can be changed, so this is UNVERIFIED: don't act on it alone."
        elif outfit:
            title = f"Possible match with the central database: {row['username']}"
            how = f"Outfit colours resemble a listed player ({ctx['conf']:.0%}). This is UNVERIFIED: don't act on it alone." if ctx else \
                  "Outfit colours resemble a listed player. This is UNVERIFIED: don't act on it alone."
        elif here:
            title = f"{row['username']} matches the central safety database"
            how = "Matched by name tag."
        else:
            title = f"{row['username']} is on the safety watchlist (seen by another device)"
            how = None
        tk.Label(f, text=title, bg=bg, fg="white", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        tk.Label(f, text=cats.capitalize(), bg=bg, fg="#ffd9d9", font=("Segoe UI", 10)).pack(anchor="w")
        if how:
            tk.Label(f, text=how, bg=bg, fg="white", font=("Segoe UI", 9), wraplength=330, justify="left").pack(anchor="w", pady=(4, 0))
        tk.Label(f, text="Report them in-game with Roblox's report tools.\nPlease don't follow or confront them.",
                 bg=bg, fg="white", font=("Segoe UI", 9), justify="left").pack(anchor="w", pady=(6, 0))
        can_send = bool(ctx) and self.prefs["cloud_on"] and self.prefs["cloud_submit"] and self.cloud.signed_in
        if can_send:
            b = tk.Button(f, text="Attach a cropped picture for the reviewers…", command=lambda: (t.destroy(), dialogs.evidence_dialog(self, ctx)))
            b.pack(anchor="w", pady=(8, 0))
        t.update_idletasks()
        self.toasts = [x for x in self.toasts if x.winfo_exists()]
        x = t.winfo_screenwidth() - t.winfo_reqwidth() - 20
        y = t.winfo_screenheight() - (t.winfo_reqheight() + 12) * (len(self.toasts) + 1) - 60
        t.geometry(f"+{x}+{y}")
        self.toasts.append(t)
        for w in (t, f, *[c for c in f.winfo_children() if not isinstance(c, tk.Button)]):
            w.bind("<Button-1>", lambda e, t=t: t.destroy())
        t.after(30000 if can_send else 15000, lambda: t.winfo_exists() and t.destroy())

    def nametag_evidence(self, row):
        """Prepare a cropped picture for a name-tag match (kept in memory; nothing is sent unless the owner agrees)."""
        im = self.evidence_for_nametag(row["username"])
        return self.make_evidence(row, "nametag", 1.0, im) if im is not None else None
        self.root.bell()

    def summarise(self):
        if not self.sel:
            return
        n = self.sel

        def run():
            chat = "\n".join(x for (x,) in self.db.q("SELECT text FROM chat WHERE player=? ORDER BY id DESC LIMIT 30", (n,)))
            notes = self.db.q("SELECT notes FROM players WHERE name=?", (n,))[0][0]
            p = f"Summarise this Roblox player in 2 short sentences.\nMy notes: {notes}\nTheir recent chat:\n{chat}"
            try:
                out = json.loads(http(config.OLLAMA, {"model": config.MODEL, "prompt": p, "stream": False}))["response"]
            except Exception as e:
                out = f"Ollama error: {e}"
            self.root.after(0, lambda: messagebox.showinfo(n, out))
        threading.Thread(target=run, daemon=True).start()

    # ---- training
    def learn(self, text, label):
        self.db.q("INSERT OR REPLACE INTO examples(text,label) VALUES(?,?)", (text, label))
        self.clf.fit(self.db.q("SELECT text,label FROM examples") + SEEDS)

    def reset_training(self):
        self.db.q("DELETE FROM examples")
        self.clf.reset()

    # ---- chat regions (several, each removable)
    def save_regions(self):
        self.db.put("regions", self.regions)

    def rects(self, frame):
        H, W = frame.shape[:2]
        return [(r[0] * W, r[1] * H, r[2] * W, r[3] * H) for r in self.regions]

    def set_chat_region(self, frac, pt=None):
        """Right-drag in the teach window adds a region; right-click inside one removes it."""
        if frac:
            self.regions.append(frac)
        elif pt:
            hit = [r for r in self.regions if r[0] <= pt[0] <= r[2] and r[1] <= pt[1] <= r[3]]
            if hit:
                self.regions.remove(min(hit, key=lambda r: (r[2] - r[0]) * (r[3] - r[1])))
        self.save_regions()
        self.status.config(text=f"{len(self.regions)} chat region(s)")

    # ---- deleting players / resetting in-memory state
    def drop_players(self, names):
        """Delete players with all their chat, flags, timeline, taught looks and image files."""
        names = list(dict.fromkeys(names))
        for i in range(0, len(names), 400):
            part = names[i:i + 400]
            m = ",".join("?" * len(part))
            for t, c in (("flags", "player"), ("chat", "player"), ("events", "player"), ("looks", "player"), ("players", "name")):
                self.db.q(f"DELETE FROM {t} WHERE {c} IN ({m})", part)
        gone = set(names)
        for f in os.listdir(IMG):
            if file_owner(f) in gone:
                remove_file(os.path.join(IMG, f))
        self.reset_state(gone)

    def reset_state(self, gone=None):
        """Forget in-memory tracking for `gone` players (or everyone) and clear the detail pane if needed."""
        if gone is None:
            self.track.clear()
            self.live, self.names, self.pend = set(), set(), {}
            self.gallery.via.clear()
            self.last_auto.clear()
            self.cands, self.cand_state, self.bodies = Candidates(), {}, {}
        else:
            for n in gone:
                self.track.pop(n, None)
                self.gallery.via.pop(n, None)
                self.last_auto.pop(n, None)
                self.names.discard(n)
            self.live = {n for n in self.live if n not in gone}
            self.pend = {k: v for k, v in self.pend.items() if v[0] not in gone}
        if gone is None or self.sel in gone:
            self.sel = None
            self.title.config(text="Select a player")
            self.thumb.config(image="")
            self.tags.delete(0, "end")
            self.notes.delete("1.0", "end")
            self.fill_tabs()
        self.gallery.reload()
        self.refresh()

    # ================================================================ scanning
    def scan_loop(self):
        ocr = self.ocr = RapidOCR()
        self.root.after(0, lambda: self.status.config(text="Ready"))
        last_sig, last_full = None, 0.0
        with mss.mss() as sct:
            while True:
                self.trigger.wait(timeout=max(1, self.opt.interval))
                forced = self.trigger.is_set()
                self.trigger.clear()
                if not (forced or self.opt.auto):
                    continue
                try:
                    frame = np.array(sct.grab(sct.monitors[self.opt.mon + 1]))[:, :, :3]
                    sig = frame_sig(frame)
                    # A screen that hasn't changed has nothing new to read. Re-read at least every 20 s anyway.
                    if not forced and same_frame(sig, last_sig) and time.time() - last_full < 20:
                        continue
                    last_sig, last_full = sig, time.time()
                    res, _ = ocr(frame)
                    with self.db.batch():
                        self.process(frame, res or [])
                except Exception as e:
                    self.log_error("scan", "scan failed")
                    self.root.after(0, lambda e=e: self.status.config(text=f"Error: {e}"))

    def process(self, frame, res):
        self.scan_n += 1
        self.rr_left, self._sk = 6, None
        ignored = {w for (w,) in self.db.q("SELECT word FROM ignored")}
        known, rects, ov = self.known_names(), self.rects(frame), list(self.ov_rects)
        self.names |= known
        self.frame_shape = frame.shape
        self._frame_ref = frame                                  # the latest frame, only so an alert can crop evidence from it
        boxes, words, alias_c = [], [], []
        for b, text, conf in res:
            conf = float(conf)
            if conf < MIN_CONF:
                continue
            x1, y1, x2, y2 = int(b[0][0]), int(b[0][1]), int(b[2][0]), int(b[2][1])
            if self.mostly_inside((x1, y1, x2, y2), ov):
                continue                                    # that's our own overlay / HUD / share window
            if any(r[0] <= x1 and r[1] <= y1 and x2 <= r[2] and y2 <= r[3] for r in rects):
                boxes.append(((y1 + y2) / 2, x1, y2 - y1, text.strip()))
            else:
                t = text.strip().lstrip("@")
                hit = self.sync.alias_match(t)
                if hit:
                    alias_c.append((hit, (x1, y1, x2, y2)))          # an in-game name a reviewer added for a listed player
                if NAME_RE.match(t) and t.lower() not in ignored:
                    words.append((t, conf, (x1, y1, x2, y2)))
        # The player list / leaderboard is a column of names too. Never read names from it.
        self.list_zones.update(find_lists([(t, bx) for t, _, bx in words]), self.scan_n)
        skip = self.ignore_rects(frame) + self.list_zones.rects()
        raw = {}
        for t, conf, bx in words:
            if inside_any(bx, skip):
                continue
            t = self.pick_name(frame, bx, t, conf)
            raw[self.canon(t)] = bx
        self.chat_lines(boxes, ignored, known, bool(rects))
        pb = self.detector(frame) if self.opt.discover else []
        tags = {}
        for n, bx in raw.items():
            if n in known or any(near(bx, p) for p in pb) or self.cand_state.get(n) == "ok":
                tags[n] = bx
            elif self.opt.discover and plausible(n):
                self.cands.seen(n, bx, self.scan_n)          # not a player yet: watch it, check it's a real account
                self.consider(n)
        self.cands.prune(self.scan_n)
        self.tag_seen, self.tag_boxes = set(tags), dict(tags)
        self.alias_step([(h, bx) for h, bx in alias_c if not inside_any(bx, skip)])
        self.activity(frame, tags)
        self.root.after(0, self.after_scan, len(tags), bool(rects))

    def alias_step(self, hits):
        """A name tag reads as an in-game name a reviewer added. Needs two scans in a row (OCR noise), then a LOCAL 'possible' alert."""
        now_hits = {}
        for (uid, username), bx in hits:
            now_hits[uid] = bx
            self.alias_boxes[username] = bx
        for uid in now_hits:
            self.alias_run[uid] = self.alias_run.get(uid, 0) + 1
            if self.alias_run[uid] == 2:
                self.sync.alias_spotted(uid)
        self.alias_run = {u: n for u, n in self.alias_run.items() if u in now_hits}

    def after_scan(self, n, has_region):
        hint = "" if has_region else "  |  no chat region (F1, right-drag over chat)"
        self.status.config(text=f"{datetime.now():%H:%M:%S}: {n} players{hint}")
        self.spot_btn.config(text=f"Spotted ({len(self.spotted())})")
        self.refresh()
        self.update_overlay()

    @staticmethod
    def mostly_inside(b, rects):
        a = max(1, (b[2] - b[0]) * (b[3] - b[1]))
        for r in rects:
            iw, ih = min(b[2], r[2]) - max(b[0], r[0]), min(b[3], r[3]) - max(b[1], r[1])
            if iw > 0 and ih > 0 and iw * ih / a > 0.5:
                return True
        return False

    # ---- names
    def known_names(self):
        return {n for (n,) in self.db.q(
            "SELECT name FROM players WHERE tags!='' OR notes!='' OR pinned=1 OR name IN (SELECT player FROM looks) "
            "OR name IN (SELECT player FROM flags WHERE status='confirmed')")}

    def sk_index(self):
        if self._sk is None or self._sk[0] != len(self.names):
            idx = {}
            for k in self.names:
                idx.setdefault(skel(k), []).append(k)
            self._sk = (len(self.names), idx)
        return self._sk[1]

    def canon(self, n):
        """Map an OCR'd name onto the player it most likely is (look-alike characters, clipped/extra trailing char)."""
        if n in self.names:
            return n
        ks = self.sk_index().get(skel(n))
        if ks:
            return max(sorted(ks), key=lambda k: difflib.SequenceMatcher(None, k, n).ratio())
        m = difflib.get_close_matches(n, self.names, 1, 0.86)
        return m[0] if m and abs(len(m[0]) - len(n)) <= 2 else n

    def reread(self, frame, box):
        """Second opinion on one nametag: crop it, enlarge it 2-4x and OCR it again, so 5/S/6 are easier to tell apart."""
        H, W = frame.shape[:2]
        x1, y1, x2, y2 = box
        c = frame[max(0, y1 - 3):min(H, y2 + 3), max(0, x1 - 3):min(W, x2 + 3), ::-1]
        if c.size == 0 or self.ocr is None:
            return None, 0.0
        im = Image.fromarray(np.ascontiguousarray(c))
        k = max(2, min(4, 72 // max(im.height, 1)))
        im = im.resize((im.width * k, im.height * k), Image.LANCZOS)
        arr = np.pad(np.asarray(im), ((14, 14), (14, 14), (0, 0)), mode="edge")[:, :, ::-1]
        res, _ = self.ocr(np.ascontiguousarray(arr))
        best = max(res or [], key=lambda r: float(r[2]), default=None)
        if not best:
            return None, 0.0
        t = best[1].strip().lstrip("@")
        return (t, float(best[2])) if NAME_RE.match(t) else (None, 0.0)

    def pick_name(self, frame, box, text, conf):
        if self.canon(text) in self.names or self.rr_left <= 0:
            return text                                     # already a player we know, or re-read budget used up
        if not difflib.get_close_matches(skel(text), self.sk_index(), 1, 0.6):
            return text                                     # looks nothing like a known player (menu text etc.)
        self.rr_left -= 1
        try:
            t2, c2 = self.reread(frame, box)
        except Exception:
            return text
        if t2 and t2 != text and (self.canon(t2) in self.names or c2 > conf):
            return t2
        return text

    # ---- chat intake
    def chat_lines(self, boxes, ignored, known, strict):
        rows, cur = [], []
        for y, x, h, t in sorted(boxes):
            if cur and y - cur[0][0] > 0.6 * cur[0][2]:
                rows.append(cur)
                cur = []
            cur.append((y, x, h, t))
        if cur:
            rows.append(cur)
        msgs, last = [], None
        for ln in rows:
            text = " ".join(t for *_, t in sorted(ln, key=lambda b: b[1]))
            m = CHAT_RE.match(text)
            if m:
                last = [m.group(1), m.group(2).strip(), ln[0][0], ln[0][2]]
                msgs.append(last)
            elif last and 0 < ln[0][0] - last[2] < 2.2 * last[3]:     # wrapped second line of the same message
                last[1] += " " + text.strip()
                last[2] = ln[0][0]
        for who, msg, *_ in msgs:
            who = self.canon(who)
            if who.lower() in ignored or (not strict and who not in known):
                continue
            key = who + "|" + re.sub(r"[^a-z0-9]", "", msg.lower())[:40]
            p = self.pend.setdefault(key, [who, msg, 0, 0])
            if p[3] != self.scan_n:
                p[2] += 1
            p[1], p[3] = msg, self.scan_n
            if p[2] == 2:                          # only trust a line seen in two scans in a row
                self.commit(who, msg)
        self.pend = {k: p for k, p in self.pend.items() if self.scan_n - p[3] < 30}

    def commit(self, who, msg):
        old = [t for (t,) in self.db.q("SELECT text FROM chat WHERE player=? ORDER BY id DESC LIMIT 30", (who,))]
        if any(difflib.SequenceMatcher(None, msg.lower(), o.lower()).ratio() > 0.9 for o in old):
            return
        self.db.ensure(who)
        self.names.add(who)
        self.db.q("INSERT OR IGNORE INTO chat(player,text,t) VALUES(?,?,?)", (who, msg, now()))
        cid = self.db.q("SELECT id FROM chat WHERE player=? AND text=?", (who, msg))[0][0]
        self.db.event(who, "chat", msg)
        for cat, pats in self.lexicon.items():
            if any(re.search(p, msg, re.I) for p in pats):
                self.db.q("INSERT INTO flags(chat_id,player,cat,src,t) VALUES(?,?,?,?,?)", (cid, who, cat, "lexicon", now()))
        if self.opt.ai_on:
            self.ai_jobs.put((cid, who, msg))

    # ---- presence / movement tracking
    def activity(self, frame, tags):
        self.update_tracks(frame, self.add_look_matches(frame, tags))

    def add_look_matches(self, frame, tags):
        """Keep refining known looks when nametag and look agree; surface players recognised by look alone."""
        tags, bodies = dict(tags), {}
        for box, n, approx in candidates(frame, tags, self.detector(frame), self.prefs["box_scale"] / 100):
            if n:
                bodies[n] = (box, approx)
            try:
                im = crop(frame, box)
                vec = embed(im)
                who, sim = self.gallery.match(vec)
            except Exception:
                continue
            self.check_central_outfit(vec, n, box, frame, im)
            if n and who == n and sim < 0.985 and time.time() - self.last_auto.get(n, 0) > 120:
                self.last_auto[n] = time.time()          # nametag and look agree -> safe to keep learning
                self.gallery.add(n, im, "auto")
            elif who and not n and who not in tags:      # recognised by look alone, nametag out of range
                tags[who] = (box[0], max(0, box[1] - 22), box[2], box[1])
                bodies[who] = (box, False)
                if who not in self.live:
                    self.db.event(who, "recognised by look", f"{sim:.0%}")
        self.bodies = bodies
        return tags

    def refresh_central(self):
        """Reload the shared list's outfit references now and then (cheap: a local table read)."""
        if time.time() - self._central_at > 30:
            self._central_at = time.time()
            try:
                self.central.load(self.sync.watch_look_rows() if self.prefs["cloud_on"] else [])
            except Exception:
                log.exception("loading outfit references failed")

    def check_central_outfit(self, vec, nametag, box, frame, im):
        """A person's outfit resembles a reference on the shared list. This is only ever 'possible, unverified': a local warning,
        no sighting sent, nobody flagged. A person whose name tag already matched is handled by the name-tag path."""
        if not self.prefs["cloud_on"]:
            return
        self.refresh_central()
        uid, sim = self.central.match(vec)
        if not uid:
            return
        row = self.sync.watch_row(uid)
        if not row or (nametag and nametag.lower() == row["username"].lower()):
            return
        t = time.time()
        if t - self._outfit_seen.get(uid, 0) < 600:
            return
        self._outfit_seen[uid] = t
        self.db.event(row["username"], "possible outfit match (unverified)", f"{sim:.0%}")
        ctx = self.make_evidence(row, "outfit", sim, im)
        self.root.after(0, self.show_alert, row, "outfit", ctx)

    def make_evidence(self, row, kind, conf, im):
        """Prepare (in memory only) the picture that COULD be sent. Nothing leaves the computer unless the owner agrees."""
        try:
            return {"uid": row["user_id"], "username": row["username"], "kind": kind, "conf": float(conf), "t": time.time(),
                    "jpeg": evidence_jpeg(im), "look": quantize(embed(im)) if kind == "nametag" else None}
        except Exception:
            log.exception("preparing evidence failed")
            return None

    def evidence_for_nametag(self, username):
        """Crop the player whose name tag just matched, from the latest frame."""
        frame = self._frame_ref
        if frame is None:
            return None
        key = next((n for n in list(self.bodies) if n.lower() == username.lower()), None)
        box = self.bodies[key][0] if key else None
        if box is None:
            tag = next((b for n, b in list(self.tag_boxes.items()) + list(self.alias_boxes.items()) if n.lower() == username.lower()), None)
            if tag is None:
                return None
            box = person_box(tag, self.frame_shape, self.prefs["box_scale"] / 100)
        return crop(frame, box)

    def send_evidence(self, ctx):
        """The owner saw the picture and agreed. Queue it for the reviewers."""
        if not (self.prefs["cloud_on"] and self.prefs["cloud_submit"] and self.cloud.signed_in):
            return False
        self.sync.queue_evidence(ctx["uid"], ctx["username"], ctx["kind"], ctx["conf"], ctx["jpeg"], ctx.get("look"))
        self.sync.wake.set()
        self.db.event(ctx["username"], "evidence sent", f"{ctx['kind']} {ctx['conf']:.0%}")
        self.status.config(text=f"Sent a cropped picture of {ctx['username']} to the reviewers")
        return True

    def update_tracks(self, frame, tags):
        t = time.time()
        common = [(n, tags[n]) for n in tags if n in self.track and self.track[n].get("pos")]
        shifts = [((b[0] + b[2]) / 2 - self.track[n]["pos"][0], (b[1] + b[3]) / 2 - self.track[n]["pos"][1]) for n, b in common]
        mx, my = (median(s[0] for s in shifts), median(s[1] for s in shifts)) if len(shifts) >= 3 else (0, 0)  # ignore camera pan
        for n, b in tags.items():
            self.db.ensure(n)
            tr = self.track.setdefault(n, {"pos": None, "still": t, "state": "visible", "missing": 0, "present": False, "crop": 0})
            c = ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
            tr["missing"] = 0
            self.mark_present(n, tr)
            self.mark_motion(n, tr, c, t, mx, my)
            tr["pos"] = c
            if t - tr["crop"] > CROP_EVERY:
                tr["crop"] = t
                self.save_crop(frame, n, b, c, t)
        for n, tr in self.track.items():
            if n not in tags:
                tr["missing"] += 1
                if tr["missing"] == GRACE and tr["present"]:
                    tr["present"] = False
                    self.db.event(n, "left")
        self.live = {n for n, tr in self.track.items() if tr["present"]}

    def mark_present(self, n, tr):
        if not tr["present"]:
            tr["present"] = True
            self.db.q("UPDATE players SET last_seen=?, seen=seen+1 WHERE name=?", (now(), n))
            self.db.event(n, "appeared")
            known = self.db.q("SELECT uid,verified FROM players WHERE name=? AND verified IS NOT NULL", (n,))
            if not known:
                self.lookups.put(n)
            elif known[0][1] == 1:
                self.check_watch(n, known[0][0])
        else:
            self.db.q("UPDATE players SET last_seen=? WHERE name=?", (now(), n))

    def mark_motion(self, n, tr, c, t, mx, my):
        if not tr["pos"]:
            return
        mv = math.hypot(c[0] - tr["pos"][0] - mx, c[1] - tr["pos"][1] - my)
        state = tr["state"]
        if mv > MOVE_PX:
            tr["still"], state = t, "moving"
        elif t - tr["still"] > IDLE_SECS:
            state = "idle/AFK"
        if state != tr["state"]:
            self.db.event(n, state)
            tr["state"] = state

    def save_crop(self, frame, n, b, c, t):
        w = max(b[2] - b[0], 60)
        x1, x2 = max(0, int(c[0] - 1.2 * w)), min(frame.shape[1], int(c[0] + 1.2 * w))
        y1, y2 = min(frame.shape[0] - 1, b[3]), min(frame.shape[0], b[3] + 260)
        if x2 > x1 and y2 > y1:
            im = Image.fromarray(frame[y1:y2, x1:x2][:, :, ::-1])
            path = f"{IMG}/{n}_{int(t)}.jpg"
            im.save(path)
            col = colours(im)
            self.db.q("UPDATE players SET colours=? WHERE name=?", (col, n))
            self.db.event(n, "screenshot", f"{path} | {col}")

    # ================================================================ background workers
    def lookup_worker(self):
        """Roblox username -> user id + headshot. Items are a name (known player) or (name, True) (a spotted candidate)."""
        while True:
            item = self.lookups.get()
            n, cand = item if isinstance(item, tuple) else (item, False)
            try:
                d = json.loads(http("https://users.roblox.com/v1/usernames/users",
                                    {"usernames": [n], "excludeBannedUsers": False}))["data"]
                if d:
                    uid = d[0]["id"]
                    if cand and not self.prefs["auto_add"]:
                        self.cand_state[n] = "verified"          # real account; waits in the Spotted list for you
                    else:
                        if cand:
                            self.db.ensure(n)
                            self.db.q("UPDATE players SET pinned=1 WHERE name=?", (n,))
                            self.cand_state[n] = "ok"
                            self.names.add(n)
                        self.finish_lookup(n, uid)
                else:
                    if cand:
                        self.cand_state[n] = "no"                # not a username (a display name, or just text)
                    else:
                        self.db.q("UPDATE players SET verified=0 WHERE name=?", (n,))
                self.root.after(0, self.refresh)
                if cand:
                    time.sleep(0.6)                              # be gentle with Roblox's API
            except Exception as e:
                if cand:
                    self.cand_state[n], self.cand_retry[n] = "error", time.time() + 60
                self.log_error("lookup", "roblox lookup failed")
                self.root.after(0, lambda e=e: self.status.config(text=f"Worker: {e}"))

    def finish_lookup(self, n, uid):
        u = json.loads(http(f"https://thumbnails.roblox.com/v1/users/avatar-headshot?userIds={uid}"
                            "&size=150x150&format=Png"))["data"][0]["imageUrl"]
        download(u, f"{IMG}/thumb_{n}.png")
        self.db.q("UPDATE players SET uid=?, verified=1 WHERE name=?", (uid, n))
        self.check_watch(n, uid)

    # ---- finding new players / logging by hand
    def consider(self, n):
        """A name has been seen on screen in close succession: ask Roblox whether it's a real account."""
        st = self.cand_state.get(n)
        if st == "error" and time.time() < self.cand_retry.get(n, 0):
            return
        if (st is None or st == "error") and self.cands.stable(n):
            self.cand_state[n] = "pending"
            self.lookups.put((n, True))

    def add_player(self, name, note=""):
        """Log a player by hand (Add player / Spotted list). They count as known from then on."""
        name = name.strip().lstrip("@")
        if not NAME_RE.match(name):
            return False
        self.db.ensure(name)
        self.db.q("UPDATE players SET pinned=1 WHERE name=?", (name,))
        if note:
            self.db.q("UPDATE players SET notes=? WHERE name=? AND notes=''", (note, name))
        self.db.q("DELETE FROM ignored WHERE word=?", (name.lower(),))
        self.names.add(name)
        self.cands.forget(name)
        self.cand_state[name] = "ok"
        self.lookups.put(name)
        self.refresh()
        return True

    def ignore_name(self, name):
        self.db.q("INSERT OR IGNORE INTO ignored VALUES(?)", (name.lower(),))
        self.cands.forget(name)
        self.cand_state[name] = "no"

    def spotted(self):
        """[(name, times_seen, state)] for names on screen that aren't players yet, most-seen first."""
        known = self.known_names()
        return [(n, k, self.cand_state.get(n, "")) for n, k in self.cands.listing(self.scan_n)
                if n not in known and self.cand_state.get(n) != "no"]

    def ignore_rects(self, frame):
        H, W = frame.shape[:2]
        return [(r[0] * W, r[1] * H, r[2] * W, r[3] * H) for r in self.ignore_zones]

    def set_ignore_zone(self, frac, pt=None):
        """Ctrl+right-drag in the teach window adds an area to ignore (e.g. the leaderboard); Ctrl+right-click removes."""
        if frac:
            self.ignore_zones.append(frac)
        elif pt:
            hit = [r for r in self.ignore_zones if r[0] <= pt[0] <= r[2] and r[1] <= pt[1] <= r[3]]
            if hit:
                self.ignore_zones.remove(min(hit, key=lambda r: (r[2] - r[0]) * (r[3] - r[1])))
        self.db.put("ignore_zones", self.ignore_zones)
        self.status.config(text=f"{len(self.ignore_zones)} ignored area(s)")

    def ai_worker(self):
        """Local LLM judge for committed chat lines."""
        while True:
            cid, who, msg = self.ai_jobs.get()
            try:
                self.judge(cid, who, msg)
            except Exception as e:
                self.log_error("ai", "AI judge failed")
                self.root.after(0, lambda e=e: self.status.config(text=f"AI: {e}"))

    def judge(self, cid, who, msg):
        res = verdict(msg, self.db.q("SELECT text,label FROM examples"), self.clf, self.opt.sens / 100, self.ai_off)
        if not res:
            return
        cat, conf = res
        self.db.q("INSERT INTO flags(chat_id,player,cat,src,t) VALUES(?,?,?,?,?)", (cid, who, cat, f"ai {conf:.0%}", now()))
        self.db.event(who, "flagged", f"{cat}: {msg}")
        self.escalate(who, cat, conf, msg)
        self.root.after(0, self.refresh)

    def escalate(self, who, cat, conf, msg):
        """A serious category at very high confidence goes straight to the reviewers' Urgent tab for a human to look at.
        This is NOT a confirmation and puts nobody on any list; it only asks for a human look sooner. Off by default."""
        p = self.prefs
        if not (p["escalate_on"] and p["cloud_on"] and p["cloud_submit"] and self.cloud.signed_in):
            return False
        if cat not in HIGH_SEVERITY or conf < p["escalate_min"] / 100:
            return False
        r = self.db.q("SELECT uid,verified FROM players WHERE name=?", (who,))
        if not r or r[0][1] != 1 or not r[0][0]:
            self.db.event(who, "escalation skipped", "no verified Roblox account for this name")
            return False
        self.sync.queue_flag(who, cat, "ai", msg, r[0][0], confidence=conf, escalated=True)
        self.sync.wake.set()
        self.db.event(who, "escalated", f"{cat} {conf:.0%}: sent to the reviewers' urgent list")
        self.root.after(0, self.show_escalation, who, cat)
        return True

    def show_escalation(self, who, cat):
        """Small notice that something was sent for urgent human review. Click to dismiss."""
        cat = cat.replace("_", " ")
        self.status.config(text=f"Sent {who} to the reviewers' urgent list ({cat})")
        t = tk.Toplevel(self.root)
        t.overrideredirect(True)
        t.attributes("-topmost", True)
        f = tk.Frame(t, bg="#5b3a00", padx=14, pady=10)
        f.pack()
        tk.Label(f, text="Sent for urgent review", bg="#5b3a00", fg="white", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        tk.Label(f, text=f"{who}: {cat}", bg="#5b3a00", fg="#ffe3b0", font=("Segoe UI", 10)).pack(anchor="w")
        tk.Label(f, text="A reviewer will decide. Nobody has been added to any list.\nIf you are in danger or someone is at risk, use the game's report tools now.",
                 bg="#5b3a00", fg="white", font=("Segoe UI", 9), justify="left").pack(anchor="w", pady=(6, 0))
        t.update_idletasks()
        self.toasts = [x for x in self.toasts if x.winfo_exists()]
        x = t.winfo_screenwidth() - t.winfo_reqwidth() - 20
        y = t.winfo_screenheight() - (t.winfo_reqheight() + 12) * (len(self.toasts) + 1) - 60
        t.geometry(f"+{x}+{y}")
        self.toasts.append(t)
        for w in (t, f, *f.winfo_children()):
            w.bind("<Button-1>", lambda e, t=t: t.destroy())
        t.after(20000, lambda: t.winfo_exists() and t.destroy())

    # ================================================================ live overlay
    def close_overlays(self):
        self._tick += 1                                      # stops any running share-view loop
        for a in ("ov", "sv"):
            w = getattr(self, a)
            if w:
                try:
                    w.w.destroy()
                except Exception:
                    pass
                setattr(self, a, None)
        if self.sct:
            self.sct.close()
            self.sct = None
        self.ov_rects = []

    def build_scene(self):
        """Everything the overlay shows, in screen-frame coordinates: people (box + label), unconfirmed names, ignored areas."""
        auto, man = self.autotags(), dict(self.db.q("SELECT name,tags FROM players"))
        scale, people = self.prefs["box_scale"] / 100, []
        for n in sorted(self.live):
            tag = self.tag_boxes.get(n)
            body, approx = self.bodies.get(n, (None, True))
            if body is None and tag is not None:
                body = person_box(tag, self.frame_shape, scale)
            if body is None:
                continue
            fl = auto.get(n, ()) if self.ov_flags.get() else ()
            conf = sorted(a for a in fl if not a.startswith("?"))
            sus = sorted(a for a in fl if a.startswith("?")) if self.show_sus.get() else []
            extra = [t.strip() for t in man.get(n, "").split(",") if t.strip()] + conf + sus
            via = f"  (look {self.gallery.via[n]:.0%})" if tag is None and n in self.gallery.via else ""
            col = "#c62828" if conf else "#ef6c00" if sus else "#1565c0" if extra else "#2e7d32"
            people.append({"key": n, "name": n + via, "extra": ", ".join(extra).replace("_", " "), "col": col,
                           "body": body, "approx": approx, "tag": tag})
        cands = [{"key": "?" + n, "name": n, "tag": rec["box"], "ok": self.cand_state.get(n) in ("ok", "verified")}
                 for n, rec in list(self.cands.c.items())
                 if self.scan_n - rec["scan"] <= 1 and n not in self.live][:8]
        H, W = self.frame_shape[:2]
        zones = [{"box": (r[0] * W, r[1] * H, r[2] * W, r[3] * H), "label": "ignored area"} for r in self.ignore_zones]
        zones += [{"box": r, "label": "ignored: player list"} for r in self.list_zones.rects()]
        return {"people": people, "cands": cands, "zones": zones}

    def scene_targets(self, scene):
        t = {}
        for p in scene["people"]:
            t[p["key"] + "|body"] = p["body"]
            if p["tag"] is not None:
                t[p["key"] + "|tag"] = p["tag"]
        for c in scene["cands"]:
            t[c["key"] + "|tag"] = c["tag"]
        return t

    def scene_now(self):
        """The latest scene with its boxes eased part of the way to where the last scan put them."""
        sc = self.ov_scene
        if not sc:
            return {"people": [], "cands": [], "zones": []}
        cur = self.ease.step()
        return {"zones": sc["zones"],
                "people": [{**p, "body": cur.get(p["key"] + "|body", p["body"]),
                            "tag": cur.get(p["key"] + "|tag", p["tag"]) if p["tag"] is not None else None} for p in sc["people"]],
                "cands": [{**c, "tag": cur.get(c["key"] + "|tag", c["tag"])} for c in sc["cands"]]}

    def hud_lines(self):
        """The 'what is it noting right now' feed: last 10 minutes of appear/leave/look-match events and flags."""
        if self.hud_corner.get() == "Off":
            return None
        cut = (datetime.now() - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
        rows = []
        for t, p, k, d in self.db.q("SELECT t,player,kind,detail FROM events WHERE t>=? AND kind IN "
                                    "('appeared','left','recognised by look','learned look') ORDER BY id DESC LIMIT 12", (cut,)):
            what = {"appeared": "appeared", "left": "left", "learned look": "look learned"}.get(k, f"seen by look {d}")
            rows.append((t, f"{p}  {what}"))
        if self.ov_flags.get():
            for t, p, c, st in self.db.q("SELECT t,player,cat,status FROM flags WHERE t>=? AND status!='dismissed' "
                                         "ORDER BY id DESC LIMIT 12", (cut,)):
                if st == "confirmed" or self.show_sus.get():
                    rows.append((t, f"FLAG {p}  {c}" + ("" if st == "confirmed" else "  (unconfirmed)")))
        rows.sort(reverse=True)
        return [f"{t[11:19]}  {s}"[:56] for t, s in rows[:8]]

    def update_overlay(self):
        if not self.overlay_on.get():
            self.close_overlays()
            return
        idx, share = self.mon.current() + 1, self.ov_mode.get() == OV_SHARE
        if self.ov_idx != idx or self.ov_mon is None:
            self.close_overlays()
            self.ov_idx = idx
            with mss.mss() as m:
                self.ov_mon = dict(m.monitors[idx])
        if share and self.ov:
            self.close_overlays()
        if not share and self.sv:
            self.close_overlays()
        self.ov_scene, self.ov_hud = self.build_scene(), self.hud_lines()
        self.ease.set(self.scene_targets(self.ov_scene))
        self._ov_dirty = True
        if share:
            if self.sv is None:
                self.sv, self.sct = ShareView(self.root, self.stop_overlay), mss.mss()
                self.root.after(50, self.share_tick, self._tick)
        else:
            if self.ov is None:
                self.ov = Overlay(self.root, self.ov_mon)
                self.root.after(50, self.ov_anim, self._tick)
            self.ov_rects = self.ov.draw(self.scene_now(), self.ov_hud, self.hud_corner.get())

    def stop_overlay(self):
        self.overlay_on.set(False)
        self.close_overlays()

    def ov_anim(self, tid):
        """~20 fps: glide the boxes toward where the last scan put them (scans only happen every second or two)."""
        if tid != self._tick or self.ov is None or not self.overlay_on.get():
            return
        try:
            if self._ov_dirty or not self.ease.settled():
                self._ov_dirty = False
                self.ov_rects = self.ov.draw(self.scene_now(), self.ov_hud, self.hud_corner.get())
        except Exception:
            self.log_error("overlay", "overlay draw failed")
        self.root.after(50, self.ov_anim, tid)

    def share_tick(self, tid):
        if tid != self._tick or self.sv is None or not self.overlay_on.get():
            return
        try:
            m = self.ov_mon
            shot = self.sct.grab(m)
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
            r = self.sv.rect_on(m)
            cx, cy = (r[0] + r[2]) / 2, (r[1] + r[3]) / 2
            self.sv.draw(img, self.scene_now(), self.ov_hud, self.hud_corner.get(), 0 <= cx < m["width"] and 0 <= cy < m["height"])
            self.ov_rects = [r]
        except Exception as e:
            self.status.config(text=f"Share view: {e}")
        self.root.after(80, self.share_tick, tid)
