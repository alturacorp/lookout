"""Update checking. The feed is a small JSON file:

    {"version": "1.2.0", "url": "https://.../Lookout-Setup-1.2.0.exe", "sha256": "<64 hex>", "notes": "..."}

The installer is downloaded over HTTPS and its SHA-256 must match the feed before it is run. That protects against a
corrupted or truncated download. It does NOT protect against someone who controls the feed itself (they could publish a
matching hash), so keep the release location locked down and sign the installer with a code-signing certificate.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request

from . import build_info

MAX_BYTES = 1_500_000_000
_VER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)")
_SHA = re.compile(r"^[0-9a-fA-F]{64}$")


def current_version():
    return build_info.VERSION


def feed_url():
    return build_info.UPDATE_FEED_URL


def can_install():
    """Only the packaged Windows build can replace itself; a source checkout just shows the download link."""
    return bool(getattr(sys, "frozen", False)) and sys.platform == "win32"


def parse_version(s):
    m = _VER.match(str(s).strip())
    return tuple(int(x) for x in m.groups()) if m else None


def is_newer(latest, current):
    a, b = parse_version(latest), parse_version(current)
    return a is not None and (b is None or a > b)


def validate(info, allow_http=False):
    """Normalise one feed entry, or return None if it is malformed or unsafe."""
    try:
        version, url, sha = str(info["version"]), str(info["url"]), str(info["sha256"])
    except (KeyError, TypeError):
        return None
    scheme = urllib.parse.urlparse(url).scheme
    if parse_version(version) is None or not _SHA.match(sha) or scheme not in (("https", "http") if allow_http else ("https",)):
        return None
    if not os.path.basename(urllib.parse.urlparse(url).path).lower().endswith(".exe"):
        return None
    return {"version": version.lstrip("v"), "url": url, "sha256": sha.lower(), "notes": str(info.get("notes", ""))[:4000]}


def fetch_feed(url, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": f"Lookout/{current_version()}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read(1_000_000))


def check(url=None, current=None, allow_http=False, timeout=10):
    """The newer release as a validated dict, or None if you are up to date. Raises on network/parse errors."""
    info = validate(fetch_feed(url or feed_url(), timeout), allow_http)
    if info is None:
        raise ValueError("the update feed is malformed")
    return info if is_newer(info["version"], current or current_version()) else None


def download(info, progress=None, dest_dir=None, timeout=30):
    """Download the installer next to a .part file, verify its SHA-256, and return its path. progress(done, total)."""
    name = os.path.basename(urllib.parse.urlparse(info["url"]).path)
    dest = os.path.join(dest_dir or tempfile.mkdtemp(prefix="rpt_update_"), name)
    h, done = hashlib.sha256(), 0
    req = urllib.request.Request(info["url"], headers={"User-Agent": f"Lookout/{current_version()}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r, open(dest + ".part", "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            if total > MAX_BYTES:
                raise ValueError("installer is unexpectedly large")
            while True:
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                done += len(chunk)
                if done > MAX_BYTES:
                    raise ValueError("installer is unexpectedly large")
                h.update(chunk)
                f.write(chunk)
                if progress:
                    progress(done, total)
        if h.hexdigest() != info["sha256"]:
            raise ValueError("the downloaded installer doesn't match its checksum, so it was discarded")
        os.replace(dest + ".part", dest)
    except BaseException:
        try:
            os.remove(dest + ".part")
        except OSError:
            pass
        raise
    return dest


def launch_installer(path):
    """Start the Inno Setup installer silently; it closes this app, upgrades in place and relaunches it."""
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([path, "/SILENT", "/CLOSEAPPLICATIONS", "/RESTARTAPPLICATIONS"], close_fds=True, creationflags=flags)
