"""Secondary dialogs. Each takes the App so it can reach the database and shared state."""
import os
import threading
import tkinter as tk
import webbrowser
from datetime import datetime, timedelta
from tkinter import messagebox, simpledialog, ttk

from . import build_info, cloud, config, health, paths, theme, tray, updater
from .config import CATS, IMG, NAME_RE
from .textutil import file_owner, remove_file


def ask_name(parent, suggestion, options):
    """Modal 'who is this?' prompt. Returns a valid username or None."""
    d = tk.Toplevel(parent)
    d.title("Who is this?")
    d.attributes("-topmost", True)
    out = []
    ttk.Label(d, text="Username for this avatar:").pack(padx=14, pady=(14, 4))
    cb = ttk.Combobox(d, values=options, width=28)
    cb.set(suggestion)
    cb.pack(padx=14)

    def ok(*_):
        out.append(cb.get().strip().lstrip("@"))
        d.destroy()
    cb.bind("<Return>", ok)
    ttk.Button(d, text="Learn", command=ok).pack(pady=10)
    d.grab_set()
    cb.focus_set()
    parent.wait_window(d)
    return out[0] if out and NAME_RE.match(out[0]) else None


def ai_dialog(app):
    d = tk.Toplevel(app.root)
    d.title("AI categories")
    ttk.Label(d, wraplength=420, text="Tick the categories the AI may flag. Your lexicon.json and the built-in patterns "
              "always apply, and AI flags stay '?' until you Confirm them.").pack(padx=10, pady=8)
    grid, vs = ttk.Frame(d), {}
    grid.pack(padx=10)
    for i, c in enumerate(CATS):
        vs[c] = tk.BooleanVar(d, value=c not in app.ai_off)
        ttk.Checkbutton(grid, text=c, variable=vs[c]).grid(row=i // 2, column=i % 2, sticky="w", padx=8)

    def save():
        app.ai_off = {c for c, v in vs.items() if not v.get()}
        app.db.put("ai_off", sorted(app.ai_off))
        d.destroy()
    ttk.Button(d, text="Save", command=save).pack(pady=8)


def train_dialog(app):
    d = tk.Toplevel(app.root)
    d.title("Train the AI")
    n, v = app.db.q("SELECT COUNT(*), SUM(label!='none') FROM examples")[0]
    ttk.Label(d, wraplength=380, text=f"{n} examples ({v or 0} violations). Confirm/Dismiss in a player's Flags tab "
              "also trains it. The on-device model takes over as a second opinion after 12 examples.").pack(padx=10, pady=8)
    e = ttk.Entry(d, width=52)
    e.pack(padx=10)
    cb = ttk.Combobox(d, values=["none"] + list(CATS), state="readonly")
    cb.set("none")
    cb.pack(pady=6)

    def add():
        if e.get().strip():
            app.learn(e.get().strip(), cb.get())
            e.delete(0, "end")

    def dismiss_all():
        app.db.q("UPDATE flags SET status='dismissed' WHERE status='pending' AND src LIKE 'ai%'")
        app.refresh()
    ttk.Button(d, text="Add example (message + correct label)", command=add).pack()
    ttk.Button(d, text="Dismiss all pending AI flags", command=dismiss_all).pack(pady=6)


def add_player_dialog(app):
    """Log a player by hand: type their Roblox username."""
    d = tk.Toplevel(app.root)
    d.title("Add player")
    d.attributes("-topmost", True)
    f = ttk.Frame(d, padding=14)
    f.pack()
    name, note = tk.StringVar(d), tk.StringVar(d)
    msg = ttk.Label(f, text="", foreground="#d33")
    ttk.Label(f, text="Roblox username").grid(row=0, column=0, sticky="w")
    e = ttk.Entry(f, textvariable=name, width=30)
    e.grid(row=1, column=0, pady=(2, 8))
    ttk.Label(f, text="Note (optional)").grid(row=2, column=0, sticky="w")
    ttk.Entry(f, textvariable=note, width=30).grid(row=3, column=0, pady=(2, 4))
    ttk.Label(f, text="Display names can't be looked up; type the @username if you know it.", wraplength=260,
              justify="left").grid(row=4, column=0, sticky="w")
    msg.grid(row=5, column=0, sticky="w")

    def go():
        n = name.get().strip().lstrip("@")
        if not NAME_RE.match(n):
            msg.config(text="3-20 letters, digits or _ only.")
            return
        app.add_player(n, note.get().strip())
        app.status.config(text=f"Added {n}")
        d.destroy()
    row = ttk.Frame(f)
    row.grid(row=6, column=0, pady=(10, 0))
    app.button(row, "Add", go, "add", accent=True).pack(side="left", padx=4)
    app.button(row, "Cancel", d.destroy).pack(side="left", padx=4)
    d.bind("<Return>", lambda _e: go())
    d.bind("<Escape>", lambda _e: d.destroy())
    e.focus_set()


def spotted_dialog(app):
    """Names the scan saw but didn't log: add the real players, ignore the rest."""
    d = tk.Toplevel(app.root)
    d.title("Spotted on screen")
    d.attributes("-topmost", True)
    f = ttk.Frame(d, padding=12)
    f.pack(fill="both", expand=True)
    ttk.Label(f, text="Names read from the game that aren't logged yet. Roblox accounts are added automatically when "
                      "'Add verified names' is on; anything else waits here.", wraplength=420, justify="left").pack(anchor="w")
    lb = tk.Listbox(f, width=52, height=12, selectmode="extended", activestyle="none")
    theme.style_text(lb)
    lb.pack(fill="both", expand=True, pady=8)
    names = []
    label = {"ok": "Roblox account", "verified": "Roblox account", "pending": "checking…", "error": "couldn't check", "": "not checked yet"}

    def fill():
        sel = {names[i] for i in lb.curselection()}
        rows = app.spotted()
        names[:] = [n for n, *_ in rows]
        lb.delete(0, "end")
        for i, (n, k, st) in enumerate(rows):
            lb.insert("end", f"{n}    seen {k}x    {label.get(st, st)}")
            if n in sel:
                lb.selection_set(i)
        if d.winfo_exists():
            d.after(2000, fill)

    def chosen():
        return [names[i] for i in lb.curselection()]

    def add():
        for n in chosen():
            app.add_player(n)
        fill()

    def ignore():
        for n in chosen():
            app.ignore_name(n)
        fill()
    row = ttk.Frame(f)
    row.pack()
    app.button(row, "Add as player", add, "add", accent=True).pack(side="left", padx=4)
    app.button(row, "Not a player", ignore, "ignore").pack(side="left", padx=4)
    app.button(row, "Close", d.destroy).pack(side="left", padx=4)
    fill()


def regions_dialog(app):
    d = tk.Toplevel(app.root)
    d.title("Chat regions and ignored areas")
    lb = tk.Listbox(d, width=46, height=8)
    lb.pack(padx=8, pady=8)

    def fill():
        lb.delete(0, "end")
        for i, r in enumerate(app.regions):
            lb.insert("end", f"Chat region {i + 1}: ({r[0]:.0%}, {r[1]:.0%}) to ({r[2]:.0%}, {r[3]:.0%})")
        for i, r in enumerate(app.ignore_zones):
            lb.insert("end", f"Ignored area {i + 1}: ({r[0]:.0%}, {r[1]:.0%}) to ({r[2]:.0%}, {r[3]:.0%})")

    def rm():
        s = lb.curselection()
        if s:
            if s[0] < len(app.regions):
                app.regions.pop(s[0])
                app.save_regions()
            else:
                app.ignore_zones.pop(s[0] - len(app.regions))
                app.db.put("ignore_zones", app.ignore_zones)
            fill()

    def clear():
        app.regions.clear()
        app.ignore_zones.clear()
        app.save_regions()
        app.db.put("ignore_zones", [])
        fill()
    row = ttk.Frame(d)
    row.pack(pady=6)
    ttk.Button(row, text="Remove selected", command=rm).pack(side="left", padx=4)
    ttk.Button(row, text="Remove all", command=clear).pack(side="left", padx=4)
    fill()


def cleanup_dialog(app):
    """Mass delete. Every button asks first and shows how many rows it will remove."""
    db = app.db
    d = tk.Toplevel(app.root)
    d.title("Data cleanup")
    d.attributes("-topmost", True)
    shown = list(app.tree.get_children())
    junk = [n for (n,) in db.q(
        "SELECT name FROM players WHERE tags='' AND notes='' AND name NOT IN (SELECT player FROM looks) "
        "AND name NOT IN (SELECT player FROM flags WHERE status='confirmed')")]
    crops = [f for f in os.listdir(IMG) if f.endswith(".jpg") and not f.startswith("look_") and file_owner(f)]
    ttk.Label(d, wraplength=430, text="Every button asks first and shows how many rows it will remove. "
              "Tip: shift/ctrl-click several players in the list and press Delete to remove just those.").pack(padx=12, pady=(10, 4))

    def done():
        db.q("VACUUM")
        d.destroy()
        app.refresh()
        cleanup_dialog(app)

    def row(label, n, fn):
        def go():
            if messagebox.askyesno("Data cleanup", f"{label}\n\n{n} item(s). This can't be undone.", parent=d):
                fn()
                done()
        ttk.Button(d, text=f"{label}  ({n})", command=go, state="normal" if n else "disabled").pack(fill="x", padx=12, pady=2)

    def del_flags(where):
        db.q(f"DELETE FROM flags WHERE {where}")

    def del_chat():
        db.q("DELETE FROM flags")
        db.q("DELETE FROM chat")

    def del_crops():
        for f in crops:
            remove_file(os.path.join(IMG, f))
        db.q("DELETE FROM events WHERE kind='screenshot'")

    def del_looks():
        db.q("DELETE FROM looks")
        for f in os.listdir(IMG):
            if f.startswith("look_") and f.endswith(".jpg"):
                remove_file(os.path.join(IMG, f))
        app.gallery.reload()

    row("Delete the players currently shown in the list (respects the search box)", len(shown), lambda: app.drop_players(shown))
    row("Delete players with no tags, notes, taught looks or confirmed flags", len(junk), lambda: app.drop_players(junk))
    row("Delete all pending AI flags", db.scalar("SELECT COUNT(*) FROM flags WHERE status='pending' AND src LIKE 'ai%'"),
        lambda: del_flags("status='pending' AND src LIKE 'ai%'"))
    row("Delete all dismissed flags", db.scalar("SELECT COUNT(*) FROM flags WHERE status='dismissed'"), lambda: del_flags("status='dismissed'"))
    row("Delete ALL chat logs (and the flags attached to them)", db.scalar("SELECT COUNT(*) FROM chat"), del_chat)
    row("Delete ALL timeline events", db.scalar("SELECT COUNT(*) FROM events"), lambda: db.q("DELETE FROM events"))
    row("Delete saved avatar screenshots (not looks or headshots)", len(crops), del_crops)
    row("Forget all taught looks", db.scalar("SELECT COUNT(*) FROM looks"), del_looks)
    row("Reset AI training (your labelled examples + model)", db.scalar("SELECT COUNT(*) FROM examples"), app.reset_training)
    row("Clear the 'not a player' ignore list", db.scalar("SELECT COUNT(*) FROM ignored"), lambda: db.q("DELETE FROM ignored"))

    age = ttk.Frame(d)
    age.pack(fill="x", padx=12, pady=6)
    days = tk.IntVar(d, 30)
    ttk.Label(age, text="Delete chat, timeline and flags older than").pack(side="left")
    ttk.Spinbox(age, from_=0, to=3650, width=5, textvariable=days).pack(side="left", padx=4)
    ttk.Label(age, text="days").pack(side="left")

    def older():
        cut = (datetime.now() - timedelta(days=days.get())).strftime("%Y-%m-%d %H:%M:%S")
        n = db.scalar("SELECT COUNT(*) FROM chat WHERE t<?", (cut,)) + db.scalar("SELECT COUNT(*) FROM events WHERE t<?", (cut,))
        if n and messagebox.askyesno("Data cleanup", f"Delete {n} chat/timeline rows (and their flags) older than {days.get()} days?", parent=d):
            db.q("DELETE FROM flags WHERE t<? OR chat_id IN (SELECT id FROM chat WHERE t<?)", (cut, cut))
            db.q("DELETE FROM chat WHERE t<?", (cut,))
            db.q("DELETE FROM events WHERE t<?", (cut,))
            done()
        elif not n:
            messagebox.showinfo("Data cleanup", "Nothing that old.", parent=d)
    ttk.Button(age, text="Delete", command=older).pack(side="left", padx=6)

    def wipe():
        if simpledialog.askstring("Wipe everything", "Deletes ALL players, chat, flags, timeline, looks, screenshots, AI "
                                  "training and the ignore list.\nType DELETE to confirm:", parent=d) != "DELETE":
            return
        for t in ("flags", "chat", "events", "looks", "players", "ignored", "examples"):
            db.q(f"DELETE FROM {t}")
        for f in os.listdir(IMG):
            if f.lower().endswith((".jpg", ".png")):
                remove_file(os.path.join(IMG, f))
        app.reset_training()
        app.reset_state()
        done()
    ttk.Button(d, text="WIPE EVERYTHING…", command=wipe).pack(fill="x", padx=12, pady=(10, 12))


def settings_dialog(app):
    d = tk.Toplevel(app.root)
    d.title("Settings")
    d.attributes("-topmost", True)
    f = ttk.Frame(d, padding=12)
    f.pack()
    url, model = tk.StringVar(d, app.prefs["ollama"]), tk.StringVar(d, app.prefs["model"])
    use_tray = tk.BooleanVar(d, app.prefs["tray"] and tray.available())

    ttk.Label(f, text="AI model server (Ollama)", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, columnspan=2, sticky="w")
    ttk.Label(f, text="Address").grid(row=1, column=0, sticky="w", pady=2)
    ttk.Entry(f, textvariable=url, width=44).grid(row=1, column=1, pady=2)
    ttk.Label(f, text="Model").grid(row=2, column=0, sticky="w", pady=2)
    ttk.Entry(f, textvariable=model, width=44).grid(row=2, column=1, pady=2)

    ttk.Label(f, text="Window", font=("Segoe UI", 10, "bold")).grid(row=3, column=0, columnspan=2, sticky="w", pady=(12, 0))
    cb = ttk.Checkbutton(f, text="Close button hides to the system tray", variable=use_tray,
                         state="normal" if tray.available() else "disabled")
    cb.grid(row=4, column=0, columnspan=2, sticky="w")
    if not tray.available():
        ttk.Label(f, text="(pip install pystray to enable)").grid(row=5, column=0, columnspan=2, sticky="w")

    ttk.Label(f, text="Your data", font=("Segoe UI", 10, "bold")).grid(row=6, column=0, columnspan=2, sticky="w", pady=(12, 0))
    ttk.Label(f, text=paths.data_dir(), wraplength=420, justify="left").grid(row=7, column=0, columnspan=2, sticky="w")

    def open_data():
        try:
            paths.open_folder(paths.data_dir())
        except Exception as e:
            messagebox.showerror("Settings", f"Couldn't open the folder: {e}", parent=d)
    ttk.Button(f, text="Open data folder", command=open_data).grid(row=8, column=0, columnspan=2, sticky="w", pady=4)

    auto_update = tk.BooleanVar(d, app.prefs["auto_update"])
    ttk.Label(f, text=f"Updates (version {build_info.VERSION})", font=("Segoe UI", 10, "bold")).grid(
        row=9, column=0, columnspan=2, sticky="w", pady=(12, 0))
    feed = bool(updater.feed_url())
    ttk.Checkbutton(f, text="Check for updates automatically", variable=auto_update,
                    state="normal" if feed else "disabled").grid(row=10, column=0, columnspan=2, sticky="w")
    ttk.Button(f, text="Check now", command=lambda: app.check_updates(manual=True)).grid(row=11, column=0, columnspan=2, sticky="w", pady=4)
    if not feed:
        ttk.Label(f, text="(this build has no update feed configured)").grid(row=12, column=0, columnspan=2, sticky="w")

    box_scale, theme_var = tk.IntVar(d, app.prefs["box_scale"]), tk.StringVar(d, app.prefs["theme"])
    ttk.Label(f, text="Overlay and look", font=("Segoe UI", 10, "bold")).grid(row=13, column=0, columnspan=2, sticky="w", pady=(12, 0))
    ttk.Label(f, text="Avatar box size %").grid(row=14, column=0, sticky="w", pady=2)
    ttk.Spinbox(f, from_=50, to=200, increment=10, width=5, textvariable=box_scale).grid(row=14, column=1, sticky="w")
    ttk.Label(f, text="Theme (restart to apply)").grid(row=15, column=0, sticky="w", pady=2)
    ttk.Combobox(f, state="readonly", width=8, values=["dark", "light"], textvariable=theme_var).grid(row=15, column=1, sticky="w")

    def save():
        u, m = url.get().strip(), model.get().strip()
        if not u.startswith("http") or not m:
            messagebox.showerror("Settings", "Address must start with http and a model name is needed.", parent=d)
            return
        try:
            scale = min(200, max(50, int(box_scale.get())))
        except (tk.TclError, ValueError):
            scale = app.prefs["box_scale"]
        app.prefs.update(ollama=u, model=m, tray=bool(use_tray.get()), auto_update=bool(auto_update.get()),
                         box_scale=scale, theme=theme_var.get())
        config.OLLAMA, config.MODEL = u, m
        app.apply_tray_pref()
        app.save_prefs()
        d.destroy()
    row = ttk.Frame(f)
    row.grid(row=16, column=0, columnspan=2, pady=(12, 0))
    ttk.Button(row, text="Save", command=save).pack(side="left", padx=4)
    ttk.Button(row, text="Cancel", command=d.destroy).pack(side="left", padx=4)


def cloud_dialog(app):
    """Sign in to the shared backend and choose what to share."""
    d = tk.Toplevel(app.root)
    d.title("Shared safety list")
    d.attributes("-topmost", True)
    f = ttk.Frame(d, padding=12)
    f.pack()
    on = tk.BooleanVar(d, app.prefs["cloud_on"])
    submit = tk.BooleanVar(d, app.prefs["cloud_submit"])
    email, pw, state = tk.StringVar(d, app.cloud.email), tk.StringVar(d), tk.StringVar(d)

    ttk.Label(f, text="Get the shared watchlist and alerts from the safety team's reviewers.", wraplength=430,
              justify="left").grid(row=0, column=0, columnspan=2, sticky="w")
    ttk.Label(f, text="Only reviewers add people to the list, never the AI. Alerts are a prompt to report the player "
                      "in-game, not to follow or confront them.", wraplength=430, justify="left").grid(
        row=1, column=0, columnspan=2, sticky="w", pady=(4, 8))
    if not app.cloud.ready:
        ttk.Label(f, text="This build isn't connected to a backend yet (no Firebase settings).", foreground="#a33",
                  wraplength=430).grid(row=2, column=0, columnspan=2, sticky="w")

    acct = ttk.Frame(f)
    acct.grid(row=3, column=0, columnspan=2, sticky="w")
    status = ttk.Label(f, textvariable=state, wraplength=430, justify="left")
    status.grid(row=6, column=0, columnspan=2, sticky="w", pady=(8, 0))

    def describe():
        for w in acct.winfo_children():
            w.destroy()
        if app.cloud.signed_in:
            role = app.cloud.role
            ttk.Label(acct, text=f"Signed in as {app.cloud.email}" + (f" ({role})" if role else "")).pack(side="left")
            ttk.Button(acct, text="Sign out", command=sign_out).pack(side="left", padx=8)
        else:
            ttk.Label(acct, text="Email").grid(row=0, column=0, sticky="w")
            ttk.Entry(acct, textvariable=email, width=34).grid(row=0, column=1, pady=2)
            ttk.Label(acct, text="Password").grid(row=1, column=0, sticky="w")
            ttk.Entry(acct, textvariable=pw, show="•", width=34).grid(row=1, column=1, pady=2)
            ttk.Button(acct, text="Sign in", command=sign_in).grid(row=2, column=1, sticky="w", pady=4)
        s = app.sync
        state.set(f"{s.message}" + (f"  ·  last sync {datetime.fromtimestamp(s.last_ok):%H:%M:%S}" if s.last_ok else ""))

    def sign_in():
        e, p = email.get().strip(), pw.get()
        if not e or not p:
            return
        state.set("Signing in…")

        def work():
            try:
                app.cloud.sign_in(e, p)
                err = None
            except cloud.CloudError as ex:
                err = str(ex)
            except Exception:
                err = "Something went wrong signing in (see the log)."
            if d.winfo_exists():
                d.after(0, done, err)
        threading.Thread(target=work, daemon=True).start()

    def done(err):
        pw.set("")
        if err:
            describe()
            state.set(err)
        else:
            on.set(True)
            describe()
            app.sync.wake.set()

    def sign_out():
        app.cloud.sign_out()
        app.sync.reset_cache()
        describe()

    ttk.Checkbutton(f, text="Use the shared list (check for watched players, get alerts)", variable=on).grid(
        row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))
    ttk.Checkbutton(f, text="Send flags to the reviewers when I press Confirm", variable=submit).grid(
        row=5, column=0, columnspan=2, sticky="w")
    ttk.Label(f, text="What's sent: the username, the chat line and category you confirmed, and a random device id. "
                      "Never sent: screenshots, outfit data, or your other chat.", wraplength=430, justify="left",
              foreground="#555").grid(row=7, column=0, columnspan=2, sticky="w", pady=(8, 0))

    def save():
        app.prefs.update(cloud_on=bool(on.get()), cloud_submit=bool(submit.get()))
        app.save_prefs()
        app.sync.wake.set()
        d.destroy()
    row = ttk.Frame(f)
    row.grid(row=8, column=0, columnspan=2, pady=(12, 0))
    ttk.Button(row, text="Save", command=save).pack(side="left", padx=4)
    ttk.Button(row, text="Sync now", command=lambda: (app.sync.wake.set(), d.after(2500, describe))).pack(side="left", padx=4)
    ttk.Button(row, text="Close", command=d.destroy).pack(side="left", padx=4)
    describe()


