"""Setup check: what is installed / reachable, and how to fix what is not."""
import importlib.util
import json
import sys
import urllib.request

from . import paths

REQUIRED = [("mss", "mss", "screen capture"),
            ("numpy", "numpy", "maths"),
            ("PIL", "pillow", "images"),
            ("rapidocr_onnxruntime", "rapidocr-onnxruntime", "reads nametags and chat")]
OPTIONAL = [("ultralytics", "ultralytics", "better avatar boxes (large download)"),
            ("keyboard", "keyboard", "F1 while Roblox is focused"),
            ("pystray", "pystray", "system tray icon")]


def have(mod):
    try:
        return mod in sys.modules or importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError):
        return False


def frozen():
    """True in the packaged (PyInstaller) build, where everything required is bundled inside the exe."""
    return bool(getattr(sys, "frozen", False))


def missing_required():
    """pip names of required packages that are not installed."""
    if frozen():
        return []
    return [pip for mod, pip, _ in REQUIRED if not have(mod)]


def package_rows():
    """[(label, status, detail)] with status 'ok' | 'missing' | 'optional'."""
    rows = []
    for mod, pip, why in REQUIRED:
        if frozen():
            rows.append((pip, "ok", f"{why} (bundled)"))
            continue
        ok = have(mod)
        rows.append((pip, "ok" if ok else "missing", why if ok else f"pip install {pip}"))
    for mod, pip, why in OPTIONAL:
        ok = have(mod)
        if frozen():
            rows.append((f"{pip} (optional)", "ok" if ok else "optional", why if ok else f"{why}: not included in this installer build"))
        else:
            rows.append((f"{pip} (optional)", "ok" if ok else "optional", why if ok else f"{why}: pip install {pip}"))
    return rows


def platform_row():
    if sys.platform == "win32":
        return ("Windows", "ok", "overlay and click-through supported")
    return (sys.platform, "optional", "the live overlay is Windows-only; scanning and the database still work")


def data_row():
    try:
        d = paths.data_dir()
        probe = paths.data_path(".write_test")
        with open(probe, "w") as f:
            f.write("ok")
        import os
        os.remove(probe)
        return ("Data folder", "ok", d)
    except OSError as e:
        return ("Data folder", "missing", f"can't write to {paths.data_dir()}: {e}")


def ollama_base(url):
    return url.split("/api/")[0].rstrip("/")


def check_ollama(url, model, timeout=2.0):
    """(ok, detail). Blocking: run it off the UI thread."""
    try:
        with urllib.request.urlopen(ollama_base(url) + "/api/tags", timeout=timeout) as r:
            names = [m.get("name", "") for m in json.load(r).get("models", [])]
    except Exception:
        return False, "Ollama isn't reachable. Install it from ollama.com and make sure it's running, or untick AI chat flagging."
    want = model if ":" in model else model + ":latest"
    if want in names or model in names:
        return True, f"running, model {model} is ready"
    return False, f"running, but model {model} isn't downloaded. Run: ollama pull {model}"
