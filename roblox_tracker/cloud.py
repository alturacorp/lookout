"""Talking to the shared backend: Firebase sign-in (REST) and Firestore (REST). No Cloud Functions, so the free plan works.

No Firebase SDK and no service-account credentials: the app only ever holds the signed-in user's own ID token,
and the Firestore security rules decide what that account may do. Passwords are used once to sign in and are never stored;
only the refresh token is kept (system keyring if the optional `keyring` package is installed, else a private file).
"""
import base64
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from . import build_info, paths

log = logging.getLogger("tracker")

TIMEOUT = 20
AUTH_BASE = "https://identitytoolkit.googleapis.com"
TOKEN_BASE = "https://securetoken.googleapis.com"
KEYRING_SERVICE = "RobloxPlayerTracker"     # internal label; kept so people signed in before the rename stay signed in
TOKEN_FILE = "cloud_token.json"

# Backend error codes that retrying will never fix: the queued item is dropped instead of retried forever.
PERMANENT = {"invalid-argument", "failed-precondition", "not-found", "already-exists", "out-of-range"}
APPROVED = ("reporter", "reviewer", "admin")
DAY_MS = 86400 * 1000


class CloudError(Exception):
    def __init__(self, code, message, retryable=False):
        super().__init__(message)
        self.code, self.retryable = code, retryable


def _status_to_code(status):
    return (status or "unknown").lower().replace("_", "-")


def load_config():
    """Project settings: env/`cloud.json` in the data folder (for testing against another project) beat the build's."""
    cfg = {"apiKey": getattr(build_info, "CLOUD_API_KEY", ""), "projectId": getattr(build_info, "CLOUD_PROJECT", ""),
           "firestoreBase": "", "authBase": "", "tokenBase": "", "dashboardUrl": ""}
    try:
        with open(paths.data_path("cloud.json"), encoding="utf-8") as f:
            cfg.update({k: v for k, v in json.load(f).items() if k in cfg and isinstance(v, str)})
    except (OSError, ValueError):
        pass
    return cfg


def configured(cfg):
    return bool(cfg.get("apiKey") and cfg.get("projectId"))


def _post(url, body, headers=None, form=False, timeout=TIMEOUT, method="POST"):
    if body is None:
        data, ctype = None, "application/json"
    elif form:
        data, ctype = urllib.parse.urlencode(body).encode(), "application/x-www-form-urlencoded"
    else:
        data, ctype = json.dumps(body).encode(), "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Content-Type": ctype, "User-Agent": f"Lookout/{build_info.VERSION}", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode("utf-8")).get("error", {})
        except ValueError:
            err = {}
        code = _status_to_code(err.get("status")) if isinstance(err, dict) else "unknown"
        msg = (err.get("message") if isinstance(err, dict) else None) or f"HTTP {e.code}"
        raise CloudError(code, msg, retryable=e.code >= 500 or e.code == 429) from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise CloudError("unavailable", f"can't reach the server ({getattr(e, 'reason', e)})", retryable=True) from None


def jwt_claims(token):
    """Read (not verify) a token's claims, only to show the signed-in role in the UI. The server does the real check."""
    try:
        part = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (IndexError, ValueError):
        return {}


