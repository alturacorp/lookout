"""Look and feel. Uses the Sun Valley theme (pip install sv-ttk) when it's installed, else a tidy built-in dark/light style.

Plain Tk widgets (Text, Listbox, Treeview row colours) don't follow ttk themes, so they get their colours from here too.
"""
from tkinter import ttk

PALETTES = {
    "dark": {"bg": "#1c1c1c", "panel": "#242424", "fg": "#f0f0f0", "mute": "#9a9a9a", "accent": "#4c8dff", "field": "#2b2b2b",
             "line": "#3a3a3a", "live": "#1f4d2e", "flag": "#ff6b6b", "sel": "#33507f"},
    "light": {"bg": "#f6f6f6", "panel": "#ffffff", "fg": "#1c1c1c", "mute": "#666666", "accent": "#1f5fd6", "field": "#ffffff",
              "line": "#d6d6d6", "live": "#d8f3dc", "flag": "#b00020", "sel": "#c8dcff"},
}
FONT = ("Segoe UI", 10)
_state = {"name": "dark", "sv": False}


def colours():
    return PALETTES[_state["name"]]


def sv_active():
    return _state["sv"]


def style_name(kind):
    """ttk style for 'switch' / 'accent' buttons: Sun Valley's if available, else the plain one."""
    if _state["sv"]:
        return {"switch": "Switch.TCheckbutton", "accent": "Accent.TButton"}.get(kind, "")
    return {"switch": "TCheckbutton", "accent": "Accent.TButton"}.get(kind, "")


def apply(root, name="dark"):
    """Apply the theme; returns True if Sun Valley was used."""
    name = name if name in PALETTES else "dark"
    _state["name"] = name
    try:
        import sv_ttk
        sv_ttk.set_theme(name)
        _state["sv"] = True
    except Exception:
        _state["sv"] = False
        _fallback(root, PALETTES[name])
    c = PALETTES[name]
    try:
        root.configure(bg=c["bg"])
        root.option_add("*Font", FONT)
    except Exception:
        pass
    return _state["sv"]


def _fallback(root, c):
    s = ttk.Style(root)
    try:
        s.theme_use("clam")
    except Exception:
        return
    s.configure(".", background=c["bg"], foreground=c["fg"], fieldbackground=c["field"], bordercolor=c["line"],
                lightcolor=c["line"], darkcolor=c["line"], troughcolor=c["panel"], font=FONT)
    s.configure("TButton", padding=(10, 5), background=c["panel"], borderwidth=1)
    s.map("TButton", background=[("active", c["line"])])
    s.configure("Accent.TButton", background=c["accent"], foreground="#ffffff")
    s.map("Accent.TButton", background=[("active", c["accent"])])
    s.configure("TLabelframe", background=c["bg"], bordercolor=c["line"])
    s.configure("TLabelframe.Label", background=c["bg"], foreground=c["mute"])
    s.configure("Treeview", background=c["panel"], fieldbackground=c["panel"], foreground=c["fg"], rowheight=26, borderwidth=0)
    s.configure("Treeview.Heading", background=c["bg"], foreground=c["mute"], padding=(8, 6), relief="flat")
    s.map("Treeview", background=[("selected", c["sel"])], foreground=[("selected", c["fg"])])
    s.configure("TNotebook", background=c["bg"], borderwidth=0)
    s.configure("TNotebook.Tab", padding=(14, 6), background=c["bg"], foreground=c["mute"])
    s.map("TNotebook.Tab", background=[("selected", c["panel"])], foreground=[("selected", c["fg"])])
    for w in ("TCheckbutton", "TRadiobutton"):
        s.configure(w, background=c["bg"], foreground=c["fg"])
    s.configure("TEntry", padding=4)
    s.configure("TCombobox", padding=4)


def row_height(tree, h=28):
    try:
        ttk.Style(tree).configure("Treeview", rowheight=h)
    except Exception:
        pass


def style_text(w):
    """Colour a plain tk.Text / tk.Listbox to match the theme."""
    c = colours()
    try:
        w.configure(bg=c["panel"], fg=c["fg"], insertbackground=c["fg"], relief="flat", highlightthickness=1,
                    highlightbackground=c["line"], highlightcolor=c["accent"], selectbackground=c["sel"],
                    font=("Segoe UI", 10), padx=6, pady=4)
    except Exception:
        pass


def tree_tags(tree):
    c = colours()
    tree.tag_configure("live", background=c["live"])
    tree.tag_configure("flag", foreground=c["flag"])
