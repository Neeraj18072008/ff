"""Portable persistence layer for The Last Chapter.

Local development:
    SQLite is used when DATABASE_URL is not set.

Render/production:
    PostgreSQL is used when DATABASE_URL is set.
"""
import os
import secrets
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo
from werkzeug.security import generate_password_hash

IST = ZoneInfo("Asia/Kolkata")
DB_PATH = None
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
USE_POSTGRES = bool(DATABASE_URL)


class DBConnection:
    """Small compatibility wrapper so app.py can keep using conn.execute(...)."""

    def __init__(self, raw, postgres=False):
        self.raw = raw
        self.postgres = postgres

    def execute(self, sql, params=()):
        sql = sql or ""
        if self.postgres:
            # Current app/store SQL uses SQLite's '?' placeholders.
            # The project's SQL does not contain literal '?' characters.
            sql = sql.replace("BEGIN IMMEDIATE", "BEGIN")
            sql = sql.replace("INSERT OR IGNORE", "INSERT")
            sql = sql.replace("?", "%s")
            return self.raw.execute(sql, params)
        return self.raw.execute(sql, params)

    def executescript(self, script):
        if self.postgres:
            for statement in script.split(";"):
                statement = statement.strip()
                if statement:
                    self.execute(statement)
            return
        return self.raw.executescript(script)

    def commit(self):
        return self.raw.commit()

    def rollback(self):
        return self.raw.rollback()

    def close(self):
        return self.raw.close()

    def __getattr__(self, name):
        return getattr(self.raw, name)


SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS organizer (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS booking (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pass_code TEXT UNIQUE NOT NULL,
    pass_type TEXT NOT NULL CHECK (pass_type IN ('single','couple')),
    person_1_name TEXT NOT NULL,
    person_1_dob TEXT NOT NULL,
    person_2_name TEXT,
    person_2_dob TEXT,
    contact TEXT NOT NULL,
    payment_method TEXT NOT NULL CHECK (payment_method IN ('cash','upi')),
    payment_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (payment_status IN ('pending','submitted','paid','received','rejected')),
    amount INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT,
    booking_status TEXT NOT NULL DEFAULT 'active'
        CHECK (booking_status IN ('active','cancelled')),
    cancelled_at TEXT,
    cancelled_by TEXT,
    cancellation_reason TEXT,
    entry_status TEXT NOT NULL DEFAULT 'unused'
        CHECK (entry_status IN ('unused','used')),
    entry_time TEXT,
    verified_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_booking_code ON booking(pass_code);
CREATE INDEX IF NOT EXISTS idx_booking_contact ON booking(contact);
CREATE TABLE IF NOT EXISTS pass_code_inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL
        CHECK(length(code)=4 AND code GLOB '[0-9][0-9][0-9][0-9]'),
    status TEXT NOT NULL DEFAULT 'available'
        CHECK(status IN ('available','assigned','used','removed')),
    booking_id INTEGER UNIQUE REFERENCES booking(id),
    created_at TEXT NOT NULL,
    assigned_at TEXT,
    used_at TEXT,
    removed_at TEXT,
    removed_by TEXT,
    removed_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_pass_inventory_status ON pass_code_inventory(status);
CREATE TABLE IF NOT EXISTS activity_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_id INTEGER REFERENCES booking(id) ON DELETE CASCADE,
    action TEXT NOT NULL,
    detail TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_activity_created ON activity_log(created_at);
CREATE TABLE IF NOT EXISTS setting (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

SCHEMA_POSTGRES = """
CREATE TABLE IF NOT EXISTS organizer (
    id BIGSERIAL PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS booking (
    id BIGSERIAL PRIMARY KEY,
    pass_code TEXT UNIQUE NOT NULL,
    pass_type TEXT NOT NULL CHECK (pass_type IN ('single','couple')),
    person_1_name TEXT NOT NULL,
    person_1_dob TEXT NOT NULL,
    person_2_name TEXT,
    person_2_dob TEXT,
    contact TEXT NOT NULL,
    payment_method TEXT NOT NULL CHECK (payment_method IN ('cash','upi')),
    payment_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (payment_status IN ('pending','submitted','paid','received','rejected')),
    amount INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT,
    booking_status TEXT NOT NULL DEFAULT 'active'
        CHECK (booking_status IN ('active','cancelled')),
    cancelled_at TEXT,
    cancelled_by TEXT,
    cancellation_reason TEXT,
    entry_status TEXT NOT NULL DEFAULT 'unused'
        CHECK (entry_status IN ('unused','used')),
    entry_time TEXT,
    verified_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_booking_code ON booking(pass_code);
CREATE INDEX IF NOT EXISTS idx_booking_contact ON booking(contact);
CREATE TABLE IF NOT EXISTS pass_code_inventory (
    id BIGSERIAL PRIMARY KEY,
    code TEXT UNIQUE NOT NULL
        CHECK(code ~ '^[0-9]{4}$'),
    status TEXT NOT NULL DEFAULT 'available'
        CHECK(status IN ('available','assigned','used','removed')),
    booking_id BIGINT UNIQUE REFERENCES booking(id),
    created_at TEXT NOT NULL,
    assigned_at TEXT,
    used_at TEXT,
    removed_at TEXT,
    removed_by TEXT,
    removed_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_pass_inventory_status ON pass_code_inventory(status);
CREATE TABLE IF NOT EXISTS activity_log (
    id BIGSERIAL PRIMARY KEY,
    booking_id BIGINT REFERENCES booking(id) ON DELETE CASCADE,
    action TEXT NOT NULL,
    detail TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_activity_created ON activity_log(created_at);
CREATE TABLE IF NOT EXISTS setting (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

DEFAULT_SETTINGS = {
    "price_single": "499",
    "price_couple": "899",
    "upi_id_single": "yourupi@okaxis",
    "upi_id_couple": "yourupi2@okaxis",
    "payee_name": "THE LAST CHAPTER",
}
ORGANIZER_USERNAMES = ("neeraj chouhan", "aryan chouhan", "mohit yadav")
ORGANIZER_PASSWORD_ENV = {
    "neeraj chouhan": "ORGANIZER_NEERAJ_PASSWORD",
    "aryan chouhan": "ORGANIZER_ARYAN_PASSWORD",
    "mohit yadav": "ORGANIZER_MOHIT_PASSWORD",
}


def now_iso():
    return datetime.now(IST).isoformat(timespec="seconds")


def connect():
    if USE_POSTGRES:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError(
                "PostgreSQL support requires psycopg[binary]. "
                "Add it to requirements.txt and redeploy."
            ) from exc
        # autocommit=True matches the old SQLite connection's isolation_level=None.
        raw = psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=True)
        return DBConnection(raw, postgres=True)

    if DB_PATH is None:
        raise RuntimeError("Database is not initialized.")
    raw = sqlite3.connect(DB_PATH, timeout=15, isolation_level=None)
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA foreign_keys=ON")
    raw.execute("PRAGMA journal_mode=WAL")
    raw.execute("PRAGMA busy_timeout=15000")
    return DBConnection(raw, postgres=False)


def _ensure_columns_sqlite(conn, table_name, columns):
    existing = {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    for name, clause in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {name} {clause}")


def init(instance_dir):
    global DB_PATH
    os.makedirs(instance_dir, exist_ok=True)

    if USE_POSTGRES:
        conn = connect()
        try:
            conn.executescript(SCHEMA_POSTGRES)
            for key, value in DEFAULT_SETTINGS.items():
                conn.execute(
                    "INSERT INTO setting(key,value) VALUES (?,?) "
                    "ON CONFLICT(key) DO NOTHING",
                    (key, value),
                )
            seed_organizers(conn)
            conn.commit()
        finally:
            conn.close()
        return

    DB_PATH = os.path.join(instance_dir, "last_chapter.db")
    conn = connect()
    try:
        conn.executescript(SCHEMA_SQLITE)
        _ensure_columns_sqlite(conn, "booking", {
            "booking_status": "TEXT NOT NULL DEFAULT 'active' CHECK (booking_status IN ('active','cancelled'))",
            "updated_at": "TEXT",
            "cancelled_at": "TEXT",
            "cancelled_by": "TEXT",
            "cancellation_reason": "TEXT",
        })
        _ensure_columns_sqlite(conn, "pass_code_inventory", {
            "removed_at": "TEXT",
            "removed_by": "TEXT",
            "removed_reason": "TEXT",
        })
        conn.execute(
            "UPDATE booking SET booking_status='active' "
            "WHERE booking_status IS NULL OR booking_status=''"
        )
        conn.execute(
            "UPDATE booking SET updated_at=created_at WHERE updated_at IS NULL"
        )
        conn.execute(
            "UPDATE pass_code_inventory SET status='available' "
            "WHERE status IS NULL OR status=''"
        )
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO setting(key,value) VALUES (?,?)",
                (key, value),
            )
        conn.execute(
            """INSERT OR IGNORE INTO pass_code_inventory
               (code,status,booking_id,created_at,assigned_at,used_at)
               SELECT pass_code,
                      CASE WHEN entry_status='used' THEN 'used'
                           WHEN booking_status='cancelled' THEN 'available'
                           ELSE 'assigned' END,
                      CASE WHEN booking_status='cancelled'
                                AND entry_status!='used' THEN NULL
                           ELSE id END,
                      created_at, created_at,
                      CASE WHEN entry_status='used' THEN entry_time END
               FROM booking"""
        )
        seed_organizers(conn)
    finally:
        conn.close()


def seed_organizers(conn):
    for username in ORGANIZER_USERNAMES:
        if conn.execute(
            "SELECT 1 FROM organizer WHERE username=?", (username,)
        ).fetchone():
            continue
        password = os.environ.get(ORGANIZER_PASSWORD_ENV[username])
        if password:
            conn.execute(
                "INSERT INTO organizer(username,password_hash,created_at) "
                "VALUES (?,?,?)",
                (username, generate_password_hash(password), now_iso()),
            )


def load_or_create_secret(instance_dir):
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    os.makedirs(instance_dir, exist_ok=True)
    path = os.path.join(instance_dir, "secret_key")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            value = f.read().strip()
            if value:
                return value
    value = secrets.token_hex(32)
    with open(path, "w", encoding="utf-8") as f:
        f.write(value)
    return value


def get_setting(conn, key, default=None):
    row = conn.execute(
        "SELECT value FROM setting WHERE key=?", (key,)
    ).fetchone()
    return row["value"] if row else default


def get_settings(conn):
    values = dict(DEFAULT_SETTINGS)
    values.update({
        r["key"]: r["value"]
        for r in conn.execute("SELECT key,value FROM setting")
    })
    return values


def set_settings(conn, updates):
    for key, value in updates.items():
        conn.execute(
            "INSERT INTO setting(key,value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def add_inventory_codes(conn, codes):
    inserted, rejected, now = 0, [], now_iso()
    conn.execute("BEGIN")
    try:
        for code in codes:
            try:
                conn.execute(
                    "INSERT INTO pass_code_inventory(code,status,created_at) "
                    "VALUES (?,'available',?)",
                    (code, now),
                )
                inserted += 1
            except Exception as exc:
                # A duplicate code is expected and should be skipped.
                # Other database errors must not be hidden.
                if "duplicate" in str(exc).lower() or "unique" in str(exc).lower():
                    rejected.append(code)
                else:
                    raise
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return inserted, rejected


def inventory_stats(conn):
    row = conn.execute(
        """SELECT COUNT(*) AS total,
            SUM(CASE WHEN status='available' THEN 1 ELSE 0 END) AS available,
            SUM(CASE WHEN status='assigned' THEN 1 ELSE 0 END) AS assigned,
            SUM(CASE WHEN status='used' THEN 1 ELSE 0 END) AS used,
            SUM(CASE WHEN status='removed' THEN 1 ELSE 0 END) AS removed
           FROM pass_code_inventory"""
    ).fetchone()
    return {
        k: (0 if v is None else v)
        for k, v in dict(row).items()
    }


def list_inventory(conn, limit=500):
    return conn.execute(
        "SELECT * FROM pass_code_inventory "
        "ORDER BY CAST(code AS INTEGER), code LIMIT ?",
        (limit,),
    ).fetchall()


def log(conn, booking_id, action, detail):
    conn.execute(
        "INSERT INTO activity_log(booking_id,action,detail,created_at) "
        "VALUES (?,?,?,?)",
        (booking_id, action, detail, now_iso()),
    )


def create_booking_with_code(conn, v):
    conn.execute("BEGIN")
    try:
        card = conn.execute(
            "SELECT id,code FROM pass_code_inventory "
            "WHERE status='available' "
            "ORDER BY CAST(code AS INTEGER), code LIMIT 1"
        ).fetchone()
        if card is None:
            conn.rollback()
            return None

        now = now_iso()

        if conn.postgres:
            cur = conn.execute(
                """INSERT INTO booking
                   (pass_code,pass_type,person_1_name,person_1_dob,
                    person_2_name,person_2_dob,contact,payment_method,
                    payment_status,amount,created_at,updated_at,booking_status)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                   RETURNING id""",
                (
                    card["code"], v["pass_type"], v["p1_name"], v["p1_dob"],
                    v["p2_name"], v["p2_dob"], v["contact"], v["payment"],
                    "pending", v["amount"], now, now, "active",
                ),
            )
            bid = cur.fetchone()["id"]
        else:
            cur = conn.execute(
                """INSERT INTO booking
                   (pass_code,pass_type,person_1_name,person_1_dob,
                    person_2_name,person_2_dob,contact,payment_method,
                    payment_status,amount,created_at,updated_at,booking_status)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    card["code"], v["pass_type"], v["p1_name"], v["p1_dob"],
                    v["p2_name"], v["p2_dob"], v["contact"], v["payment"],
                    "pending", v["amount"], now, now, "active",
                ),
            )
            bid = cur.lastrowid

        if conn.execute(
            "UPDATE pass_code_inventory "
            "SET status='assigned',booking_id=?,assigned_at=? "
            "WHERE id=? AND status='available'",
            (bid, now, card["id"]),
        ).rowcount != 1:
            raise RuntimeError("pass-code reservation conflict")

        log(conn, bid, "booking",
            f"new {v['pass_type']} booking — {v['p1_name']}")
        conn.commit()
        return bid
    except Exception:
        conn.rollback()
        raise


def mark_code_used(conn, booking_id, used_at):
    return conn.execute(
        "UPDATE pass_code_inventory SET status='used',used_at=? "
        "WHERE booking_id=? AND status='assigned'",
        (used_at, booking_id),
    ).rowcount


def cancel_booking(conn, booking_id, organizer_name, reason=None):
    now = now_iso()
    booking = conn.execute(
        "SELECT * FROM booking WHERE id=?", (booking_id,)
    ).fetchone()
    if booking is None:
        return None
    if booking["booking_status"] == "cancelled":
        return booking

    conn.execute(
        """UPDATE booking SET booking_status='cancelled',
           cancelled_at=?,cancelled_by=?,cancellation_reason=?,updated_at=?
           WHERE id=?""",
        (
            now, organizer_name, reason or "cancelled by organizer",
            now, booking_id,
        ),
    )
    conn.execute(
        "UPDATE pass_code_inventory "
        "SET status='available',booking_id=NULL,assigned_at=NULL "
        "WHERE booking_id=? AND status='assigned'",
        (booking_id,),
    )
    return conn.execute(
        "SELECT * FROM booking WHERE id=?", (booking_id,)
    ).fetchone()


def remove_inventory_code(conn, code, organizer_name, reason=None):
    row = conn.execute(
        "SELECT * FROM pass_code_inventory WHERE code=?", (code,)
    ).fetchone()
    if row is None:
        return {
            "ok": False, "status": "not_found",
            "message": "pass code not found",
        }
    if row["status"] != "available":
        return {
            "ok": False, "status": "in_use",
            "message": "this pass code is currently assigned/used. "
                       "release or resolve its booking before removing it.",
        }
    conn.execute(
        """UPDATE pass_code_inventory
           SET status='removed',removed_at=?,removed_by=?,removed_reason=?
           WHERE code=? AND status='available'""",
        (
            now_iso(), organizer_name,
            reason or "removed by organizer", code,
        ),
    )
    return {
        "ok": True, "status": "removed",
        "message": f"pass code {code} removed from inventory",
    }


def release_pass_code(conn, code, organizer_name, reason=None):
    row = conn.execute(
        "SELECT * FROM pass_code_inventory WHERE code=?", (code,)
    ).fetchone()
    if row is None:
        return {
            "ok": False, "status": "not_found",
            "message": "pass code not found",
        }
    if row["status"] == "used":
        return {
            "ok": False, "status": "used",
            "message": "used codes stay used and cannot be released automatically",
        }
    if row["status"] == "available":
        return {
            "ok": False, "status": "already_available",
            "message": "pass code is already available",
        }

    bk = (
        conn.execute(
            "SELECT * FROM booking WHERE id=?", (row["booking_id"],)
        ).fetchone()
        if row["booking_id"] else None
    )
    conn.execute(
        "UPDATE pass_code_inventory "
        "SET status='available',booking_id=NULL,assigned_at=NULL "
        "WHERE code=? AND status='assigned'",
        (code,),
    )
    if bk:
        conn.execute(
            "UPDATE booking SET updated_at=?,booking_status='cancelled' "
            "WHERE id=? AND booking_status!='cancelled'",
            (now_iso(), bk["id"]),
        )
    return {
        "ok": True, "status": "released",
        "message": f"pass code {code} released",
    }


def stats(conn):
    row = conn.execute(
        """SELECT COUNT(*) AS total,
            SUM(CASE WHEN pass_type='single' THEN 1 ELSE 0 END) AS single,
            SUM(CASE WHEN pass_type='couple' THEN 1 ELSE 0 END) AS couple,
            SUM(CASE WHEN payment_method='upi' THEN 1 ELSE 0 END) AS upi,
            SUM(CASE WHEN payment_method='cash' THEN 1 ELSE 0 END) AS cash,
            SUM(CASE WHEN payment_status IN ('paid','received')
                     THEN 1 ELSE 0 END) AS received,
            SUM(CASE WHEN payment_status IN ('pending','submitted')
                     THEN 1 ELSE 0 END) AS pending,
            SUM(CASE WHEN entry_status='used' THEN 1 ELSE 0 END) AS used,
            SUM(CASE WHEN entry_status='unused' THEN 1 ELSE 0 END) AS unused,
            SUM(CASE WHEN booking_status='cancelled'
                     THEN 1 ELSE 0 END) AS cancelled,
            SUM(CASE WHEN booking_status IS NULL OR booking_status='active'
                     THEN 1 ELSE 0 END) AS active
           FROM booking"""
    ).fetchone()
    d = {
        k: (0 if v is None else v)
        for k, v in dict(row).items()
    }
    d["guests"] = d["single"] + d["couple"] * 2
    d["entered"] = d["used"]
    d["payment_received"] = d["received"]
    d["payment_pending"] = d["pending"]
    d["active_bookings"] = d["active"]
    d["cancelled_bookings"] = d["cancelled"]
    return d


FILTER_SQL = {
    "single": "pass_type='single'",
    "couple": "pass_type='couple'",
    "cash": "payment_method='cash'",
    "upi": "payment_method='upi'",
    "paid": "payment_status IN ('paid','received')",
    "pending": "payment_status IN ('pending','submitted')",
    "used": "entry_status='used'",
    "unused": "entry_status='unused'",
    "active": "COALESCE(booking_status,'active')='active'",
    "cancelled": "booking_status='cancelled'",
}


def list_bookings(conn, q="", f=""):
    sql, args, where = "SELECT * FROM booking", [], []
    if q.strip():
        like = "%" + q.strip() + "%"
        where.append(
            "(person_1_name LIKE ? OR person_2_name LIKE ? "
            "OR contact LIKE ? OR pass_code LIKE ?)"
        )
        args += [like] * 4
    if f in FILTER_SQL:
        where.append(FILTER_SQL[f])
    if where:
        sql += " WHERE " + " AND ".join(where)
    return conn.execute(
        sql + " ORDER BY id DESC LIMIT 500", args
    ).fetchall()


def recent_activity(conn, limit=120):
    return conn.execute(
        "SELECT * FROM activity_log ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
