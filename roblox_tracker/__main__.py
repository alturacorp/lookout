import ctypes
import sys


def selftest():
    """Used by the CI build to prove the packaged exe can load everything (bundled OCR models included). Exit 0 = ok."""
    from . import build_info, logs
    log = logs.setup()
    log.info("selftest start: version %s, frozen=%s", build_info.VERSION, bool(getattr(sys, "frozen", False)))
    try:
        import mss  # noqa: F401
        import numpy as np
        import tkinter  # noqa: F401
        from PIL import Image, ImageDraw, ImageTk  # noqa: F401
        from rapidocr_onnxruntime import RapidOCR

        from . import app  # noqa: F401  (imports every module the app uses)
        img = Image.new("RGB", (360, 90), "white")
        ImageDraw.Draw(img).text((12, 30), "toes555: hello world", fill="black")
        res, _ = RapidOCR()(np.asarray(img)[:, :, ::-1])      # loads the bundled detection + recognition models
        log.info("selftest OK (ocr returned %d text boxes)", len(res or []))
        return 0
    except Exception:
        log.exception("selftest FAILED")
        return 2


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--version" in argv:
        from . import build_info
        print(build_info.VERSION)
        return 0
    if "--selftest" in argv:
        return selftest()

    from . import logs, paths
    log = logs.setup()
    try:
        import tkinter as tk
        from tkinter import messagebox
    except ImportError:
        print("Python's tkinter is missing. Reinstall Python from python.org and tick 'tcl/tk and IDLE'.", file=sys.stderr)
        return 1

    from .instance import SingleInstance
    inst = SingleInstance()
    if not inst.acquire():
        log.info("another copy is already running; asked it to show itself")
        return 0

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)   # make Tk and screen-grab pixels line up
    except Exception:
        pass
    root = tk.Tk()
    root.withdraw()

    from . import health
    missing = health.missing_required()
    if missing:
        cmd = "pip install " + " ".join(missing)
        messagebox.showerror("Lookout", f"Some required packages are missing:\n\n    {cmd}\n\nInstall them, then start the app again.")
        inst.release()
        return 1

    legacy = paths.find_legacy()
    if legacy:
        import os
        here = os.getcwd()
        if messagebox.askyesno("Import existing data",
                               f"Found tracker data in:\n{here}\n\nCopy it into the app's data folder?\n{paths.data_dir()}\n\n"
                               "Your originals are left untouched."):
            paths.migrate(here, legacy)
            log.info("imported existing data from %s: %s", here, ", ".join(legacy))
        else:
            paths.mark_legacy_checked()

    try:
        from .app import App
        App(root, inst)
    except Exception as e:
        log.exception("startup failed")
        messagebox.showerror("Lookout", f"The app couldn't start:\n\n{e}\n\nDetails are in {paths.data_path('logs', 'tracker.log')}")
        inst.release()
        return 1
    root.deiconify()
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
