"""Keeps this computer and the shared backend in step.

  outbox     things to send: confirmed flags and sightings. Sent in order; kept and retried if the network is down.
  watchlist  local copy of the shared list (id, name, categories only - no evidence, no reviewer info).
  pull       asks the backend what changed since last time, and which OTHER devices spotted a watched player.

Nothing here decides who is on the list. That is done by human reviewers (the dashboard); this only mirrors it.
A sighting is queued only when a player on the shared list is seen, and the alert is a heads-up to report them in
the game to Roblox, not an invitation to follow or confront anyone.
"""
import json
import logging
import threading
import time

from . import build_info
from .cloud import PERMANENT, CloudError

log = logging.getLogger("tracker")

COOLDOWN = 10 * 60          # matches the backend: one sighting per watched player per 10 minutes
POLL = 30                   # seconds between syncs. Each costs ~1.5 database reads: the free plan allows 50,000 a day in total
HEARTBEAT = 5 * 60
FLAG_MAX_AGE = 14 * 86400   # the backend deletes unreviewed flags after 14 days, so don't resend older ones
OUTBOX_MAX = 500
MAX_PAGES = 20


def source_of(src):
    s = (src or "").lower()
    return "lexicon" if s.startswith("lex") else "ai" if s.startswith("ai") else "manual"


