"""SQLite veri katmanı — kullanıcılar, oturumlar, abonelikler, ödemeler, bağlantılar, kullanım sayaçları.

Harici bağımlılık gerektirmez (standart kütüphane sqlite3). Her çağrıda kısa ömürlü
bağlantı açılır; WAL modu sayesinde eşzamanlı okuma/yazma sorunsuz çalışır.
"""
import json
import logging
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Optional

_logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "app.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    full_name TEXT DEFAULT '',
    company TEXT DEFAULT '',
    created_at REAL NOT NULL,
    is_demo INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS subscriptions (
    id TEXT PRIMARY KEY,
    user_id TEXT UNIQUE NOT NULL REFERENCES users(id),
    plan TEXT NOT NULL DEFAULT 'free',
    status TEXT NOT NULL DEFAULT 'active',
    started_at REAL NOT NULL,
    renews_at REAL,
    canceled_at REAL
);
CREATE TABLE IF NOT EXISTS payments (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    plan TEXT NOT NULL,
    amount REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'TRY',
    card_last4 TEXT DEFAULT '',
    provider TEXT NOT NULL DEFAULT 'demo',
    status TEXT NOT NULL DEFAULT 'paid',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS connections (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    platform TEXT NOT NULL,
    label TEXT DEFAULT '',
    credentials TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'unverified',
    created_at REAL NOT NULL,
    last_check REAL
);
CREATE TABLE IF NOT EXISTS usage (
    user_id TEXT NOT NULL,
    period TEXT NOT NULL,
    metric TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, period, metric)
);
CREATE TABLE IF NOT EXISTS email_verification_codes (
    user_id TEXT PRIMARY KEY REFERENCES users(id),
    code TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0
);
"""


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    return c


def init_db() -> None:
    with _conn() as c:
        c.executescript(SCHEMA)
        cols = {r[1] for r in c.execute("PRAGMA table_info(users)").fetchall()}
        if "is_demo" not in cols:
            c.execute("ALTER TABLE users ADD COLUMN is_demo INTEGER DEFAULT 0")
        if "email_verified_at" not in cols:
            c.execute("ALTER TABLE users ADD COLUMN email_verified_at REAL")
            c.execute("UPDATE users SET email_verified_at = created_at WHERE email_verified_at IS NULL")
        # Var olan verification_codes tablosuna attempts kolonu ekle
        vcols = {r[1] for r in c.execute("PRAGMA table_info(email_verification_codes)").fetchall()}
        if "attempts" not in vcols:
            c.execute("ALTER TABLE email_verification_codes ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")


def delete_user_cascade(user_id: str) -> None:
    """Kullanıcıya bağlı tüm kayıtları siler."""
    with _conn() as c:
        c.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        c.execute("DELETE FROM email_verification_codes WHERE user_id=?", (user_id,))
        c.execute("DELETE FROM usage WHERE user_id=?", (user_id,))
        c.execute("DELETE FROM connections WHERE user_id=?", (user_id,))
        c.execute("DELETE FROM payments WHERE user_id=?", (user_id,))
        c.execute("DELETE FROM subscriptions WHERE user_id=?", (user_id,))
        c.execute("DELETE FROM users WHERE id=?", (user_id,))


def cleanup_expired_demos() -> int:
    """Süresi dolmuş (7 günden eski) demo hesapları siler."""
    import shutil

    cutoff = now() - 7 * 24 * 3600
    rows = query("SELECT id FROM users WHERE is_demo=1 AND created_at < ?", (cutoff,))
    users_root = DATA_DIR / "users"
    for row in rows:
        uid = row["id"]
        delete_user_cascade(uid)
        user_dir = users_root / uid
        if user_dir.is_dir():
            shutil.rmtree(user_dir, ignore_errors=True)
    return len(rows)


def query(sql: str, params: tuple = ()) -> list[dict]:
    with _conn() as c:
        rows = c.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def query_one(sql: str, params: tuple = ()) -> Optional[dict]:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: tuple = ()) -> None:
    with _conn() as c:
        c.execute(sql, params)


def new_id() -> str:
    return uuid.uuid4().hex


def now() -> float:
    return time.time()


# ---- Kullanım sayaçları (plan limitleri için) ----

def current_period() -> str:
    return time.strftime("%Y-%m")


def get_usage(user_id: str, metric: str) -> int:
    row = query_one(
        "SELECT count FROM usage WHERE user_id=? AND period=? AND metric=?",
        (user_id, current_period(), metric),
    )
    return row["count"] if row else 0


def increment_usage(user_id: str, metric: str, amount: int = 1) -> int:
    with _conn() as c:
        c.execute(
            "INSERT INTO usage (user_id, period, metric, count) VALUES (?,?,?,?) "
            "ON CONFLICT(user_id, period, metric) DO UPDATE SET count = count + ?",
            (user_id, current_period(), metric, amount, amount),
        )
        row = c.execute(
            "SELECT count FROM usage WHERE user_id=? AND period=? AND metric=?",
            (user_id, current_period(), metric),
        ).fetchone()
        return row["count"]


def check_and_increment_usage(user_id: str, metric: str, limit: int, amount: int = 1) -> tuple:
    """Atomik kontrol+artır. (geçti, yeni_sayı) döndürür."""
    period = current_period()
    with _conn() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute(
            "SELECT count FROM usage WHERE user_id=? AND period=? AND metric=?",
            (user_id, period, metric),
        ).fetchone()
        current = row["count"] if row else 0
        if current + amount > limit:
            c.execute("ROLLBACK")
            return False, current
        c.execute(
            "INSERT INTO usage (user_id, period, metric, count) VALUES (?,?,?,?) "
            "ON CONFLICT(user_id, period, metric) DO UPDATE SET count = count + ?",
            (user_id, period, metric, amount, amount),
        )
        new_count = current + amount
    return True, new_count


def decrement_usage(user_id: str, metric: str, amount: int = 1) -> int:
    """Kullanım sayacını düşürür (başarısız işlem geri alımı)."""
    period = current_period()
    with _conn() as c:
        row = c.execute(
            "SELECT count FROM usage WHERE user_id=? AND period=? AND metric=?",
            (user_id, period, metric),
        ).fetchone()
        if not row:
            return 0
        new_count = max(0, row["count"] - amount)
        c.execute(
            "UPDATE usage SET count=? WHERE user_id=? AND period=? AND metric=?",
            (new_count, user_id, period, metric),
        )
        return new_count


def usage_summary(user_id: str) -> dict:
    rows = query(
        "SELECT metric, count FROM usage WHERE user_id=? AND period=?",
        (user_id, current_period()),
    )
    return {r["metric"]: r["count"] for r in rows}


# ---- Bağlantı yardımcıları ----

def connection_credentials(row: dict) -> dict[str, Any]:
    try:
        return json.loads(row.get("credentials") or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}


init_db()
