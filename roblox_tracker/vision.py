"""Finding avatars in a frame and recognising them by outfit."""
import time

import numpy as np
from PIL import Image

from . import config
from .config import CENTRAL_MARGIN, CENTRAL_MATCH, EVIDENCE_MAX_BYTES, GALLERY_MAX, IMG, MARGIN, MATCH, now

BASIC = {"black": (20, 20, 20), "white": (235, 235, 235), "grey": (128, 128, 128),
         "red": (200, 40, 40), "orange": (230, 130, 30), "yellow": (235, 220, 50),
         "green": (50, 160, 60), "blue": (50, 90, 210), "purple": (130, 60, 170),
         "pink": (240, 130, 180), "brown": (110, 70, 40)}


def colours(im):
    pal = im.convert("RGB").resize((24, 24)).quantize(4).convert("RGB").getcolors()
    out = []
    for _, rgb in sorted(pal, reverse=True)[:3]:
        n = min(BASIC, key=lambda k: sum((a - b) ** 2 for a, b in zip(BASIC[k], rgb)))
        if n not in out:
            out.append(n)
    return ", ".join(out)


def embed(im):
    """Outfit fingerprint: hue/greyscale histograms over 4 body bands (head/torso/hips/legs)."""
    im = im.convert("RGB")
    w, h = im.size
    a = np.asarray(im.crop((int(w * .2), 0, int(w * .8), h)).resize((24, 48)).convert("HSV"), dtype=np.float32)
    feats = []
    for band in np.array_split(a, 4, axis=0):
        H, S, V = (band[..., i].ravel() for i in range(3))
        col = (S > 40) & (V > 50)
        hist = np.concatenate([np.histogram(H[col], bins=12, range=(0, 256))[0],
                               np.histogram(V[~col], bins=4, range=(0, 256))[0]]).astype(np.float32)
        feats.append(np.sqrt(hist / max(hist.sum(), 1)))
    v = np.concatenate(feats)
    return (v / (np.linalg.norm(v) + 1e-9)).astype(np.float32)


def crop(frame, b):
    return Image.fromarray(np.ascontiguousarray(frame[b[1]:b[3], b[0]:b[2], ::-1]))


def near(b, p):
    """True if person-box p sits under nametag box b."""
    cx = (b[0] + b[2]) / 2
    return p[0] - 20 <= cx <= p[2] + 20 and b[3] - 60 <= p[1] <= b[3] + 220


def suggest(box, tags):
    """Best-guess username for a drawn box: the nametag just above it."""
    cx, best = (box[0] + box[2]) / 2, None
    for n, b in tags.items():
        d = box[1] - b[3]
        if -40 <= d <= 220 and b[0] - 60 <= cx <= b[2] + 60 and (best is None or abs(d) < best[0]):
            best = (abs(d), n)
    return best[1] if best else ""


class Detector:
    """Optional YOLO person detector. Callable: detector(frame) -> [(x1, y1, x2, y2)]; falls back to [] silently."""

    def __init__(self):
        self.model, self._cache = None, None

    def __call__(self, frame):
        if not config.USE_YOLO or self.model is False:
            return []
        if self._cache and self._cache[0] is frame:
            return self._cache[1]
        try:
            if self.model is None:
                from ultralytics import YOLO
                self.model = YOLO("yolov8n.pt")
            r = self.model(frame, classes=[0], conf=0.25, verbose=False)[0]
            out = [tuple(int(v) for v in b) for b in r.boxes.xyxy.tolist()]
            self._cache = (frame, out)
            return out
        except Exception:
            self.model = False
            return []


def person_box(tag, shape, scale=1.0):
    """Estimated avatar area under a nametag, used when no person detector is available. `scale` is the user's
    "avatar box size" setting, since how big avatars look depends on the camera distance."""
    H, W = shape[:2]
    cx, w = (tag[0] + tag[2]) / 2, max(tag[2] - tag[0], 60)
    half = 1.2 * w * scale
    return (max(0, int(cx - half)), tag[3], min(W, int(cx + half)), min(H, int(tag[3] + 260 * scale)))


