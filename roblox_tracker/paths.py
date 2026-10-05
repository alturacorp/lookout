"""Where the app keeps its files, so it no longer depends on the folder it was launched from.

Windows:  %APPDATA%\\Lookout      macOS: ~/Library/Application Support/Lookout
Linux:    ~/.local/share/Lookout  (override anywhere with the LOOKOUT_HOME environment variable)
The folder used before the app was renamed (RobloxPlayerTracker) is moved here the first time the new version starts.
"""
import os
import shutil
import subprocess
import sys

APP_NAME = "Lookout"       # one place to rename the app's folder
OLD_APP_NAME = "RobloxPlayerTracker"

# What older versions kept in whatever folder they were started from.
LEGACY_FILES = ("roblox_players.db", "chat_model.npz", "lexicon.json")
LEGACY_DIRS = ("captures",)
_CHECKED = ".legacy_checked"


def data_dir():
    env = os.environ.get("LOOKOUT_HOME") or os.environ.get("ROBLOX_TRACKER_HOME")    # second name: older versions
    if env:
        base = env
    else:
        if sys.platform == "win32":
            parent = os.environ.get("APPDATA") or os.path.expanduser("~")
        elif sys.platform == "darwin":
            parent = os.path.expanduser("~/Library/Application Support")
        else:
            parent = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
        base, old = os.path.join(parent, APP_NAME), os.path.join(parent, OLD_APP_NAME)
        if not os.path.exists(base) and os.path.isdir(old):         # first run after the rename: keep everything
            try:
                os.rename(old, base)
            except OSError:
                shutil.copytree(old, base)
    os.makedirs(base, exist_ok=True)
    return base


def data_path(*parts):
    return os.path.join(data_dir(), *parts)


def find_legacy(cwd=None):
    """Names of old-style data sitting in `cwd` that could be imported, or [] if there is nothing to offer."""
    cwd = os.path.abspath(cwd or os.getcwd())
    if cwd == os.path.abspath(data_dir()):
        return []
    if os.path.exists(data_path(_CHECKED)) or os.path.exists(data_path("roblox_players.db")):
        return []
    if not os.path.exists(os.path.join(cwd, "roblox_players.db")):
        return []
    return [n for n in LEGACY_FILES + LEGACY_DIRS if os.path.exists(os.path.join(cwd, n))]


def migrate(cwd, names):
    """Copy (never move) old data into the data folder. The originals are left untouched."""
    cwd = os.path.abspath(cwd)
    for n in names:
        src, dst = os.path.join(cwd, n), data_path(n)
        if os.path.isdir(src):
            shutil.copytree(src, dst, dirs_exist_ok=True)
        elif os.path.isfile(src):
            shutil.copy2(src, dst)
    mark_legacy_checked()
    return names


def mark_legacy_checked():
    with open(data_path(_CHECKED), "w") as f:
        f.write("1")


def open_folder(path):
    if sys.platform == "win32":
        os.startfile(path)                                  # only exists on Windows
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])
