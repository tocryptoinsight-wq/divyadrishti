import os
import threading
import time
from pathlib import Path

import bcrypt as _bcrypt

_DATABASE_URL = os.environ.get("DATABASE_URL")
_IS_PG = bool(_DATABASE_URL)

if _IS_PG:
    import psycopg2
    import psycopg2.extras

    def _get_conn():
        if not hasattr(_thread_local, "conn") or _thread_local.conn is None:
            _thread_local.conn = psycopg2.connect(_DATABASE_URL, sslmode="require")
            _thread_local.conn.autocommit = False
        return _thread_local.conn

    def _fix(sql):
        return sql.replace("?", "%s")

    def _init_db():
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id              SERIAL PRIMARY KEY,
                username        VARCHAR(255) UNIQUE NOT NULL,
                password        TEXT NOT NULL,
                role            VARCHAR(50) NOT NULL DEFAULT 'user',
                is_active       BOOLEAN NOT NULL DEFAULT TRUE,
                created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
                expires_at      TIMESTAMP,
                failed_attempts INTEGER NOT NULL DEFAULT 0,
                last_active     TIMESTAMP,
                session_token   TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS algo_state (
                username        VARCHAR(255) NOT NULL,
                symbol          VARCHAR(255) NOT NULL,
                state_json      TEXT NOT NULL,
                credentials_json TEXT,
                setup_json      TEXT,
                updated_at      TIMESTAMP NOT NULL DEFAULT NOW(),
                PRIMARY KEY (username, symbol)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_screener_symbols (
                username VARCHAR(255) NOT NULL,
                symbol   VARCHAR(255) NOT NULL,
                PRIMARY KEY (username, symbol)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_api_keys (
                id         SERIAL PRIMARY KEY,
                username   VARCHAR(255) NOT NULL,
                api_key    TEXT NOT NULL,
                api_secret TEXT NOT NULL,
                type       VARCHAR(50) NOT NULL DEFAULT 'read',
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id            SERIAL PRIMARY KEY,
                username      VARCHAR(255) NOT NULL,
                session_token TEXT NOT NULL UNIQUE,
                device_name   TEXT DEFAULT '',
                created_at    TIMESTAMP NOT NULL DEFAULT NOW(),
                last_active   TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_drawings (
                username      VARCHAR(255) NOT NULL,
                symbol        VARCHAR(255) NOT NULL,
                drawings_json TEXT NOT NULL,
                updated_at    TIMESTAMP NOT NULL DEFAULT NOW(),
                PRIMARY KEY (username, symbol)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS backup_schedules (
                id              SERIAL PRIMARY KEY,
                source          VARCHAR(255) NOT NULL DEFAULT 'live',
                frequency       VARCHAR(50) NOT NULL DEFAULT 'manual',
                destination     VARCHAR(50) NOT NULL DEFAULT 'both',
                scheduled_time  TIME DEFAULT '02:00',
                day_of_week     INTEGER DEFAULT NULL,
                day_of_month    INTEGER DEFAULT NULL,
                enabled         BOOLEAN DEFAULT TRUE,
                last_run        TIMESTAMP,
                next_run        TIMESTAMP,
                created_at      TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("SELECT COUNT(*) AS cnt FROM users")
        if cur.fetchone()["cnt"] == 0:
            _seed_users(conn)
        conn.commit()
        cur.close()
        _thread_local.conn = None

else:
    import sqlite3

    _LOCAL_DB = Path(os.getcwd()) / "ddscanner.db"
    if _LOCAL_DB.exists():
        _DB_PATH = _LOCAL_DB
    else:
        _APP_DATA = Path(os.environ.get("APPDATA", str(Path.home() / ".config"))) / "DivyaDrishti"
        _APP_DATA.mkdir(parents=True, exist_ok=True)
        _DB_PATH = _APP_DATA / "ddscanner.db"

    def _get_conn():
        if not hasattr(_thread_local, "conn") or _thread_local.conn is None:
            _thread_local.conn = sqlite3.connect(str(_DB_PATH))
            _thread_local.conn.row_factory = sqlite3.Row
            _thread_local.conn.execute("PRAGMA journal_mode=WAL")
            _thread_local.conn.execute("PRAGMA foreign_keys=ON")
        return _thread_local.conn

    def _fix(sql):
        return sql

    def _init_db():
        conn = _get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                username        TEXT UNIQUE NOT NULL,
                password        TEXT NOT NULL,
                role            TEXT NOT NULL DEFAULT 'user',
                is_active       INTEGER NOT NULL DEFAULT 1,
                created_at      TEXT NOT NULL DEFAULT (datetime('now')),
                expires_at      TEXT,
                failed_attempts INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.commit()
        for col in ("failed_attempts", "last_active", "session_token"):
            try:
                conn.execute(f"ALTER TABLE users ADD COLUMN {col} TEXT")
                conn.commit()
            except Exception:
                pass
        conn.execute("""
            CREATE TABLE IF NOT EXISTS algo_state (
                username TEXT NOT NULL,
                symbol TEXT NOT NULL,
                state_json TEXT NOT NULL,
                credentials_json TEXT,
                setup_json TEXT,
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (username, symbol)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_screener_symbols (
                username TEXT NOT NULL,
                symbol TEXT NOT NULL,
                PRIMARY KEY (username, symbol)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_api_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                api_key TEXT NOT NULL,
                api_secret TEXT NOT NULL,
                type TEXT NOT NULL DEFAULT 'read',
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                session_token TEXT NOT NULL UNIQUE,
                device_name TEXT DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                last_active TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_drawings (
                username TEXT NOT NULL,
                symbol TEXT NOT NULL,
                drawings_json TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (username, symbol)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS backup_schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL DEFAULT 'live',
                frequency TEXT NOT NULL DEFAULT 'manual',
                destination TEXT NOT NULL DEFAULT 'both',
                scheduled_time TEXT DEFAULT '02:00',
                day_of_week INTEGER DEFAULT NULL,
                day_of_month INTEGER DEFAULT NULL,
                enabled INTEGER DEFAULT 1,
                last_run TEXT,
                next_run TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.commit()
        try:
            rows = conn.execute(
                "SELECT username, session_token, last_active FROM users WHERE session_token IS NOT NULL"
            ).fetchall()
            for r in rows:
                existing = conn.execute(
                    "SELECT COUNT(*) FROM sessions WHERE username = ?", (r["username"],)
                ).fetchone()[0]
                if existing == 0:
                    conn.execute(
                        "INSERT INTO sessions (username, session_token, device_name, created_at, last_active) VALUES (?, ?, 'Migrated', datetime('now'), ?)",
                        (r["username"], r["session_token"], r["last_active"]),
                    )
            conn.commit()
        except Exception:
            pass
        conn.commit()
        cur = conn.execute("SELECT COUNT(*) FROM users")
        if cur.fetchone()[0] == 0:
            _seed_users(conn)
        conn.commit()
        _thread_local.conn = None
        try:
            threading.Thread(target=_integrity_check, daemon=True).start()
        except Exception:
            pass

_thread_local = threading.local()
_MAX_RETRIES = 5
_RETRY_DELAY = 0.05

_HOSTING_USERS = [
    ("divyadrishti_live", "Divya_Drishti@Live", "admin"),
    ("DD_Live",           "D_D@Live",           "admin"),
    ("mohit_live",        "Mohit@Live",          "user"),
    ("mahendra_live",     "Mahendra@Live",       "user"),
]


def _seed_users(conn):
    for u, p, r in _HOSTING_USERS:
        pw = _bcrypt.hashpw(p.encode(), _bcrypt.gensalt()).decode()
        if _IS_PG:
            cur = conn.cursor()
            cur.execute("INSERT INTO users (username, password, role) VALUES (%s, %s, %s) ON CONFLICT (username) DO NOTHING", (u, pw, r))
            cur.close()
        else:
            conn.execute("INSERT OR IGNORE INTO users (username, password, role) VALUES (?, ?, ?)", (u, pw, r))
    if _IS_PG:
        conn.commit()


def init_db():
    _init_db()


def _integrity_check():
    if _IS_PG:
        return
    try:
        conn = _get_conn()
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            print(f"[DB] Integrity issue: {result}. Running REINDEX...")
            conn.execute("REINDEX")
            conn.commit()
    except Exception:
        pass
    finally:
        _thread_local.conn = None


def _retry(fn):
    for attempt in range(_MAX_RETRIES + 1):
        try:
            return fn()
        except Exception as e:
            err = str(e)
            if "database is locked" in err and attempt < _MAX_RETRIES:
                time.sleep(_RETRY_DELAY * (2 ** attempt))
                _thread_local.conn = None
                continue
            raise


def query(sql: str, params: tuple = ()) -> list:
    def _do():
        conn = _get_conn()
        if _IS_PG:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(_fix(sql), params)
            rows = cur.fetchall()
            cur.close()
            return rows
        return conn.execute(sql, params).fetchall()
    return _retry(_do)


def query_one(sql: str, params: tuple = ()):
    def _do():
        conn = _get_conn()
        if _IS_PG:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(_fix(sql), params)
            row = cur.fetchone()
            cur.close()
            return row
        cur = conn.execute(sql, params)
        return cur.fetchone()
    return _retry(_do)


def execute(sql: str, params: tuple = ()) -> int:
    def _do():
        conn = _get_conn()
        if _IS_PG:
            cur = conn.cursor()
            cur.execute(_fix(sql), params)
            conn.commit()
            last_id = cur.fetchone()
            cur.close()
            if last_id:
                return last_id[0]
            try:
                cur2 = conn.cursor()
                cur2.execute("SELECT LASTVAL()")
                val = cur2.fetchone()[0]
                cur2.close()
                return val or 0
            except Exception:
                return 0
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid or 0
    return _retry(_do)


def hash_password(password: str) -> str:
    return _bcrypt.hashpw(password.encode(), _bcrypt.gensalt()).decode()


def verify_password(password: str, hash_: str) -> bool:
    return _bcrypt.checkpw(password.encode(), hash_.encode())


def close_db():
    if hasattr(_thread_local, "conn") and _thread_local.conn:
        _thread_local.conn.close()
        _thread_local.conn = None
