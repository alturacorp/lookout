"""Reads in-game NAME PLATES: a name over one or two smaller lines, e.g.

        SGT. R. Emerson            <- the name (largest line)
      Combat - Turn-Based          <- status line(s) under it
          View Player

Such a name is a character / display name, not a Roblox username: it has a rank, initials, dots and spaces, so the username
pattern never matched it, and the lines under it were read as stray "players". This finds the plate by its shape (a name with
smaller, roughly centred lines directly under it), returns the name cleaned up, and reports every line of the plate so the rest of the
scan can ignore the status lines.

Nothing here decides anything about a person. The name is only compared with the in-game names reviewers added for listed players,
and a match is only ever a local 'possible, unverified' notice."""
import re

# Words that mark the smaller lines of a plate. Not needed to find one (shape is enough) but makes a near-miss certain.
STATUS = re.compile(r"\b(view player|combat|turn[- ]?based|real[- ]?time|online|offline|away|busy|afk|on duty|off duty|in combat|"
                    r"idle|typing|speaking|wanted|arrested|downed|injured)\b", re.I)
RANKS = ("SGT", "SSGT", "CPL", "LCPL", "PVT", "PFC", "LT", "CPT", "CAPT", "MAJ", "COL", "GEN", "OFC", "PO", "DET", "INSP", "SUPT", "CHIEF",
         "CMDR", "SPEC", "TRN", "TRAINEE", "REC", "RCT", "AUX", "SENIOR", "SR", "JR", "DR", "MR", "MS", "MRS", "MX")
_RANK = re.compile(r"^(?:%s)\.?\s+" % "|".join(RANKS), re.I)
_JUNK = re.compile(r"[^\w\s.'\-]", re.UNICODE)          # emoji, icons, brackets, stray symbols
_TOKEN = re.compile(r"^[^\W\d_](?:[\w'.\-]*)$", re.UNICODE)


def clean(text):
    """OCR text -> a plain name: icons and symbols removed, spaces collapsed."""
    t = _JUNK.sub(" ", str(text or "").replace("_", " "))
    return re.sub(r"\s+", " ", t).strip(" .-'")


def strip_rank(name):
    """'SGT. R. Emerson' -> ('SGT.', 'R. Emerson'). No rank -> (None, name)."""
    m = _RANK.match(name)
    return (m.group(0).strip(), name[m.end():].strip()) if m and name[m.end():].strip() else (None, name)


def name_shaped(text):
    """Could this line be a person's name? 1-5 word-like tokens, 3-30 characters, mostly letters."""
    t = clean(text)
    if not (3 <= len(t) <= 30):
        return False
    toks = t.split()
    if not (1 <= len(toks) <= 5) or not all(_TOKEN.match(x) for x in toks):
        return False
    letters = sum(ch.isalpha() for ch in t)
    return letters >= 3 and letters / max(1, len(t.replace(" ", ""))) >= 0.7 and any(sum(c.isalpha() for c in x) >= 3 for x in toks)


def _items(res, min_conf):
    out = []
    for b, text, conf in res:
        try:
            if float(conf) < min_conf:
                continue
            xs, ys = [p[0] for p in b], [p[1] for p in b]
        except Exception:
            continue
        x1, y1, x2, y2 = int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))
        if x2 > x1 and y2 > y1 and str(text).strip():
            out.append({"t": str(text).strip(), "x1": x1, "y1": y1, "x2": x2, "y2": y2, "h": y2 - y1})
    return out


def _lines(items):
    """Join words that sit on one line (some OCR engines return each word separately)."""
    rows = []
    for it in sorted(items, key=lambda i: (i["y1"] + i["y2"]) / 2):
        cy = (it["y1"] + it["y2"]) / 2
        for row in rows:
            if abs(cy - row["cy"]) < 0.5 * max(it["h"], row["h"]):
                row["items"].append(it)
                row["h"] = max(row["h"], it["h"])
                break
        else:
            rows.append({"cy": cy, "h": it["h"], "items": [it]})
    lines = []
    for row in rows:
        cur = None
        for it in sorted(row["items"], key=lambda i: i["x1"]):
            if cur and 0 <= it["x1"] - cur["x2"] < 1.5 * max(it["h"], cur["h"]):
                cur["t"] += " " + it["t"]
                cur["x2"], cur["y1"], cur["y2"] = max(cur["x2"], it["x2"]), min(cur["y1"], it["y1"]), max(cur["y2"], it["y2"])
                cur["h"] = cur["y2"] - cur["y1"]
            else:
                cur = dict(it)
                lines.append(cur)
    return lines


def find_plates(res, min_conf=0.5):
    """Name plates in one OCR result (list of (4-point box, text, confidence)).
    Returns [{"name", "rank", "box": (x1,y1,x2,y2) of the name, "lines": [(x1,y1,x2,y2) of every line of the plate], "status": [texts]}]."""
    lines = _lines(_items(res, min_conf))
    used, plates = set(), []
    order = sorted(range(len(lines)), key=lambda k: -lines[k]["h"])          # the biggest line of a plate is its name
    for i in order:
        L = lines[i]
        if i in used or not name_shaped(L["t"]):
            continue
        cx, w = (L["x1"] + L["x2"]) / 2, L["x2"] - L["x1"]
        below, prev = [], L
        for j in sorted((k for k in range(len(lines)) if k != i and k not in used), key=lambda k: lines[k]["y1"]):
            S = lines[j]
            gap = S["y1"] - prev["y2"]
            if gap < -0.2 * S["h"] or gap > 1.2 * L["h"]:
                continue
            if S["y1"] <= L["y1"]:
                continue
            if abs((S["x1"] + S["x2"]) / 2 - cx) > 0.3 * max(w, S["x2"] - S["x1"]) or S["h"] > 0.95 * L["h"]:
                continue
            below.append(S)
            prev = S
            if len(below) == 3:
                break
        if not below:
            continue
        keyword = any(STATUS.search(S["t"]) for S in below)
        bigger = L["h"] >= 1.12 * min(S["h"] for S in below)
        if not (keyword or bigger):
            continue
        name = clean(L["t"])
        rank, _ = strip_rank(name)
        plates.append({"name": name, "rank": rank, "box": (L["x1"], L["y1"], L["x2"], L["y2"]),
                       "lines": [(L["x1"], L["y1"], L["x2"], L["y2"])] + [(S["x1"], S["y1"], S["x2"], S["y2"]) for S in below],
                       "status": [S["t"] for S in below]})
        used.add(i)
        used.update(id_ for id_, ln in enumerate(lines) if any(ln is S for S in below))
    return plates


def match_forms(name):
    """The spellings worth comparing with a reviewer's saved name: the whole thing, and without the rank ('R. Emerson')."""
    rank, rest = strip_rank(name)
    return [name] + ([rest] if rank else [])