def update_dialog(app, info):
    """A newer version exists. Install now (packaged Windows build), open the download page, remind later, or skip it."""
    d = tk.Toplevel(app.root)
    d.title("Update available")
    d.attributes("-topmost", True)
    f = ttk.Frame(d, padding=12)
    f.pack(fill="both")
    ttk.Label(f, text=f"Version {info['version']} is available (you have {build_info.VERSION}).",
              font=("Segoe UI", 11, "bold")).pack(anchor="w")
    if info["notes"]:
        t = tk.Text(f, width=64, height=10, wrap="word")
        t.insert("1.0", info["notes"])
        t.config(state="disabled")
        t.pack(pady=8)
    bar = ttk.Progressbar(f, length=420, mode="determinate")
    msg = ttk.Label(f, text="")
    row = ttk.Frame(f)
    row.pack(pady=(8, 0))
    busy = {"on": False}

    def install():
        if busy["on"]:
            return
        busy["on"] = True
        btn_install.config(state="disabled")
        bar.pack(pady=(8, 0))
        msg.pack()
        msg.config(text="Downloading…")

        def progress(done, total):
            def paint():
                try:
                    if total:
                        bar.config(value=100 * done / total)
                    msg.config(text=f"Downloading… {done / 1e6:.0f} MB")
                except tk.TclError:
                    pass
            app.root.after(0, paint)

        def work():
            try:
                path = updater.download(info, progress)
            except Exception as e:
                app.log_error("update", "update download failed")
                app.root.after(0, lambda e=e: (messagebox.showerror("Update", f"The update couldn't be installed:\n\n{e}", parent=d),
                                               busy.update(on=False), btn_install.config(state="normal")))
                return

            def go():
                msg.config(text="Installing… the app will restart.")
                try:
                    updater.launch_installer(path)
                except OSError as e:
                    messagebox.showerror("Update", f"Couldn't start the installer:\n\n{e}", parent=d)
                    busy["on"] = False
                    btn_install.config(state="normal")
                    return
                app.quit()
            app.root.after(0, go)
        threading.Thread(target=work, daemon=True).start()

    def open_page():
        webbrowser.open(info["url"])
        d.destroy()

    def skip():
        app.prefs["skip_version"] = info["version"]
        app.save_prefs()
        d.destroy()

    if updater.can_install():
        btn_install = ttk.Button(row, text="Install now", command=install)
    else:
        btn_install = ttk.Button(row, text="Download", command=open_page)
    btn_install.pack(side="left", padx=4)
    ttk.Button(row, text="Later", command=d.destroy).pack(side="left", padx=4)
    ttk.Button(row, text="Skip this version", command=skip).pack(side="left", padx=4)


