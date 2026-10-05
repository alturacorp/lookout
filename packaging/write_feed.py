"""Writes latest.json, the update feed the app polls, for a freshly built installer.

    python packaging/write_feed.py --version 1.2.3 --installer dist/Lookout-Setup-1.2.3.exe \\
        --url https://github.com/<owner>/<repo>/releases/download/v1.2.3/Lookout-Setup-1.2.3.exe
"""
import argparse
import datetime
import hashlib
import json
import os


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def render(version, url, sha256, notes=""):
    return {"version": version, "url": url, "sha256": sha256, "notes": notes,
            "released": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True)
    ap.add_argument("--installer", required=True)
    ap.add_argument("--url", required=True, help="public https URL the installer will be downloadable from")
    ap.add_argument("--notes", default="")
    ap.add_argument("--out", default=None, help="default: latest.json next to the installer")
    a = ap.parse_args()
    out = a.out or os.path.join(os.path.dirname(os.path.abspath(a.installer)), "latest.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(render(a.version, a.url, sha256_of(a.installer), a.notes), f, indent=2)
    print("wrote", out)


if __name__ == "__main__":
    main()
