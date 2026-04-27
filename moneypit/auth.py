import secrets
from datetime import UTC, datetime, timedelta

import bcrypt
from fastapi import HTTPException, Request

from .db import connect, DEFAULT_PROFILE_NAME, DEFAULT_PROFILE_COLOR

SESSION_LIFETIME_DAYS = 30
COOKIE_NAME = "moneypit_session"


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_session(conn, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(UTC) + timedelta(days=SESSION_LIFETIME_DAYS)).strftime("%Y-%m-%dT%H:%M:%S")
    conn.execute(
        "INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
        (token, user_id, expires),
    )
    return token


def get_session_user(conn, token: str) -> dict | None:
    row = conn.execute(
        """SELECT u.id, u.email FROM sessions s
           JOIN users u ON u.id = s.user_id
           WHERE s.token = ? AND s.expires_at > datetime('now')""",
        (token,),
    ).fetchone()
    return dict(row) if row else None


def delete_session(conn, token: str) -> None:
    conn.execute("DELETE FROM sessions WHERE token = ?", (token,))


def cleanup_expired_sessions(conn) -> None:
    conn.execute("DELETE FROM sessions WHERE expires_at <= datetime('now')")


def get_current_user(request: Request) -> dict:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="login_required")
    with connect() as conn:
        user = get_session_user(conn, token)
    if not user:
        raise HTTPException(status_code=401, detail="login_required")
    return user


def register_user(conn, email: str, password: str) -> int:
    pw_hash = hash_password(password)
    cur = conn.execute(
        "INSERT INTO users (email, password_hash) VALUES (?, ?)",
        (email, pw_hash),
    )
    user_id = cur.lastrowid
    existing_users = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
    if existing_users == 1:
        conn.execute("UPDATE profiles SET user_id = ? WHERE user_id IS NULL", (user_id,))
    return user_id


def create_default_profile(conn, user_id: int) -> int:
    cur = conn.execute(
        "INSERT INTO profiles (name, color, user_id) VALUES (?, ?, ?)",
        (DEFAULT_PROFILE_NAME, DEFAULT_PROFILE_COLOR, user_id),
    )
    return cur.lastrowid
