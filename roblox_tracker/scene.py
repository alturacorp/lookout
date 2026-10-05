"""Pure decisions about what's on screen (no Tk, no OCR, no database), so they can be tested without a game.

  find_lists      spots the player list / leaderboard: name-like text stacked in a regular column
  ListZones       remembers those lists for a while so a half-hidden one stays ignored
  Candidates      names seen on screen that aren't players yet (the "Spotted" list, and what gets auto-added)
  Ease            smooths boxes between scans so the overlay glides instead of jumping
"""
from statistics import median

# Words that are valid Roblox usernames but are almost always menus, buttons or signs in a game.
STOPWORDS = frozenset("""
settings setting shop store menu play stop start pause resume inventory backpack items item chat players player leaderboard
score scores coins coin cash gems gem money level levels xp rank ranks team teams spawn respawn reset home back next
close open exit quit yes no ok cancel accept decline buy sell trade gift gifts quest quests daily reward rewards claim
help info rules map maps lobby server servers join leave kick ban admin owner mod vip premium robux roblox group
friends friend follow share like favorite emotes emote avatar badges badge stats stat health stamina energy mana
loading please wait ready go winner winners lose lost win game games mode modes round rounds time timer voting vote
""".split())

LIST_PAD_X = (2.8, 1.5)      # row heights of padding left/right of a list's names (avatar icons, scores)
LIST_PAD_Y = (1.0, 2.0)      # rows of padding above/below (lists grow downward as people join)


def width(b): return b[2] - b[0]
def height(b): return b[3] - b[1]


def overlap_frac(b, zone):
    """Share of box b that lies inside zone."""
    w = min(b[2], zone[2]) - max(b[0], zone[0])
    h = min(b[3], zone[3]) - max(b[1], zone[1])
    area = max(1, width(b) * height(b))
    return max(0, w) * max(0, h) / area


def inside_any(b, zones, frac=0.6):
    return any(overlap_frac(b, z) >= frac for z in zones)


def plausible(name):
    n = name.lower().strip("_")
    return len(name) >= 3 and any(c.isalpha() for c in name) and n not in STOPWORDS and name.count("_") <= 3


def _columns(words, edge, tol):
    """Group words whose left (edge=0) or right (edge=2) x are within tol of each other."""
    ws = sorted(words, key=lambda w: w[1][edge])
    groups, cur = [], []
    for w in ws:
        if cur and w[1][edge] - cur[0][1][edge] > tol:
            groups.append(cur)
            cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    return groups


def find_lists(words, min_rows=3):
    """words: [(name, (x1, y1, x2, y2))] of name-like text. Returns rectangles around stacked, evenly spaced columns.

    A list shows 3+ names with the same left (or right) edge and equal spacing. Nametags above avatars don't do that:
    they float at different x and y, so this finds the leaderboard without misreading a crowd.
    """
    if len(words) < min_rows:
        return []
    h = median(height(b) for _, b in words)
    tol = max(6.0, 0.7 * h)
    zones = []
    for edge in (0, 2):
        for g in _columns(words, edge, tol):
            if len(g) < min_rows:
                continue
            g = sorted(g, key=lambda w: w[1][1])
            run = [g[0]]
            for prev, cur in zip(g, g[1:]):
                gap = cur[1][1] - prev[1][1]
                prev_gap = run[-1][1][1] - run[-2][1][1] if len(run) > 1 else None
                ok = 0.9 * h <= gap <= 3.5 * h and (prev_gap is None or 0.7 * prev_gap <= gap <= 1.4 * prev_gap)
                if ok:
                    run.append(cur)
                    continue
                if len(run) >= min_rows:
                    zones.append(_zone(run, h))
                run = [cur]
            if len(run) >= min_rows:
                zones.append(_zone(run, h))
    return _merge(zones)


def _zone(run, h):
    xs1, ys1 = min(b[0] for _, b in run), min(b[1] for _, b in run)
    xs2, ys2 = max(b[2] for _, b in run), max(b[3] for _, b in run)
    gap = (ys2 - ys1) / max(1, len(run) - 1)
    return (xs1 - LIST_PAD_X[0] * h, ys1 - LIST_PAD_Y[0] * gap, xs2 + LIST_PAD_X[1] * h, ys2 + LIST_PAD_Y[1] * gap)


def _merge(zones):
    out = []
    for z in zones:
        for i, o in enumerate(out):
            if overlap_frac(z, o) > 0.3 or overlap_frac(o, z) > 0.3:
                out[i] = (min(z[0], o[0]), min(z[1], o[1]), max(z[2], o[2]), max(z[3], o[3]))
                break
        else:
            out.append(z)
    return out


class ListZones:
    """Lists found recently. A zone stays ignored for `ttl` scans after it was last seen."""

    def __init__(self, ttl=120):
        self.ttl, self.items = ttl, []          # [[rect, last_scan_seen]]

    def update(self, found, scan):
        for z in found:
            for it in self.items:
                if overlap_frac(z, it[0]) > 0.3 or overlap_frac(it[0], z) > 0.3:
                    r = it[0]
                    it[0] = (min(z[0], r[0]), min(z[1], r[1]), max(z[2], r[2]), max(z[3], r[3]))
                    it[1] = scan
                    break
            else:
                self.items.append([z, scan])
        self.items = [it for it in self.items if scan - it[1] <= self.ttl]

    def rects(self):
        return [it[0] for it in self.items]

    def clear(self):
        self.items = []


class Candidates:
    """Name-like words seen in the world that aren't players yet."""

    def __init__(self, keep=60):
        self.keep, self.c = keep, {}

    def seen(self, name, box, scan):
        c = self.c.get(name)
        if c is None:
            self.c[name] = {"n": 1, "scan": scan, "box": box, "first": scan}
        else:
            c["n"] = c["n"] + 1 if scan - c["scan"] <= 3 else 1      # needs to be seen in close succession
            c["scan"], c["box"] = scan, box

    def stable(self, name, need=3):
        c = self.c.get(name)
        return bool(c) and c["n"] >= need

    def forget(self, name):
        self.c.pop(name, None)

    def prune(self, scan):
        self.c = {k: v for k, v in self.c.items() if scan - v["scan"] <= self.keep}

    def listing(self, scan=None, recent=40):
        """Most-seen first. With `scan`, only names seen within the last `recent` scans."""
        rows = [(k, v) for k, v in self.c.items() if scan is None or scan - v["scan"] <= recent]
        return [(k, v["n"]) for k, v in sorted(rows, key=lambda kv: (-kv[1]["n"], kv[0]))]


class Ease:
    """Moves each rectangle part of the way to its target every step, so boxes glide between scans."""

    def __init__(self, alpha=0.4):
        self.alpha, self.cur, self.target = alpha, {}, {}

    def set(self, targets):
        self.target = dict(targets)
        for k in list(self.cur):
            if k not in self.target:
                del self.cur[k]
        for k, t in self.target.items():
            self.cur.setdefault(k, t)               # new things appear where they are, they don't fly in

    def step(self):
        for k, t in self.target.items():
            c = self.cur[k]
            self.cur[k] = tuple(a + (b - a) * self.alpha if abs(b - a) > 0.5 else b for a, b in zip(c, t))
        return dict(self.cur)

    def settled(self):
        return all(self.cur[k] == t for k, t in self.target.items())
