"""The chat database: one SQLite file plus a photo folder in <config>/household_chat.

Every method here blocks: call it in the executor. Message text is never logged.
"""
from __future__ import annotations

import os
import secrets
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  conv TEXT NOT NULL,
  sender TEXT NOT NULL,
  ts REAL NOT NULL,
  kind TEXT NOT NULL DEFAULT 'text',
  text TEXT NOT NULL DEFAULT '',
  photo TEXT,
  deleted INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS messages_conv ON messages(conv, id);
CREATE TABLE IF NOT EXISTS marks(
  conv TEXT NOT NULL,
  user TEXT NOT NULL,
  delivered INTEGER NOT NULL DEFAULT 0,
  read INTEGER NOT NULL DEFAULT 0,
  cleared INTEGER NOT NULL DEFAULT 0,
  muted INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(conv, user)
);
CREATE TABLE IF NOT EXISTS reactions(
  msg INTEGER NOT NULL,
  user TEXT NOT NULL,
  emoji TEXT NOT NULL,
  PRIMARY KEY(msg, user)
);
CREATE TABLE IF NOT EXISTS hidden(
  user TEXT NOT NULL,
  msg INTEGER NOT NULL,
  PRIMARY KEY(user, msg)
);
CREATE TABLE IF NOT EXISTS people(
  user TEXT PRIMARY KEY,
  hide_online INTEGER NOT NULL DEFAULT 0,
  last_seen REAL
);
CREATE TABLE IF NOT EXISTS media(
  id TEXT PRIMARY KEY,
  conv TEXT NOT NULL,
  owner TEXT NOT NULL,
  mime TEXT NOT NULL,
  ts REAL NOT NULL,
  msg INTEGER
);
"""

EDIT_WINDOW = 15 * 60   # seconds in which a sender can still edit a message, as in WhatsApp
REACTIONS = ("\U0001F44D", "\u2764\ufe0f", "\U0001F602", "\U0001F62E", "\U0001F622", "\U0001F64F")
MIME_EXT = {"image/jpeg": "jpg", "image/png": "png", "image/gif": "gif", "image/webp": "webp"}
UNSENT_PHOTO_DAYS = 1


def sniff(data: bytes) -> str | None:
    """The picture type from the first bytes, or None when it is not a picture we accept."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _msg(row) -> dict:
    return {"id": row["id"], "conv": row["conv"], "sender": row["sender"], "ts": row["ts"], "kind": row["kind"],
            "text": row["text"], "photo": row["photo"], "deleted": bool(row["deleted"]), "reply_to": row["reply_to"],
            "edited": row["edited"], "reactions": {}}


def _snippet(row) -> dict | None:
    """What a reply shows of the message it answers."""
    if row is None:
        return None
    return {"id": row["id"], "sender": row["sender"], "text": (row["text"] or "")[:160], "photo": bool(row["photo"]),
            "deleted": bool(row["deleted"]), "kind": row["kind"]}