def setup_dialog(app):
    """What's installed, what's reachable, and how to fix anything that isn't."""
    d = tk.Toplevel(app.root)
    d.title("Setup check")
    d.attributes("-topmost", True)
    body = ttk.Frame(d, padding=12)
    body.pack(fill="both")
    ttk.Label(body, wraplength=460, text="Required items must be green for scanning to work. Optional items just add features.").pack(anchor="w")
    grid = ttk.Frame(body)
    grid.pack(fill="x", pady=8)
    SYMBOL = {"ok": ("✓", "#2e7d32"), "missing": ("✗", "#c62828"), "optional": ("–", "#757575")}
    state = {"n": 0}

    def line(label, status, detail):
        r = state["n"]
        state["n"] += 1
        sym, col = SYMBOL[status]
        tk.Label(grid, text=sym, fg=col, font=("Segoe UI", 12, "bold")).grid(row=r, column=0, sticky="w", padx=(0, 8))
        ttk.Label(grid, text=label, font=("Segoe UI", 10, "bold")).grid(row=r, column=1, sticky="w")
        ttk.Label(grid, text=detail, wraplength=340, justify="left").grid(row=r, column=2, sticky="w", padx=8)

    def paint():
        for w in grid.winfo_children():
            w.destroy()
        state["n"] = 0
        line(*health.platform_row())
        line(*health.data_row())
        for row in health.package_rows():
            line(*row)
        ollama_row = state["n"]
        state["n"] += 1
        wait = ttk.Label(grid, text="Checking Ollama…")
        wait.grid(row=ollama_row, column=1, columnspan=2, sticky="w")
        url, model = config.OLLAMA, config.MODEL

        def work():
            ok, detail = health.check_ollama(url, model)

            def show():
                try:
                    if not d.winfo_exists():
                        return
                    wait.destroy()
                    sym, col = SYMBOL["ok" if ok else "optional"]
                    tk.Label(grid, text=sym, fg=col, font=("Segoe UI", 12, "bold")).grid(row=ollama_row, column=0, sticky="w", padx=(0, 8))
                    ttk.Label(grid, text="Ollama (AI flagging)", font=("Segoe UI", 10, "bold")).grid(row=ollama_row, column=1, sticky="w")
                    ttk.Label(grid, text=detail, wraplength=340, justify="left").grid(row=ollama_row, column=2, sticky="w", padx=8)
                except tk.TclError:
                    pass
            app.root.after(0, show)
        threading.Thread(target=work, daemon=True).start()

    paint()
    row = ttk.Frame(body)
    row.pack(pady=(6, 0))
    ttk.Button(row, text="Re-check", command=paint).pack(side="left", padx=4)
    ttk.Button(row, text="Close", command=d.destroy).pack(side="left", padx=4)
