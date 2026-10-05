"""SQLite wrapper. All tables live here (previously they were created in three different places)."""
import contextlib
import json
import sqlite3
import threading
from datetime import datetime, timedelta

from .config import DB_PATH, now

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS players(name TEXT PRIMARY KEY, tags TEXT DEFAULT '',
        notes TEXT DEFAULT '', first_seen TEXT, last_seen TEXT, seen INTEGER DEFAULT 0,
        uid INTEGER, verified INTEGER, colours TEXT DEFAULT '', pinned INTEGER DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS chat(id INTEGER PRIMARY KEY, player TEXT, text TEXT, t TEXT,
        UNIQUE(player, text))""",
    """CREATE TABLE IF NOT EXISTS flags(id INTEGER PRIMARY KEY, chat_id INTEGER, player TEXT,
        cat TEXT, src TEXT, t TEXT, status TEXT DEFAULT 'pending')""",
    "CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, player TEXT, t TEXT, kind TEXT, detail TEXT)",
    "CREATE TABLE IF NOT EXISTS ignored(word TEXT PRIMARY KEY)",
    "CREATE TABLE IF NOT EXISTS looks(id INTEGER PRIMARY KEY, player TEXT, vec BLOB, path TEXT, t TEXT, src TEXT)",
    "CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT)",
    "CREATE TABLE IF NOT EXISTS examples(text TEXT PRIMARY KEY, label TEXT)",
    # shared-watchlist sync: things waiting to be sent, and the local copy of the shared list
    """CREATE TABLE IF NOT EXISTS outbox(id INTEGER PRIMARY KEY, kind TEXT, payload TEXT, created REAL,
        tries INTEGER DEFAULT 0, next_try REAL DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS watchlist(user_id TEXT PRIMARY KEY, username TEXT, usernames TEXT DEFAULT '[]',
        categories TEXT DEFAULT '[]', severity TEXT, review_by REAL, updated_at REAL)""",
)


class Store:
    def __init__(self, path=DB_PATH):
        self.c = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.RLock()
        self._defer = 0                         # >0 while inside batch(): commit once at the end instead of per statement
        try:
            self.c.execute("PRAGMA journal_mode=WAL")      # readers and the writer stop blocking each other
            self.c.execute("PRAGMA synchronous=NORMAL")    # one disk sync per checkpoint, not per statement
        except sqlite3.DatabaseError:
            pass
        for sql in SCHEMA:
            self.q(sql)
        try:                                    # databases made by older versions
            self.q("ALTER TABLE players ADD COLUMN pinned INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass

    def q(self, sql, a=()):
        with self.lock:
            cur = self.c.execute(sql, a)
            if not self._defer:
                self.c.commit()
            return cur.fetchall()

    @contextlib.contextmanager
    def batch(self):
        """Group many writes into one commit (a scan does dozens). Nests; other threads' writes ride along."""
        with self.lock:
            self._defer += 1
        try:
            yield
        finally:
            with self.lock:
                self._defer -= 1
                if not self._defer:
                    self.c.commit()

    def purge(self, days):
        """Delete chat and timeline rows older than `days` (0 = keep forever) plus the flags that hang off them.
        Keeps anything tied to a confirmed flag, and never touches the players table. Returns rows removed."""
        if not days or days <= 0:
            return 0
        cut = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        with self.lock, self.batch():
            keep = "(SELECT chat_id FROM flags WHERE status='confirmed')"
            n = self.scalar(f"SELECT COUNT(*) FROM chat WHERE t<? AND id NOT IN {keep}", (cut,))
            n += self.scalar("SELECT COUNT(*) FROM events WHERE t<?", (cut,))
            self.q(f"DELETE FROM flags WHERE status!='confirmed' AND (t<? OR chat_id IN (SELECT id FROM chat WHERE t<? AND id NOT IN {keep}))", (cut, cut))
            self.q(f"DELETE FROM chat WHERE t<? AND id NOT IN {keep}", (cut,))
            self.q("DELETE FROM events WHERE t<?", (cut,))
            self.q("DELETE FROM outbox WHERE created<?", ((datetime.now() - timedelta(days=days)).timestamp(),))
        return n

    def scalar(self, sql, a=()):
        return self.q(sql, a)[0][0]

    def ensure(self, name):
        self.q("INSERT OR IGNORE INTO players(name,first_seen,last_seen) VALUES(?,?,?)", (name, now(), now()))

    def event(self, name, kind, detail=""):
        self.q("INSERT INTO events(player,t,kind,detail) VALUES(?,?,?,?)", (name, now(), kind, detail))

    # JSON values in the settings table (keys used so far: 'chat' (legacy), 'regions', 'ai_off')
    def get(self, key, default=None):
        r = self.q("SELECT v FROM settings WHERE k=?", (key,))
        return json.loads(r[0][0]) if r else default

    def put(self, key, value):
        self.q("INSERT OR REPLACE INTO settings VALUES(?, ?)", (key, json.dumps(value)))