class ChatStore:
    def __init__(self, folder: str):
        self.folder = folder
        self.db_path = os.path.join(folder, "chat.db")
        self.media_dir = os.path.join(folder, "media")
        self._lock = threading.Lock()

    # ---- plumbing
    def _run(self, fn):
        with self._lock:
            db = sqlite3.connect(self.db_path, timeout=15)
            db.row_factory = sqlite3.Row
            try:
                out = fn(db)
                db.commit()
                return out
            finally:
                db.close()

    def media_path(self, media_id: str, mime: str) -> str:
        return os.path.join(self.media_dir, "%s.%s" % (media_id, MIME_EXT[mime]))

    def _unlink(self, media_id: str, mime: str) -> None:
        try:
            os.remove(self.media_path(media_id, mime))
        except (OSError, KeyError):
            pass

    @staticmethod
    def _quote_hidden(db, conv: str, reply_to: int, reader: str | None) -> bool:
        """True when the quoted message must not be shown: for a reader, from before their join or clear mark or deleted for
        them; for a message sent to every member, from before anyone's mark (review 2026-11-15)."""
        if reader:
            m = db.execute("SELECT cleared FROM marks WHERE conv = ? AND user = ?", (conv, reader)).fetchone()
            if m and reply_to <= m["cleared"]:
                return True
            return db.execute("SELECT 1 FROM hidden WHERE user = ? AND msg = ?", (reader, reply_to)).fetchone() is not None
        top = db.execute("SELECT COALESCE(MAX(cleared), 0) FROM marks WHERE conv = ?", (conv,)).fetchone()[0]
        return reply_to <= top

    @staticmethod
    def _dress(db, rows, reader: str | None = None) -> list[dict]:
        out = [_msg(r) for r in rows]
        ids = [m["id"] for m in out]
        if ids:
            marks = ",".join("?" * len(ids))
            for r in db.execute("SELECT msg, user, emoji FROM reactions WHERE msg IN (%s)" % marks, ids):
                next(m for m in out if m["id"] == r["msg"])["reactions"][r["user"]] = r["emoji"]
            for m in out:
                if m["reply_to"]:
                    if ChatStore._quote_hidden(db, m["conv"], m["reply_to"], reader):
                        m["reply"] = {"id": m["reply_to"], "sender": None, "text": "", "photo": False, "deleted": False,
                                      "kind": "text", "unavailable": True}
                    else:
                        m["reply"] = _snippet(db.execute("SELECT * FROM messages WHERE id = ?", (m["reply_to"],)).fetchone())
        return out

    @staticmethod
    def _top(db, conv: str) -> int:
        return db.execute("SELECT COALESCE(MAX(id), 0) FROM messages WHERE conv = ?", (conv,)).fetchone()[0]

    @classmethod
    def _ensure(cls, db, conv: str, user: str) -> None:
        """Creates this user's marks row when it is missing. In a group the row starts at the newest message, so
        whatever path creates it first (opening, delivered ticks, mute, ...), a new member never reads the past."""
        cls._new_mark(db, conv, user, cls._top(db, conv) if conv.startswith("g:") else 0)

    @staticmethod
    def _new_mark(db, conv: str, user: str, cleared: int) -> None:
        """Review round 24 #5: a marks row made now gets a join number from an AUTOINCREMENT table, so a number is never
        reused, not even after the row with the highest number was pruned."""
        if db.execute("SELECT 1 FROM marks WHERE conv = ? AND user = ?", (conv, user)).fetchone():
            return
        joined = db.execute("INSERT INTO joins(conv, user) VALUES(?, ?)", (conv, user)).lastrowid
        db.execute("INSERT OR IGNORE INTO marks(conv, user, cleared, joined) VALUES(?, ?, ?, ?)", (conv, user, cleared, joined))

    def setup(self) -> None:
        os.makedirs(self.media_dir, exist_ok=True)

        def go(db):
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)
            cols = {r[1] for r in db.execute("PRAGMA table_info(messages)")}
            if "reply_to" not in cols:
                db.execute("ALTER TABLE messages ADD COLUMN reply_to INTEGER")
            if "edited" not in cols:
                db.execute("ALTER TABLE messages ADD COLUMN edited REAL")
            if "joined" not in {r[1] for r in db.execute("PRAGMA table_info(marks)")}:   # review round 24 #5: a join number
                db.execute("ALTER TABLE marks ADD COLUMN joined INTEGER NOT NULL DEFAULT 0")
            db.execute("CREATE TABLE IF NOT EXISTS joins(id INTEGER PRIMARY KEY AUTOINCREMENT, conv TEXT NOT NULL, "
                       "user TEXT NOT NULL)")
            if "phone_seen" not in {r[1] for r in db.execute("PRAGMA table_info(people)")}:
                db.execute("ALTER TABLE people ADD COLUMN phone_seen REAL")
            # photos that were uploaded but never sent
            old = db.execute("SELECT id, mime FROM media WHERE msg IS NULL AND ts < ?",
                             (time.time() - UNSENT_PHOTO_DAYS * 86400,)).fetchall()
            for row in old:
                self._unlink(row["id"], row["mime"])
                db.execute("DELETE FROM media WHERE id = ?", (row["id"],))
        self._run(go)

    # ---- reading
    def join(self, conv: str, user: str) -> None:
        """First visit of a group: start at the newest message, so a new member does not read the past."""
        self._run(lambda db: self._ensure(db, conv, user))

    def group_epoch(self, conv: str, members) -> str:
        """Review round 24 #5: a short key for "who is in this group since when": each current member with their marks row
        and its join number (from an AUTOINCREMENT table, so never reused: made when they join, removed by prune when they
        leave, so a member added back has a new number; a row from before this column has 0). JARVIS keeps one thread
        per key, so someone newly added never meets the group's earlier turns in JARVIS's memory; the store already hides the
        earlier messages from them (_ensure starts them at the newest)."""
        import hashlib
        members = sorted(set(members or ()))
        def go(db):
            for u in members:
                self._ensure(db, conv, u)
            rows = db.execute("SELECT user, rowid AS r, joined FROM marks WHERE conv = ?", (conv,)).fetchall()
            mine = sorted("%s:%s:%s" % (r["user"], r["r"], r["joined"]) for r in rows if r["user"] in members)
            return hashlib.sha256("|".join(mine).encode("utf-8")).hexdigest()[:12]
        return self._run(go)

    def prune(self, conv: str, keep) -> None:
        """Review round 8: someone who is no longer a member of the group loses their marks row, so when they are added back
        they start at the newest message like any newcomer and never read what was posted while they were out."""
        keep = sorted(set(keep))
        self._run(lambda db: db.execute("DELETE FROM marks WHERE conv = ? AND user NOT IN (%s)" % ",".join("?" * len(keep)),
                                        [conv] + keep) if keep else None)

    def overview(self, user: str, convs: list[str]) -> dict:
        """Per conversation, for this user: the last message they can see, unread count, own marks."""
        def go(db):
            out = {}
            for conv in convs:
                m = db.execute("SELECT delivered, read, cleared, muted FROM marks WHERE conv = ? AND user = ?",
                               (conv, user)).fetchone()
                cleared, read = (m["cleared"], m["read"]) if m else (0, 0)
                last = db.execute("SELECT * FROM messages WHERE conv = ? AND id > ? AND id NOT IN "
                                  "(SELECT msg FROM hidden WHERE user = ?) ORDER BY id DESC LIMIT 1",
                                  (conv, cleared, user)).fetchone()
                unread = db.execute("SELECT COUNT(*) FROM messages WHERE conv = ? AND id > ? AND sender <> ? AND deleted = 0 "
                                    "AND id NOT IN (SELECT msg FROM hidden WHERE user = ?)",
                                    (conv, max(cleared, read), user, user)).fetchone()[0]
                out[conv] = {"last": self._dress(db, [last], user)[0] if last else None, "unread": unread, "read": read,
                             "delivered": m["delivered"] if m else 0, "muted": bool(m and m["muted"])}
            return out
        return self._run(go)

    def direct_convs(self, user: str) -> list[str]:
        """Every one-to-one conversation this user has messages in (also with people who have left)."""
        like = ("d:%" + user + "%")
        return [r[0] for r in self._run(lambda db: db.execute(
            "SELECT DISTINCT conv FROM messages WHERE conv LIKE ?", (like,)).fetchall())]

    def history(self, conv: str, user: str, before: int | None, limit: int) -> dict:
        def go(db):
            m = db.execute("SELECT cleared FROM marks WHERE conv = ? AND user = ?", (conv, user)).fetchone()
            cleared = m["cleared"] if m else 0
            sql, args = ("SELECT * FROM messages WHERE conv = ? AND id > ? AND id NOT IN (SELECT msg FROM hidden WHERE user = ?)",
                         [conv, cleared, user])
            if before:
                sql += " AND id < ?"
                args.append(before)
            rows = db.execute(sql + " ORDER BY id DESC LIMIT ?", args + [limit + 1]).fetchall()
            marks = {r["user"]: {"delivered": r["delivered"], "read": r["read"]}
                     for r in db.execute("SELECT user, delivered, read FROM marks WHERE conv = ?", (conv,))}
            return {"messages": self._dress(db, list(reversed(rows[:limit])), user), "has_more": len(rows) > limit, "marks": marks}
        return self._run(go)

    def muted_users(self, conv: str) -> set[str]:
        return {r[0] for r in self._run(lambda db: db.execute(
            "SELECT user FROM marks WHERE conv = ? AND muted = 1", (conv,)).fetchall())}

    def media(self, media_id: str) -> dict | None:
        row = self._run(lambda db: db.execute("SELECT * FROM media WHERE id = ?", (media_id,)).fetchone())
        return dict(row) if row else None

    # ---- writing
    def add(self, conv: str, sender: str, kind: str, text: str, photo: str | None, members: set[str] | None,
            reply_to: int | None = None) -> dict:
        """Stores a message. members (group only): everyone who belongs to the group now gets it, also when they
        never opened the chat yet."""
        def go(db):
            if members:
                top = self._top(db, conv)
                for u in members:
                    self._new_mark(db, conv, u, top)
            self._ensure(db, conv, sender)      # before the new message, so the sender's row starts below it
            if photo:
                row = db.execute("SELECT owner, conv, msg FROM media WHERE id = ?", (photo,)).fetchone()
                if not row or row["owner"] != sender or row["conv"] != conv or row["msg"] is not None:
                    raise ValueError("photo")
            if reply_to:
                row = db.execute("SELECT conv FROM messages WHERE id = ?", (reply_to,)).fetchone()
                if not row or row["conv"] != conv:
                    raise ValueError("reply")
            ts = time.time()
            mid = db.execute("INSERT INTO messages(conv, sender, ts, kind, text, photo, reply_to) VALUES(?, ?, ?, ?, ?, ?, ?)",
                             (conv, sender, ts, kind, text, photo, reply_to)).lastrowid
            if photo:
                db.execute("UPDATE media SET msg = ? WHERE id = ?", (mid, photo))
            db.execute("UPDATE marks SET delivered = MAX(delivered, ?), read = MAX(read, ?) WHERE conv = ? AND user = ?",
                       (mid, mid, conv, sender))
            return self._dress(db, [db.execute("SELECT * FROM messages WHERE id = ?", (mid,)).fetchone()])[0]
        return self._run(go)

    def mark(self, conv: str, user: str, delivered: int = 0, read: int = 0) -> dict | None:
        """Moves this user's delivered / read marks forward (never back). Returns the new marks when they changed."""
        def go(db):
            top = self._top(db, conv)
            r = min(read, top)
            d = min(max(delivered, r), top)
            self._ensure(db, conv, user)
            before = tuple(db.execute("SELECT delivered, read FROM marks WHERE conv = ? AND user = ?", (conv, user)).fetchone())
            db.execute("UPDATE marks SET delivered = MAX(delivered, ?), read = MAX(read, ?) WHERE conv = ? AND user = ?",
                       (d, r, conv, user))
            after = tuple(db.execute("SELECT delivered, read FROM marks WHERE conv = ? AND user = ?", (conv, user)).fetchone())
            return {"delivered": after[0], "read": after[1]} if after != before else None
        return self._run(go)

    def delete(self, message_id: int, user: str) -> dict | None:
        """Deletes the sender's own message for everyone: the text and photo are removed, a placeholder stays."""
        def go(db):
            row = db.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
            if not row or row["sender"] != user or row["deleted"] or row["kind"] != "text":
                return None
            if row["photo"]:
                m = db.execute("SELECT mime FROM media WHERE id = ?", (row["photo"],)).fetchone()
                if m:
                    self._unlink(row["photo"], m["mime"])
                db.execute("DELETE FROM media WHERE id = ?", (row["photo"],))
            db.execute("UPDATE messages SET deleted = 1, text = '', photo = NULL WHERE id = ?", (message_id,))
            db.execute("DELETE FROM reactions WHERE msg = ?", (message_id,))
            out = _msg(row)
            out.update(deleted=True, text="", photo=None)
            return out
        return self._run(go)

    def readers(self, conv: str, message_id: int, users) -> set:
        """Review round 5: of these members, the ones who can see this message (it is after their join or clear mark and not
        hidden for them). In a group a member with no mark yet has not joined, so cannot."""
        def go(db):
            out = set()
            for u in users:
                m = db.execute("SELECT cleared FROM marks WHERE conv = ? AND user = ?", (conv, u)).fetchone()
                if m is None and conv.startswith("g:"):
                    continue
                if m is not None and message_id <= m["cleared"]:
                    continue
                if db.execute("SELECT 1 FROM hidden WHERE user = ? AND msg = ?", (u, message_id)).fetchone():
                    continue
                out.add(u)
            return out
        return self._run(go)

    def message(self, message_id: int) -> dict | None:
        def go(db):
            row = db.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
            return self._dress(db, [row])[0] if row else None
        return self._run(go)

    def edit(self, message_id: int, user: str, text: str) -> dict | None:
        """The sender changes their own text within EDIT_WINDOW; the message shows 'edited'."""
        def go(db):
            row = db.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
            if (not row or row["sender"] != user or row["deleted"] or row["kind"] != "text"
                    or time.time() - row["ts"] > EDIT_WINDOW or (not text and not row["photo"])):
                return None
            db.execute("UPDATE messages SET text = ?, edited = ? WHERE id = ?", (text, time.time(), message_id))
            return self._dress(db, [db.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()])[0]
        return self._run(go)

    def react(self, message_id: int, user: str, emoji: str | None) -> dict | None:
        """One reaction per person per message; the same again or None takes it away. Returns the message's reactions."""
        def go(db):
            row = db.execute("SELECT deleted, kind FROM messages WHERE id = ?", (message_id,)).fetchone()
            if not row or row["deleted"] or row["kind"] != "text":
                return None
            old = db.execute("SELECT emoji FROM reactions WHERE msg = ? AND user = ?", (message_id, user)).fetchone()
            if emoji is None or (old and old["emoji"] == emoji):
                db.execute("DELETE FROM reactions WHERE msg = ? AND user = ?", (message_id, user))
            else:
                db.execute("INSERT OR REPLACE INTO reactions(msg, user, emoji) VALUES(?, ?, ?)", (message_id, user, emoji))
            return {r["user"]: r["emoji"] for r in db.execute("SELECT user, emoji FROM reactions WHERE msg = ?", (message_id,))}
        return self._run(go)

    def hide(self, message_id: int, user: str) -> None:
        """Delete for me: the message is gone from this person's view only."""
        self._run(lambda db: db.execute("INSERT OR IGNORE INTO hidden(user, msg) VALUES(?, ?)", (user, message_id)))

    def unread_lines(self, conv: str, user: str, limit: int = 5) -> list[dict]:
        """The newest unread messages for a phone notification that stacks like WhatsApp's."""
        def go(db):
            m = db.execute("SELECT read, cleared FROM marks WHERE conv = ? AND user = ?", (conv, user)).fetchone()
            after = max(m["read"], m["cleared"]) if m else 0
            rows = db.execute("SELECT * FROM messages WHERE conv = ? AND id > ? AND sender <> ? AND deleted = 0 "
                              "AND id NOT IN (SELECT msg FROM hidden WHERE user = ?) ORDER BY id DESC LIMIT ?",
                              (conv, after, user, user, limit)).fetchall()
            total = db.execute("SELECT COUNT(*) FROM messages WHERE conv = ? AND id > ? AND sender <> ? AND deleted = 0 "
                               "AND id NOT IN (SELECT msg FROM hidden WHERE user = ?)",
                               (conv, after, user, user)).fetchone()[0]
            return {"lines": [_msg(r) for r in reversed(rows)], "total": total}
        return self._run(go)

    def clear(self, conv: str, user: str) -> int:
        """Empties a chat for this user only; the others keep their copy."""
        def go(db):
            top = self._top(db, conv)
            self._ensure(db, conv, user)
            db.execute("UPDATE marks SET cleared = ?, delivered = MAX(delivered, ?), read = MAX(read, ?) "
                       "WHERE conv = ? AND user = ?", (top, top, top, conv, user))
            return top
        return self._run(go)

    def mute(self, conv: str, user: str, muted: bool) -> None:
        def go(db):
            self._ensure(db, conv, user)
            db.execute("UPDATE marks SET muted = ? WHERE conv = ? AND user = ?", (1 if muted else 0, conv, user))
        self._run(go)

    # ---- online status
    def people_info(self) -> dict:
        return {r["user"]: {"hide_online": bool(r["hide_online"]), "last_seen": r["last_seen"], "phone_seen": r["phone_seen"]}
                for r in self._run(lambda db: db.execute("SELECT * FROM people").fetchall())}

    def set_phone_seen(self, user: str, ts: float) -> None:
        """The last real report of this person's phone, kept over restarts (Home Assistant puts back old phone values)."""
        def go(db):
            db.execute("INSERT OR IGNORE INTO people(user) VALUES(?)", (user,))
            db.execute("UPDATE people SET phone_seen = MAX(COALESCE(phone_seen, 0), ?) WHERE user = ?", (ts, user))
        self._run(go)

    def set_hide_online(self, user: str, hide: bool) -> None:
        def go(db):
            db.execute("INSERT OR IGNORE INTO people(user) VALUES(?)", (user,))
            db.execute("UPDATE people SET hide_online = ? WHERE user = ?", (1 if hide else 0, user))
        self._run(go)

    def set_last_seen(self, user: str, ts: float) -> None:
        def go(db):
            db.execute("INSERT OR IGNORE INTO people(user) VALUES(?)", (user,))
            db.execute("UPDATE people SET last_seen = MAX(COALESCE(last_seen, 0), ?) WHERE user = ?", (ts, user))
        self._run(go)

    def add_media(self, conv: str, owner: str, mime: str, data: bytes) -> str:
        media_id = secrets.token_hex(16)
        path = self.media_path(media_id, mime)
        tmp = path + ".part"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
        self._run(lambda db: db.execute("INSERT INTO media(id, conv, owner, mime, ts) VALUES(?, ?, ?, ?, ?)",
                                        (media_id, conv, owner, mime, time.time())))
        return media_id