class TokenStore:
    """Keeps the refresh token (never the password)."""

    def __init__(self):
        try:
            import keyring  # optional
            self._kr = keyring
        except Exception:
            self._kr = None

    def load(self):
        if self._kr:
            try:
                raw = self._kr.get_password(KEYRING_SERVICE, "firebase")
                if raw:
                    return json.loads(raw)
            except Exception:
                log.warning("keyring unavailable; using the token file")
                self._kr = None
        try:
            with open(paths.data_path(TOKEN_FILE), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def save(self, data):
        raw = json.dumps(data)
        if self._kr:
            try:
                self._kr.set_password(KEYRING_SERVICE, "firebase", raw)
                self._drop_file()
                return
            except Exception:
                self._kr = None
        path = paths.data_path(TOKEN_FILE)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(raw)

    def clear(self):
        if self._kr:
            try:
                self._kr.delete_password(KEYRING_SERVICE, "firebase")
            except Exception:
                pass
        self._drop_file()

    @staticmethod
    def _drop_file():
        try:
            os.remove(paths.data_path(TOKEN_FILE))
        except OSError:
            pass


class Cloud:
    def __init__(self, cfg=None, tokens=None, clock=time.time):
        self.cfg = cfg or load_config()
        self.tokens = tokens or TokenStore()
        self.clock = clock
        self._id, self._exp, self._refresh, self.email, self.uid, self._role = None, 0, None, "", "", None
        saved = self.tokens.load()
        if saved and saved.get("refresh"):
            self._refresh, self.email, self.uid = saved["refresh"], saved.get("email", ""), saved.get("uid", "")

    # ---- state
    @property
    def ready(self):
        return configured(self.cfg)

    def dashboard_url(self):
        """Where the reviewers' web dashboard lives: cloud.json can override, otherwise Firebase Hosting's default."""
        u = (self.cfg.get("dashboardUrl") or "").strip()
        if not u and self.cfg.get("projectId"):
            u = f"https://{self.cfg['projectId']}.web.app"
        return u if u.startswith("https://") or u.startswith("http://127.0.0.1") else ""

    @property
    def signed_in(self):
        return bool(self._refresh)

    @property
    def role(self):
        """'pending' (waiting for approval), 'reporter', 'reviewer' or 'admin'; '' until it has been looked up."""
        return self._role or ""

    # ---- auth
    def _auth_url(self, path):
        return f"{self.cfg.get('authBase') or AUTH_BASE}{path}?key={urllib.parse.quote(self.cfg['apiKey'])}"

    def _store(self, id_token, refresh, expires_in, email=None, uid=None):
        self._id, self._refresh = id_token, refresh
        self._exp = self.clock() + max(60, int(expires_in) - 60)
        self.email = email or self.email
        self.uid = uid or self.uid
        self.tokens.save({"refresh": refresh, "email": self.email, "uid": self.uid})

    def sign_in(self, email, password):
        if not self.ready:
            raise CloudError("failed-precondition", "This build isn't connected to a backend yet.")
        try:
            r = _post(self._auth_url("/v1/accounts:signInWithPassword"),
                      {"email": email.strip(), "password": password, "returnSecureToken": True})
        except CloudError as e:
            m = str(e)
            if any(k in m for k in ("INVALID_LOGIN", "INVALID_PASSWORD", "EMAIL_NOT_FOUND", "INVALID_EMAIL")):
                raise CloudError("unauthenticated", "Wrong email or password.") from None
            if "USER_DISABLED" in m:
                raise CloudError("permission-denied", "That account has been disabled.") from None
            if "TOO_MANY" in m:
                raise CloudError("resource-exhausted", "Too many attempts. Wait a few minutes and try again.") from None
            raise
        self._role = None
        self._store(r["idToken"], r["refreshToken"], r.get("expiresIn", 3600), r.get("email", email), r.get("localId"))
        try:
            self.refresh_role()
        except CloudError:
            pass                      # the next sync round looks again

    def sign_out(self):
        self._id, self._refresh, self._exp, self._role = None, None, 0, None
        self.tokens.clear()

    def token(self):
        if not self._refresh:
            raise CloudError("unauthenticated", "Not signed in.")
        if self._id and self.clock() < self._exp:
            return self._id
        url = f"{self.cfg.get('tokenBase') or TOKEN_BASE}/v1/token?key={urllib.parse.quote(self.cfg['apiKey'])}"
        try:
            r = _post(url, {"grant_type": "refresh_token", "refresh_token": self._refresh}, form=True)
        except CloudError as e:
            if not e.retryable:
                self.sign_out()   # the refresh token was revoked/expired: ask for a fresh sign-in
                raise CloudError("unauthenticated", "Your sign-in expired. Sign in again.") from None
            raise
        self._store(r["id_token"], r["refresh_token"], r.get("expires_in", 3600), uid=r.get("user_id"))
        return self._id

    # ---- Firestore (REST)
    def _root(self):
        return (self.cfg.get("firestoreBase") or "https://firestore.googleapis.com").rstrip("/") + \
            f"/v1/projects/{self.cfg['projectId']}/databases/(default)/documents"

    def _name(self, path):
        return f"projects/{self.cfg['projectId']}/databases/(default)/documents/{path}"

    def _fs(self, suffix, body, method="POST"):
        return _post(self._root() + suffix, body, method=method, headers={"Authorization": f"Bearer {self.token()}"})

    def _create(self, path, fields, server_time=("createdAt",)):
        """Create a document that must not exist yet. Fields named in `server_time` get the server's commit time,
        which is what the security rules compare against."""
        write = {"update": {"name": self._name(path), "fields": {k: enc(v) for k, v in fields.items()}},
                 "currentDocument": {"exists": False},
                 "updateTransforms": [{"fieldPath": f, "setToServerValue": "REQUEST_TIME"} for f in server_time]}
        self._fs(":commit", {"writes": [write]})

    def _upsert(self, path, fields, server_time=("at",)):
        """Create the document or replace its fields (no 'must not exist' check). Used for delivery receipts."""
        write = {"update": {"name": self._name(path), "fields": {k: enc(v) for k, v in fields.items()}},
                 "updateTransforms": [{"fieldPath": f, "setToServerValue": "REQUEST_TIME"} for f in server_time]}
        self._fs(":commit", {"writes": [write]})

    def _ack(self, d):
        """'This device has received the entry for <userId>.' Only the Roblox id, the account id and the time: no location,
        no device name. Reviewers see a count. One document per account and player, so repeating it just replaces it."""
        uid = str(d["userId"])
        self._upsert(f"receipts/{uid}_{self.uid}", {"userId": uid, "uid": self.uid, "expireAt": Ts(int(self.clock() * 1000) + 30 * DAY_MS)})
        return {"recorded": True}

    def refresh_role(self):
        """Look up this account's role; the first time ever, ask for access (role 'pending') so an admin can approve."""
        path = f"moderators/{self.uid}"
        try:
            doc = self._fs("/" + path, None, method="GET")
            self._role = dec(doc.get("fields", {})).get("role", "pending")
        except CloudError as e:
            if e.code != "not-found":
                raise
            self._create(path, {"email": (self.email or "").lower(), "role": "pending"})
            self._role = "pending"
        return self._role

    def call(self, name, data):
        """The three things the app does against the backend. Raises CloudError(code, message, retryable)."""
        if self._role not in APPROVED:
            self.refresh_role()                    # an admin may have approved it since we last looked
        if self._role not in APPROVED:
            raise CloudError("permission-denied", "This account hasn't been approved yet.")
        try:
            return {"submitFlag": self._submit_flag, "reportSighting": self._report_sighting,
                    "submitEvidence": self._submit_evidence, "syncWatchlist": self._sync, "ackReceipt": self._ack}[name](data)
        except CloudError as e:
            if e.code == "permission-denied" and name != "syncWatchlist":
                # Either the account lost access, or the rules refused this one item (e.g. the player is no longer on
                # the list). Tell them apart: a refused item is dropped, a lost account stops the sync.
                self._role = None
                if self.refresh_role() in APPROVED:
                    raise CloudError("failed-precondition", "The backend's rules refused this item.") from None
            raise

    def _submit_flag(self, d):
        if not d.get("userId"):
            raise CloudError("invalid-argument", "A flag needs a verified Roblox user id.")
        fid = flag_id(d["username"], d["text"])
        f = {"username": d["username"], "userId": str(d["userId"]), "category": d["category"], "source": d["source"],
             "text": d["text"], "deviceId": d["deviceId"], "reporter": self.uid, "status": "pending", "confirmedBy": [],
             "expireAt": Ts(self.clock() * 1000 + 14 * DAY_MS)}
        if d.get("confidence") is not None:
            f["confidence"] = float(d["confidence"])
        if d.get("escalated"):
            f["escalated"] = True
        try:
            self._create(f"flags/{fid}", f, server_time=("createdAt", "updatedAt"))
        except CloudError as e:
            if e.code == "already-exists":
                return {"flagId": fid, "duplicate": True}     # somebody already reported this exact line
            raise
        return {"flagId": fid, "duplicate": False}

    def _report_sighting(self, d):
        now_ms = self.clock() * 1000
        sid = f"{d['userId']}_{int(now_ms // 600000)}"
        try:
            self._create(f"sightings/{sid}", {
                "userId": str(d["userId"]), "username": d.get("username", ""), "deviceId": d["deviceId"],
                "expireAt": Ts(now_ms + 30 * DAY_MS)})
        except CloudError as e:
            if e.code == "already-exists":
                return {"recorded": False, "reason": "cooldown"}
            raise
        return {"recorded": True, "reason": "ok"}

    def _submit_evidence(self, d):
        """A cropped picture the device owner chose to send. The id limits how often one player is reported per device."""
        import base64
        img = base64.b64decode(d["image"])
        if not img or len(img) > 90000:
            raise CloudError("invalid-argument", "The picture is too large to send.")
        now_ms = self.clock() * 1000
        dev = "".join(ch for ch in str(d["deviceId"]) if ch.isalnum())[:8] or "dev"
        eid = f"{d['userId']}_{int(now_ms // 600000)}_{dev}"
        f = {"userId": str(d["userId"]), "username": d["username"], "matchType": d["matchType"],
             "confidence": float(d["confidence"]), "image": Bytes(img), "deviceId": d["deviceId"], "reporter": self.uid,
             "expireAt": Ts(now_ms + 30 * DAY_MS)}
        if d.get("look"):
            from .sync import encode_look
            f["look"] = encode_look(d["look"])                  # one string: matches what a reviewer's reference will be stored as
        try:
            self._create(f"evidence/{eid}", f)
        except CloudError as e:
            if e.code == "already-exists":
                return {"recorded": False, "reason": "already-sent"}
            raise
        return {"recorded": True}

    def _query(self, collection, where=None, order=None, limit=None):
        q = {"from": [{"collectionId": collection}]}
        if where:
            q["where"] = where
        if order:
            q["orderBy"] = [{"field": {"fieldPath": order}, "direction": "ASCENDING"}]
        if limit:
            q["limit"] = limit
        rows = self._fs(":runQuery", {"structuredQuery": q})
        return [dict(id=r["document"]["name"].rsplit("/", 1)[-1], **dec(r["document"].get("fields", {})))
                for r in (rows if isinstance(rows, list) else []) if "document" in r]

    def _sync(self, d):
        """One poll. Costs one read when nothing changed: reviewers touch `meta/watchlist` in every commit that changes
        the list, so the (larger) list is only fetched when that document's time has moved."""
        now_ms = int(self.clock() * 1000)
        try:
            rev = dec(self._fs("/meta/watchlist", None, method="GET").get("fields", {})).get("updatedAt", 0)
        except CloudError as e:
            if e.code != "not-found":
                raise
            rev = 0                                           # nobody has ever been listed
        out = {"serverTime": now_ms, "players": [], "hasMore": False, "nextSince": d.get("since", 0), "metaRev": rev,
               "unchanged": rev == d.get("metaRev") and d.get("metaRev") is not None,
               "sightings": [], "sightingsNext": d.get("sightingsSince") or now_ms}
        if not out["unchanged"]:
            players = self._query("players", {"fieldFilter": {"field": {"fieldPath": "status"}, "op": "EQUAL",
                                                              "value": {"stringValue": "watch"}}}, limit=1000)
            out["fullList"] = True
            out["players"] = [{"userId": p["userId"], "username": p.get("username", ""), "status": "watch",
                               "categories": p.get("categories", []), "severity": p.get("severity", "normal"),
                               "reviewBy": p.get("reviewBy"), "updatedAt": p.get("updatedAt", 0), "looks": p.get("looks", []), "seenAs": p.get("seenAs", [])}
                              for p in players if (p.get("reviewBy") or 0) > now_ms]        # lapsed entries don't count
        s_since = d.get("sightingsSince")
        if s_since is not None and d.get("wantSightings", True):
            rows = self._query("sightings", {"fieldFilter": {"field": {"fieldPath": "createdAt"}, "op": "GREATER_THAN_OR_EQUAL",
                                                             "value": {"timestampValue": iso(s_since)}}}, order="createdAt", limit=100)
            out["sightings"] = [{"id": r["id"], "userId": r["userId"], "username": r.get("username", ""), "at": r.get("createdAt", 0)}
                                for r in rows if r.get("deviceId") != d.get("deviceId")]
            if rows:
                out["sightingsNext"] = rows[-1].get("createdAt", now_ms)
        return out


# ---- Firestore value encoding (just the types this app uses)
class Bytes(bytes):
    """Raw bytes (a small picture), encoded as a Firestore bytes value."""


class Ts(int):
    """A timestamp in milliseconds, so it is encoded as a Firestore timestamp rather than a plain number."""


def iso(ms):
    t = time.gmtime(ms / 1000)
    return time.strftime("%Y-%m-%dT%H:%M:%S", t) + f".{int(ms % 1000):03d}Z"


def parse_iso(s):
    base, _, frac = s.rstrip("Z").partition(".")
    ms = int((frac + "000")[:3]) if frac else 0
    import calendar
    return calendar.timegm(time.strptime(base, "%Y-%m-%dT%H:%M:%S")) * 1000 + ms


def enc(v):
    if isinstance(v, Ts):
        return {"timestampValue": iso(int(v))}
    if isinstance(v, Bytes):
        import base64
        return {"bytesValue": base64.b64encode(bytes(v)).decode()}
    if isinstance(v, bool):
        return {"booleanValue": v}
    if isinstance(v, int):
        return {"integerValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    if isinstance(v, str):
        return {"stringValue": v}
    if isinstance(v, (list, tuple)):
        return {"arrayValue": {"values": [enc(x) for x in v]}}
    if isinstance(v, dict):
        return {"mapValue": {"fields": {k: enc(x) for k, x in v.items()}}}
    if v is None:
        return {"nullValue": None}
    raise TypeError(type(v))


def dec_value(v):
    (kind, val), = v.items()
    if kind == "timestampValue":
        return parse_iso(val)
    if kind == "integerValue":
        return int(val)
    if kind == "arrayValue":
        return [dec_value(x) for x in val.get("values", [])]
    if kind == "mapValue":
        return dec(val.get("fields", {}))
    if kind == "nullValue":
        return None
    return val


def dec(fields):
    return {k: dec_value(v) for k, v in fields.items()}


def flag_id(username, text):
    import hashlib
    return hashlib.sha256(f"{username.lower()}|{text.lower()}".encode()).hexdigest()[:24]