def candidates(frame, tags, boxes, scale=1.0):
    """Pair each nametag with the avatar box under it. Returns [(box, name, approx)]: approx is True when the box is
    an estimate (no detector box found). Detected boxes without a nametag come back with name None."""
    used, out = set(), []
    for n, b in tags.items():
        cx, best = (b[0] + b[2]) / 2, None
        for i, (x1, y1, x2, y2) in enumerate(boxes):
            if x1 - 20 <= cx <= x2 + 20 and b[3] - 60 <= y1 <= b[3] + 220 and (best is None or abs(y1 - b[3]) < best[0]):
                best = (abs(y1 - b[3]), i)
        if best:
            used.add(best[1])
            out.append((boxes[best[1]], n, False))
        else:
            out.append((person_box(b, frame.shape, scale), n, True))
    return out + [(bx, None, False) for i, bx in enumerate(boxes) if i not in used]


class Gallery:
    """Taught/learned outfit fingerprints per player, backed by the `looks` table."""

    def __init__(self, db):
        self.db, self.vecs, self.via = db, {}, {}      # via: last look-match similarity per player (for the overlay)
        self.reload()

    def reload(self):
        g = {}
        for p, b in self.db.q("SELECT player,vec FROM looks"):
            g.setdefault(p, []).append(np.frombuffer(b, np.float32))
        self.vecs = {p: np.stack(v) for p, v in g.items()}

    def count(self, name):
        return len(self.vecs.get(name, []))

    def match(self, vec):
        """-> (player or None, best similarity). A player only counts if clearly ahead of the runner-up."""
        s = sorted(((float((m @ vec).max()), p) for p, m in self.vecs.items()), reverse=True)
        if not s:
            return None, 0.0
        ok = s[0][0] >= MATCH and s[0][0] - (s[1][0] if len(s) > 1 else 0) >= MARGIN
        who = s[0][1] if ok else None
        if who:
            self.via[who] = s[0][0]
        return who, s[0][0]

    def add(self, name, im, src):
        path = f"{IMG}/look_{name}_{int(time.time() * 1000)}.jpg"
        im.convert("RGB").save(path)
        self.db.ensure(name)
        self.db.q("INSERT INTO looks(player,vec,path,t,src) VALUES(?,?,?,?,?)", (name, embed(im).tobytes(), path, now(), src))
        if self.db.scalar("SELECT COUNT(*) FROM looks WHERE player=?", (name,)) > GALLERY_MAX:
            self.db.q("DELETE FROM looks WHERE id=(SELECT id FROM looks WHERE player=? AND src='auto' ORDER BY id LIMIT 1)", (name,))
        self.reload()


# ---- the shared list's outfit references, and the pictures sent as evidence
def quantize(vec):
    """A fingerprint as 64 small integers (0-127), the form stored in the shared list."""
    return [int(max(0, min(127, round(float(x) * 127)))) for x in vec]


def dequantize(ints):
    v = np.asarray(ints, np.float32) / 127.0
    return (v / (np.linalg.norm(v) + 1e-9)).astype(np.float32)


class Central:
    """Outfit references from the shared watchlist. A match is only ever 'possible, unverified': it never flags or lists anyone."""

    def __init__(self):
        self.refs = {}                              # user id -> (username, matrix of fingerprints)

    def load(self, rows):
        self.refs = {uid: (name, np.stack([dequantize(v) for v in looks])) for uid, name, looks in rows if looks}

    def match(self, vec):
        """-> (user id or None, best similarity). Needs to clearly beat the runner-up, and clear a stricter bar than your own taught looks."""
        s = sorted(((float((m @ vec).max()), uid) for uid, (_, m) in self.refs.items()), reverse=True)
        if not s:
            return None, 0.0
        ok = s[0][0] >= CENTRAL_MATCH and s[0][0] - (s[1][0] if len(s) > 1 else 0) >= CENTRAL_MARGIN
        return (s[0][1] if ok else None), s[0][0]

    def name(self, uid):
        return self.refs.get(uid, ("", None))[0]


def evidence_jpeg(im, max_side=320, limit=EVIDENCE_MAX_BYTES):
    """Shrink a crop of ONE player until it fits the size limit. Returns JPEG bytes."""
    import io
    im = im.convert("RGB")
    w, h = im.size
    k = min(1.0, max_side / max(w, h, 1))
    if k < 1:
        im = im.resize((max(1, int(w * k)), max(1, int(h * k))))
    for q in (75, 65, 55, 45, 35):
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=q, optimize=True)
        if buf.tell() <= limit:
            return buf.getvalue()
    im = im.resize((max(1, im.width // 2), max(1, im.height // 2)))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=40, optimize=True)
    return buf.getvalue()
