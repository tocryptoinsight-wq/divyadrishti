import os
import sqlite3
import threading
import time
from pathlib import Path

import bcrypt as _bcrypt

_LOCAL_DB = Path(os.getcwd()) / "ddscanner.db"
if _LOCAL_DB.exists():
    _DB_PATH = _LOCAL_DB
else:
    _APP_DATA = Path(os.environ.get("APPDATA", str(Path.home() / ".config"))) / "DivyaDrishti"
    _APP_DATA.mkdir(parents=True, exist_ok=True)
    _DB_PATH = _APP_DATA / "ddscanner.db"
_local = threading.local()
_MAX_RETRIES = 5
_RETRY_DELAY = 0.05


def _get_conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(str(_DB_PATH))
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
    return _local.conn


def init_db():
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
    try:
        conn.execute("ALTER TABLE users ADD COLUMN failed_attempts INTEGER NOT NULL DEFAULT 0")
        conn.commit()
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE users ADD COLUMN last_active TEXT")
        conn.commit()
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE users ADD COLUMN session_token TEXT")
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
    # Migrate old single session from users table to sessions
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
        _dev_seed = [
            ("admin",     "admin123",     "admin"),
            ("admin_dev", "Admin@Dev",    "admin"),
            ("user",      "user123",      "user"),
        ]
        for u, p, r in _dev_seed:
            conn.execute(
                "INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
                (u, _bcrypt.hashpw(p.encode(), _bcrypt.gensalt()).decode(), r),
            )
        conn.commit()

    # Dev-only convenience: ensure dev accounts always work on every startup
    # (only runs for the dev project at E:\DDTools\DDScanner, NOT the live deployment)
    import os as _os
    _cwd = _os.getcwd().lower()
    if "ddtools-live" not in _cwd:
        _dev_ensured = [
            ("admin",     "admin123",     "admin"),
            ("admin_dev", "Admin@Dev",    "admin"),
            ("user",      "user123",      "user"),
        ]
        for u, p, r in _dev_ensured:
            _pw = _bcrypt.hashpw(p.encode(), _bcrypt.gensalt()).decode()
            _existing = conn.execute("SELECT id FROM users WHERE username = ?", (u,)).fetchone()
            if _existing:
                conn.execute("UPDATE users SET password = ?, failed_attempts = 0, is_active = 1 WHERE username = ?", (_pw, u))
            else:
                conn.execute("INSERT INTO users (username, password, role) VALUES (?, ?, ?)", (u, _pw, r))
        conn.commit()

    _local.conn = None
    # Integrity check + auto-repair (fire-and-forget in background thread)
    try:
        import threading
        threading.Thread(target=_integrity_check, daemon=True).start()
    except Exception:
        pass


def _integrity_check():
    """Run integrity check + REINDEX in background. Must not block startup."""
    try:
        conn = _get_conn()
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            print(f"[DB] Integrity issue detected: {result}. Running REINDEX...")
            conn.execute("REINDEX")
            conn.commit()
            print("[DB] REINDEX completed.")
    except Exception:
        pass
    finally:
        _local.conn = None


def _retry(fn):
    for attempt in range(_MAX_RETRIES + 1):
        try:
            return fn()
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e) and attempt < _MAX_RETRIES:
                time.sleep(_RETRY_DELAY * (2 ** attempt))
                _local.conn = None
                continue
            raise


def query(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    def _do():
        conn = _get_conn()
        return conn.execute(sql, params).fetchall()
    return _retry(_do)


def query_one(sql: str, params: tuple = ()) -> sqlite3.Row | None:
    def _do():
        conn = _get_conn()
        cur = conn.execute(sql, params)
        return cur.fetchone()
    return _retry(_do)


def execute(sql: str, params: tuple = ()) -> int:
    def _do():
        conn = _get_conn()
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid or 0
    return _retry(_do)


def hash_password(password: str) -> str:
    return _bcrypt.hashpw(password.encode(), _bcrypt.gensalt()).decode()


def verify_password(password: str, hash_: str) -> bool:
    return _bcrypt.checkpw(password.encode(), hash_.encode())


def close_db():
    if hasattr(_local, "conn") and _local.conn:
        _local.conn.close()
        _local.conn = None
