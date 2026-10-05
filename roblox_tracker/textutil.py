"""Small text / network / file helpers with no GUI or database dependencies."""
import json
import os
import re
import urllib.request
import zlib

import numpy as np

from .config import N_FEAT


# ---- network / files --------------------------------------------------------------------------------------------
def http(url, data=None, timeout=60):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, body, {"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=timeout).read()


def download(url, path, timeout=20):
    with open(path, "wb") as f:
        f.write(urllib.request.urlopen(url, timeout=timeout).read())


def remove_file(path):
    try:
        os.remove(path)
    except OSError:
        pass


# ---- names -------------------------------------------------------------------------------------------------------
# OCR swaps look-alike characters (S/5/6, O/0, I/l/1 ...). Names are compared by a "skeleton" in which every
# look-alike is the same character, so toes555 / toeS555 / toe6S55 all land on the same player.
_LOOK = str.maketrans({"s": "5", "6": "5", "$": "5", "o": "0", "q": "0", "i": "1", "l": "1", "|": "1", "!": "1",
                       "z": "2", "b": "8", "g": "9"})


def skel(n):
    return n.lower().replace("rn", "m").replace("vv", "w").translate(_LOOK)


_FILE = re.compile(r"^(?:look_)?(.+)_\d+\.jpg$|^thumb_(.+)\.png$")


def file_owner(f):
    """Which player a file in captures/ belongs to (crops, look images, headshots)."""
    m = _FILE.match(f)
    return (m.group(1) or m.group(2)) if m else None


# ---- text fingerprints (for the on-device classifier and few-shot example selection) -------------------------------
def grams(t):
    t = " " + re.sub(r"\s+", " ", t.lower().strip()) + " "
    return {t[i:i + 3] for i in range(len(t) - 2)}


def text_vec(t):
    v = np.zeros(N_FEAT, np.float32)
    for g in grams(t):
        v[zlib.crc32(g.encode()) % N_FEAT] = 1
    return v


def jac(a, b):
    A, B = grams(a), grams(b)
    return len(A & B) / max(1, len(A | B))
