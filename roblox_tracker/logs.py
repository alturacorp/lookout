"""File logging in the data folder (logs/tracker.log, rotated). Never logs chat text."""
import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler

from . import paths

log = logging.getLogger("tracker")


def setup():
    if log.handlers:
        return log
    log.setLevel(logging.INFO)
    os.makedirs(paths.data_path("logs"), exist_ok=True)
    h = RotatingFileHandler(paths.data_path("logs", "tracker.log"), maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(h)

    def main_hook(t, v, tb):
        log.critical("Unhandled exception", exc_info=(t, v, tb))
        sys.__excepthook__(t, v, tb)

    def thread_hook(a):
        log.critical("Unhandled exception in thread %s", a.thread.name if a.thread else "?",
                     exc_info=(a.exc_type, a.exc_value, a.exc_traceback))

    sys.excepthook, threading.excepthook = main_hook, thread_hook
    log.info("---- start (data folder: %s)", paths.data_dir())
    return log
