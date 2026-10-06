"""Saved toolbar/settings state (one JSON blob under the 'prefs' key of the settings table)."""
import re

from .config import CORNERS, MODEL, OLLAMA, OV_SHARE, OV_TOP

DEFAULTS = {
    "monitor": 0, "auto": True, "interval": 2, "ai_on": True, "sens": 80, "discover": True,
    "ov_mode": OV_TOP, "hud": CORNERS[0], "ov_flags": True, "show_sus": False,
    "geometry": "1150x680", "ollama": OLLAMA, "model": MODEL, "tray": False, "setup_done": False,
    "auto_update": True, "last_update_check": 0, "skip_version": "",
    "cloud_on": False, "cloud_submit": True, "escalate_on": False, "escalate_min": 90,
    "auto_add": True, "box_scale": 100, "theme": "dark", "keep_days": 90,
}
_GEOM = re.compile(r"^\d+x\d+(\+-?\d+\+-?\d+)?$")


def load(db):
    saved = db.get("prefs", {}) or {}
    p = dict(DEFAULTS)
    p.update({k: v for k, v in saved.items() if k in DEFAULTS and type(v) is type(DEFAULTS[k])})
    p["interval"] = min(30, max(1, p["interval"]))
    p["sens"] = min(95, max(30, p["sens"]))
    p["monitor"] = max(0, p["monitor"])
    p["box_scale"] = min(200, max(50, p["box_scale"]))
    p["keep_days"] = min(3650, max(0, p["keep_days"]))
    p["escalate_min"] = min(99, max(70, p["escalate_min"]))
    if p["theme"] not in ("dark", "light"):
        p["theme"] = "dark"
    if p["ov_mode"] not in (OV_TOP, OV_SHARE):
        p["ov_mode"] = OV_TOP
    if p["hud"] not in CORNERS:
        p["hud"] = CORNERS[0]
    if not _GEOM.match(p["geometry"]):
        p["geometry"] = DEFAULTS["geometry"]
    if not p["ollama"].startswith("http"):
        p["ollama"] = OLLAMA
    if not p["model"].strip():
        p["model"] = MODEL
    return p


def save(db, p):
    db.put("prefs", p)
