from __future__ import annotations

import sqlite3
import time

from models import UserFilter

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    chat_id INTEGER PRIMARY KEY,
    active INTEGER NOT NULL DEFAULT 1,
    collections TEXT NOT NULL DEFAULT '',
    max_num INTEGER,
    max_rarity INTEGER,
    backdrops TEXT NOT NULL DEFAULT '',
    only_original INTEGER NOT NULL DEFAULT 0,
    sub_until INTEGER NOT NULL DEFAULT 0,
    expiry_notified INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS promos (
    code TEXT PRIMARY KEY,
    seconds INTEGER NOT NULL,
    max_uses INTEGER NOT NULL DEFAULT 0,
    used INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS promo_uses (
    code TEXT NOT NULL,
    chat_id INTEGER NOT NULL,
    used_at INTEGER NOT NULL,
    PRIMARY KEY (code, chat_id)
);
CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    method TEXT NOT NULL,
    plan TEXT NOT NULL,
    amount TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    charge_id TEXT UNIQUE,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS positions (
    collection TEXT PRIMARY KEY,
    last_num INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

_USER_FIELDS = {"active", "collections", "max_num", "max_rarity", "backdrops", "only_original"}


class Database:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """Старые базы без колонки backdrops — добавляем её."""
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(users)")}
        if "backdrops" not in cols:
            self.conn.execute("ALTER TABLE users ADD COLUMN backdrops TEXT NOT NULL DEFAULT ''")
        for col in ("sub_until", "expiry_notified"):
            if col not in cols:
                self.conn.execute(f"ALTER TABLE users ADD COLUMN {col} INTEGER NOT NULL DEFAULT 0")
        if "only_original" not in cols:
            self.conn.execute("ALTER TABLE users ADD COLUMN only_original INTEGER NOT NULL DEFAULT 0")

    # ---------- users ----------
    @staticmethod
    def _to_filter(r: sqlite3.Row) -> UserFilter:
        cols = [c for c in r["collections"].split(",") if c.strip()]
        return UserFilter(
            chat_id=r["chat_id"],
            active=bool(r["active"]),
            collections=cols,
            max_num=r["max_num"],
            max_rarity=r["max_rarity"],
            backdrops=[b for b in r["backdrops"].split(",") if b.strip()],
            only_original=bool(r["only_original"]),
            sub_until=r["sub_until"],
        )

    def subscribe(self, chat_id: int) -> UserFilter:
        self.conn.execute(
            "INSERT INTO users(chat_id, active) VALUES(?, 1) "
            "ON CONFLICT(chat_id) DO UPDATE SET active=1",
            (chat_id,),
        )
        self.conn.commit()
        return self.get_user(chat_id)  # type: ignore[return-value]

    def get_user(self, chat_id: int) -> UserFilter | None:
        r = self.conn.execute("SELECT * FROM users WHERE chat_id=?", (chat_id,)).fetchone()
        return self._to_filter(r) if r else None

    def update_user(self, chat_id: int, **fields) -> None:
        bad = set(fields) - _USER_FIELDS
        if bad:
            raise ValueError(f"unknown fields: {bad}")
        for key in ("collections", "backdrops"):
            if key in fields and isinstance(fields[key], list):
                fields[key] = ",".join(fields[key])
        for key in ("active", "only_original"):
            if key in fields:
                fields[key] = int(bool(fields[key]))
        sets = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE users SET {sets} WHERE chat_id=?", (*fields.values(), chat_id))
        self.conn.commit()

    def reset_filters(self, chat_id: int) -> None:
        self.conn.execute(
            "UPDATE users SET collections='', max_num=NULL, max_rarity=NULL, backdrops='', only_original=0 WHERE chat_id=?",
            (chat_id,),
        )
        self.conn.commit()

    def active_users(self) -> list[UserFilter]:
        rows = self.conn.execute("SELECT * FROM users WHERE active=1").fetchall()
        return [self._to_filter(r) for r in rows]

    # ---------- подписки и платежи ----------
    def extend_sub(self, chat_id: int, seconds: int, now: int | None = None) -> int:
        """Продлить подписку (от текущего конца, если она ещё идёт). Возвращает новое время окончания."""
        now = int(now if now is not None else time.time())
        self.conn.execute("INSERT OR IGNORE INTO users(chat_id) VALUES(?)", (chat_id,))
        cur = self.conn.execute("SELECT sub_until FROM users WHERE chat_id=?", (chat_id,)).fetchone()["sub_until"]
        until = max(cur, now) + int(seconds)
        self.conn.execute(
            "UPDATE users SET sub_until=?, expiry_notified=0 WHERE chat_id=?", (until, chat_id)
        )
        self.conn.commit()
        return until

    def take_expired(self, now: int | None = None) -> list[int]:
        """Пользователи, у которых подписка закончилась, а уведомления об этом ещё не было."""
        now = int(now if now is not None else time.time())
        rows = self.conn.execute(
            "SELECT chat_id FROM users WHERE sub_until>0 AND sub_until<=? AND expiry_notified=0", (now,)
        ).fetchall()
        ids = [r["chat_id"] for r in rows]
        if ids:
            self.conn.executemany("UPDATE users SET expiry_notified=1 WHERE chat_id=?", [(i,) for i in ids])
            self.conn.commit()
        return ids

    def add_payment(self, chat_id: int, method: str, plan: str, amount: str, status: str,
                    charge_id: str | None = None) -> int | None:
        """Записать платёж. None — если платёж с таким charge_id уже есть."""
        try:
            cur = self.conn.execute(
                "INSERT INTO payments(chat_id, method, plan, amount, status, charge_id, created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (chat_id, method, plan, amount, status, charge_id, int(time.time())),
            )
        except sqlite3.IntegrityError:
            return None
        self.conn.commit()
        return cur.lastrowid

    def get_payment(self, pid: int):
        return self.conn.execute("SELECT * FROM payments WHERE id=?", (pid,)).fetchone()

    def resolve_payment(self, pid: int, status: str) -> bool:
        """Перевести заявку из pending в ok/rejected. False, если её уже обработали."""
        cur = self.conn.execute(
            "UPDATE payments SET status=? WHERE id=? AND status='pending'", (status, pid)
        )
        self.conn.commit()
        return cur.rowcount == 1

    def pending_count(self, chat_id: int) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) AS n FROM payments WHERE chat_id=? AND status='pending'", (chat_id,)
        ).fetchone()["n"]

    # ---------- промокоды ----------
    def add_promo(self, code: str, seconds: int, max_uses: int = 0) -> bool:
        """Создать промокод. False, если такой код уже есть. max_uses=0 — без лимита."""
        try:
            self.conn.execute(
                "INSERT INTO promos(code, seconds, max_uses, created_at) VALUES(?,?,?,?)",
                (code.upper(), int(seconds), int(max_uses), int(time.time())),
            )
        except sqlite3.IntegrityError:
            return False
        self.conn.commit()
        return True

    def del_promo(self, code: str) -> bool:
        cur = self.conn.execute("DELETE FROM promos WHERE code=?", (code.upper(),))
        self.conn.commit()
        return cur.rowcount == 1

    def list_promos(self):
        return self.conn.execute("SELECT * FROM promos ORDER BY created_at").fetchall()

    def redeem_promo(self, code: str, chat_id: int) -> tuple[str, int]:
        """Погасить промокод. Статус: ok / nope (нет такого) / used (уже брал) / limit (исчерпан)."""
        code = code.strip().upper()
        row = self.conn.execute("SELECT * FROM promos WHERE code=?", (code,)).fetchone()
        if row is None:
            return "nope", 0
        if self.conn.execute(
            "SELECT 1 FROM promo_uses WHERE code=? AND chat_id=?", (code, chat_id)
        ).fetchone():
            return "used", 0
        if row["max_uses"] and row["used"] >= row["max_uses"]:
            return "limit", 0
        self.conn.execute(
            "INSERT INTO promo_uses(code, chat_id, used_at) VALUES(?,?,?)", (code, chat_id, int(time.time()))
        )
        self.conn.execute("UPDATE promos SET used=used+1 WHERE code=?", (code,))
        self.conn.commit()
        return "ok", row["seconds"]

    # ---------- positions ----------
    def all_positions(self) -> dict[str, int]:
        rows = self.conn.execute("SELECT collection, last_num FROM positions").fetchall()
        return {r["collection"]: r["last_num"] for r in rows}

    def set_pos(self, collection: str, num: int) -> None:
        self.conn.execute(
            "INSERT INTO positions(collection, last_num) VALUES(?, ?) "
            "ON CONFLICT(collection) DO UPDATE SET last_num=excluded.last_num",
            (collection, num),
        )
        self.conn.commit()

    # ---------- meta ----------
    def get_meta(self, key: str) -> str | None:
        r = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r["value"] if r else None

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.conn.commit()