class Sync:
    def __init__(self, db, cloud, device_id, enabled=lambda: True, on_alert=None, on_status=None, clock=time.time):
        self.db, self.cloud, self.device_id = db, cloud, device_id
        self.enabled, self.on_alert, self.on_status, self.clock = enabled, on_alert, on_status, clock
        self.state, self.message, self.last_ok = "off", "Not connected", 0
        self.wake = threading.Event()
        self._spot, self._seen_sightings, self._last_beat, self._fails, self._round = {}, [], 0, 0, 0

    # ------------------------------------------------------------ queueing (called by the app)
    def _enqueue(self, kind, payload):
        self.db.q("INSERT INTO outbox(kind,payload,created) VALUES(?,?,?)", (kind, json.dumps(payload), self.clock()))
        extra = self.outbox_count() - OUTBOX_MAX
        if extra > 0:
            self.db.q("DELETE FROM outbox WHERE id IN (SELECT id FROM outbox ORDER BY id LIMIT ?)", (extra,))

    def queue_flag(self, username, category, source, text, user_id=None, confidence=None):
        p = {"username": username, "category": category, "source": source_of(source), "text": text[:300],
             "deviceId": self.device_id}
        if user_id:
            p["userId"] = str(user_id)
        if confidence is not None:
            p["confidence"] = confidence
        self._enqueue("flag", p)

    def spotted(self, user_id, username=""):
        """A player was just seen. If they're on the shared list: raise the local alert and queue a sighting."""
        row = self.watch_row(user_id)
        if not row:
            return None
        t = self.clock()
        if t - self._spot.get(str(user_id), 0) < COOLDOWN:
            return None
        self._spot[str(user_id)] = t
        self._enqueue("sighting", {"userId": str(user_id), "username": row["username"], "deviceId": self.device_id})
        self._alert(row, "here")
        return row

    # ------------------------------------------------------------ the local copy
    def watch_row(self, user_id):
        r = self.db.q("SELECT user_id,username,categories,severity FROM watchlist WHERE user_id=? AND " + self._LIVE,
                      (str(user_id), self._now_ms()))
        return self._row(r[0]) if r else None

    def watch_rows(self):
        """Everything live on the local copy, A-Z, for the viewer window."""
        r = self.db.q("SELECT user_id,username,categories,severity,review_by FROM watchlist WHERE " + self._LIVE +
                      " ORDER BY username COLLATE NOCASE", (self._now_ms(),))
        return [dict(self._row(x), review_by=x[4]) for x in r]

    @staticmethod
    def _row(r):
        return {"user_id": r[0], "username": r[1], "categories": json.loads(r[2] or "[]"), "severity": r[3]}

    _LIVE = "(review_by IS NULL OR review_by > ?)"      # an entry past its review date no longer counts

    def _now_ms(self):
        return self.clock() * 1000

    def watch_ids(self):
        return {r[0] for r in self.db.q("SELECT user_id FROM watchlist WHERE " + self._LIVE, (self._now_ms(),))}

    def watch_count(self):
        return self.db.scalar("SELECT COUNT(*) FROM watchlist WHERE " + self._LIVE, (self._now_ms(),))

    def outbox_count(self):
        return self.db.scalar("SELECT COUNT(*) FROM outbox")

    def reset_cache(self):
        self.db.q("DELETE FROM watchlist")
        self.db.put("sync_state", {})
        self._seen_sightings = []

    # ------------------------------------------------------------ talking to the backend
    def flush(self):
        """Send queued items oldest-first. Stops at the first problem that retrying later might fix."""
        t = self.clock()
        for oid, kind, payload, created, tries, next_try in self.db.q("SELECT * FROM outbox ORDER BY id"):
            age = t - created
            if (kind == "sighting" and age > COOLDOWN) or (kind == "flag" and age > FLAG_MAX_AGE):
                self.db.q("DELETE FROM outbox WHERE id=?", (oid,))      # too old to be useful / the rules would refuse it
                continue
            if next_try > t:
                continue
            try:
                self.cloud.call("submitFlag" if kind == "flag" else "reportSighting", json.loads(payload))
            except CloudError as e:
                if e.code in PERMANENT:
                    log.warning("dropping a queued %s the backend rejected (%s)", kind, e.code)
                    self.db.q("DELETE FROM outbox WHERE id=?", (oid,))
                    continue
                if e.code in ("unauthenticated", "permission-denied"):
                    raise
                wait = min(3600, 30 * 2 ** min(tries, 7)) if e.code != "resource-exhausted" else 900
                self.db.q("UPDATE outbox SET tries=tries+1, next_try=? WHERE id=?", (t + wait, oid))
                raise
            self.db.q("DELETE FROM outbox WHERE id=?", (oid,))

    def pull(self):
        st = self.db.get("sync_state", {}) or {}
        if st.get("uid") and st["uid"] != self.cloud.uid:
            self.reset_cache()                                       # different account: don't mix lists
            st = {}
        since, s_since, meta = st.get("since", 0), st.get("sightingsSince"), st.get("metaRev")
        beat = did_beat = self.clock() - self._last_beat > HEARTBEAT
        sightings, server_time = [], self.clock() * 1000
        self._round += 1
        out = self.cloud.call("syncWatchlist", {
            "deviceId": self.device_id, "since": since, "sightingsSince": s_since, "metaRev": meta,
            "wantSightings": self._round % 2 == 1 or self.clock() - max(self._spot.values(), default=0) < 120,
            "heartbeat": beat, "appVersion": build_info.VERSION})
        server_time = out.get("serverTime", server_time)
        if not out.get("unchanged"):
            self._apply(out.get("players", []), out.get("fullList", False))
            meta = out.get("metaRev", meta)
        since = out.get("nextSince", since)
        s_since = out.get("sightingsNext", s_since)
        sightings += out.get("sightings", [])
        if did_beat:
            self._last_beat = self.clock()
        self.db.put("sync_state", {"since": since, "sightingsSince": s_since, "metaRev": meta, "uid": self.cloud.uid})
        self._alerts_from_others(sightings, server_time)

    def _apply(self, players, full=False):
        if full:     # the backend sent the whole list: anyone not on it any more is dropped
            keep = {str(p["userId"]) for p in players if p.get("status") == "watch"}
            for uid in self.watch_ids() - keep:
                self.db.q("DELETE FROM watchlist WHERE user_id=?", (uid,))
        for p in players:
            uid = str(p["userId"])
            old = self.db.q("SELECT updated_at FROM watchlist WHERE user_id=?", (uid,))
            if old and old[0][0] is not None and p.get("updatedAt", 0) < old[0][0]:
                continue                                              # an older copy of something we already have
            if p.get("status") == "watch":
                self.db.q("INSERT OR REPLACE INTO watchlist VALUES(?,?,?,?,?,?,?)", (
                    uid, p.get("username", ""), json.dumps(p.get("usernames", [])), json.dumps(p.get("categories", [])),
                    p.get("severity", "normal"), p.get("reviewBy"), p.get("updatedAt", 0)))
            else:
                self.db.q("DELETE FROM watchlist WHERE user_id=?", (uid,))

    def _alerts_from_others(self, sightings, server_time):
        for s in sightings:
            if s["id"] in self._seen_sightings:
                continue
            self._seen_sightings = (self._seen_sightings + [s["id"]])[-200:]
            if server_time - s.get("at", 0) > COOLDOWN * 1000:
                continue                                              # old news
            row = self.watch_row(s["userId"])
            if row:
                self._alert(row, "another device")

    def _alert(self, row, where):
        if self.on_alert:
            try:
                self.on_alert(row, where)
            except Exception:
                log.exception("alert callback failed")

    # ------------------------------------------------------------ one round, and the loop
    def _set(self, state, msg):
        self.state, self.message = state, msg
        if state == "ok":
            self.last_ok = self.clock()
        if self.on_status:
            try:
                self.on_status(state, msg)
            except Exception:
                log.exception("status callback failed")

    def tick(self):
        """One sync round. Never raises: problems become a status the UI can show."""
        if not self.enabled():
            return self._set("off", "Sharing is off")
        if not self.cloud.ready:
            return self._set("off", "This build isn't connected to a backend")
        if not self.cloud.signed_in:
            return self._set("auth", "Not signed in")
        try:
            self.flush()
            self.pull()
        except CloudError as e:
            self._fails += 1
            if e.code == "unauthenticated":
                return self._set("auth", "Sign in again")
            if e.code == "permission-denied":
                return self._set("denied", "Signed in, but an admin hasn't approved this account yet")
            return self._set("offline", f"Will retry ({e})")
        except Exception:
            self._fails += 1
            log.exception("sync round failed")
            return self._set("offline", "Sync error (see log)")
        self._fails = 0
        self._set("ok", f"Up to date · {self.watch_count()} on the list · {self.outbox_count()} waiting to send")

    def delay(self):
        if self.state in ("auth", "denied", "off"):
            return 60
        return min(300, POLL * 2 ** min(self._fails, 4)) if self._fails else POLL

    def run(self, stop):
        while not stop.is_set():
            self.tick()
            self.wake.wait(self.delay())
            self.wake.clear()
