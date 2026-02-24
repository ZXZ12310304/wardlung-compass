from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
from datetime import datetime
from typing import Tuple

_BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_DB_PATH = os.path.join(_BASE_DIR, "data", "ward_demo.db")
_LOCK = threading.Lock()

_ALGO = "pbkdf2_sha256"
_ITERATIONS = 210000


def configure(*, db_path: str) -> None:
    global _DB_PATH
    _DB_PATH = db_path or _DB_PATH


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_table() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS account_credentials (
                account_key TEXT PRIMARY KEY,
                role TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )


def _hash_password(raw_password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        (raw_password or "").encode("utf-8"),
        salt,
        _ITERATIONS,
    )
    return f"{_ALGO}${_ITERATIONS}${salt.hex()}${dk.hex()}"


def _verify_password(raw_password: str, stored: str) -> bool:
    try:
        algo, iters, salt_hex, hash_hex = (stored or "").split("$", 3)
        if algo != _ALGO:
            return False
        iterations = int(iters)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except Exception:
        return False
    actual = hashlib.pbkdf2_hmac(
        "sha256",
        (raw_password or "").encode("utf-8"),
        salt,
        iterations,
    )
    return hmac.compare_digest(actual, expected)


def ensure_default_credential(
    account_key: str, role: str, default_password: str = "Demo@123"
) -> None:
    key = (account_key or "").strip()
    if not key:
        return
    with _LOCK:
        _ensure_table()
        with _connect() as conn:
            row = conn.execute(
                "SELECT account_key FROM account_credentials WHERE account_key = ?",
                (key,),
            ).fetchone()
            if row:
                return
            conn.execute(
                """
                INSERT INTO account_credentials (account_key, role, password_hash, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (key, role or "unknown", _hash_password(default_password), _now_iso()),
            )


def verify_password(account_key: str, raw_password: str) -> bool:
    key = (account_key or "").strip()
    if not key:
        return False
    with _LOCK:
        _ensure_table()
        with _connect() as conn:
            row = conn.execute(
                "SELECT password_hash FROM account_credentials WHERE account_key = ?",
                (key,),
            ).fetchone()
    if not row:
        return False
    return _verify_password(raw_password or "", row["password_hash"] or "")


def set_password(account_key: str, role: str, raw_password: str) -> None:
    key = (account_key or "").strip()
    if not key:
        return
    with _LOCK:
        _ensure_table()
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO account_credentials (account_key, role, password_hash, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(account_key) DO UPDATE SET
                    role = excluded.role,
                    password_hash = excluded.password_hash,
                    updated_at = excluded.updated_at
                """,
                (key, role or "unknown", _hash_password(raw_password or ""), _now_iso()),
            )


def change_password(
    account_key: str,
    role: str,
    old_password: str,
    new_password: str,
    confirm_password: str,
) -> Tuple[bool, str]:
    newp = (new_password or "").strip()
    conf = (confirm_password or "").strip()
    if not (old_password or "").strip():
        return False, "Current password is required."
    if len(newp) < 6:
        return False, "New password must be at least 6 characters."
    if newp != conf:
        return False, "Password confirmation does not match."
    if not verify_password(account_key, old_password or ""):
        return False, "Current password is incorrect."
    set_password(account_key, role, newp)
    return True, "Password updated"

