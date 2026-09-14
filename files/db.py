import os
import sqlite3

import bcrypt

DB_PATH = os.path.join(os.path.dirname(__file__), "users.db")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _get_table_columns(conn: sqlite3.Connection, table_name: str):
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return [row[1] for row in rows]


def _migrate_legacy_users_table(conn: sqlite3.Connection) -> None:
    """Handle older databases that stored users by username instead of email."""
    cols = _get_table_columns(conn, "users")

    if "email" in cols:
        if "created_at" not in cols:
            try:
                conn.execute(
                    "ALTER TABLE users ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP"
                )
            except sqlite3.Error:
                pass
        return

    if "username" in cols:
        conn.execute("ALTER TABLE users RENAME TO users_legacy")
        conn.execute(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                full_name TEXT DEFAULT '',
                details TEXT DEFAULT '',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            INSERT INTO users (id, email, password_hash, full_name, details, created_at)
            SELECT id, username, password_hash, full_name, details, CURRENT_TIMESTAMP
            FROM users_legacy
            """
        )
        conn.execute("DROP TABLE users_legacy")
        conn.commit()


def _repair_scores_table(conn: sqlite3.Connection) -> None:
    """Fix legacy scores tables that still point at users_legacy."""
    scores_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='scores'"
    ).fetchone()
    if scores_sql and "users_legacy" not in scores_sql[0]:
        return

    old_rows = conn.execute(
        "SELECT id, user_email, correct_total, total, created_at FROM scores"
    ).fetchall()

    conn.execute("ALTER TABLE scores RENAME TO scores_old")
    conn.execute(
        """
        CREATE TABLE scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_email TEXT NOT NULL,
            correct_total INTEGER NOT NULL,
            total INTEGER NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_email) REFERENCES users(email)
        )
        """
    )

    for row in old_rows:
        conn.execute(
            """
            INSERT INTO scores (id, user_email, correct_total, total, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (row[0], row[1], row[2], row[3], row[4]),
        )

    conn.execute("DROP TABLE scores_old")
    conn.commit()


def _init_db() -> None:
    conn = _connect()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                full_name TEXT DEFAULT '',
                details TEXT DEFAULT '',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        _migrate_legacy_users_table(conn)
        _repair_scores_table(conn)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_email TEXT NOT NULL,
                correct_total INTEGER NOT NULL,
                total INTEGER NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_email) REFERENCES users(email)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


_init_db()


def create_user(email: str, password: str) -> bool:
    clean_email = (email or "").strip().lower()
    if not clean_email or not password:
        return False

    conn = _connect()
    try:
        cols = _get_table_columns(conn, "users")
        existing = None

        if "email" in cols:
            existing = conn.execute(
                "SELECT 1 FROM users WHERE email = ?",
                (clean_email,),
            ).fetchone()
        elif "username" in cols:
            existing = conn.execute(
                "SELECT 1 FROM users WHERE username = ?",
                (clean_email,),
            ).fetchone()

        if existing:
            return False

        password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        conn.execute(
            "INSERT INTO users (email, password_hash) VALUES (?, ?)",
            (clean_email, password_hash),
        )
        conn.commit()
        return True
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def verify_user(email: str, password: str) -> bool:
    clean_email = (email or "").strip().lower()
    if not clean_email or not password:
        return False

    conn = _connect()
    try:
        cols = _get_table_columns(conn, "users")

        row = None
        if "email" in cols:
            row = conn.execute(
                "SELECT password_hash FROM users WHERE email = ?",
                (clean_email,),
            ).fetchone()

        if row is None and "username" in cols:
            row = conn.execute(
                "SELECT password_hash FROM users WHERE username = ?",
                (clean_email,),
            ).fetchone()

        if row is None:
            return False

        return bcrypt.checkpw(password.encode("utf-8"), row["password_hash"].encode("utf-8"))
    except Exception:
        return False
    finally:
        conn.close()


def get_user_profile(email: str):
    clean_email = (email or "").strip().lower()
    if not clean_email:
        return None

    conn = _connect()
    try:
        row = conn.execute(
            "SELECT email, full_name, details FROM users WHERE email = ?",
            (clean_email,),
        ).fetchone()
        if row is None:
            return None
        return {"email": row["email"], "full_name": row["full_name"], "details": row["details"]}
    finally:
        conn.close()


def update_user_profile(email: str, full_name: str, details: str) -> bool:
    clean_email = (email or "").strip().lower()
    if not clean_email:
        return False

    conn = _connect()
    try:
        conn.execute(
            "UPDATE users SET full_name = ?, details = ? WHERE email = ?",
            (full_name or "", details or "", clean_email),
        )
        conn.commit()
        return True
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def save_quiz_score(email: str, correct_total: int, total: int) -> bool:
    clean_email = (email or "").strip().lower()
    if not clean_email:
        return False

    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO scores (user_email, correct_total, total) VALUES (?, ?, ?)",
            (clean_email, int(correct_total), int(total)),
        )
        conn.commit()
        return True
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def get_leaderboard():
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT
                u.email,
                COUNT(s.id) AS quizzes_completed,
                MAX(s.correct_total) AS best_score,
                MAX(s.total) AS total_questions
            FROM users u
            LEFT JOIN scores s ON u.email = s.user_email
            GROUP BY u.email
            ORDER BY best_score DESC, quizzes_completed DESC, u.email ASC
            """
        ).fetchall()

        leaderboard = []
        for row in rows:
            leaderboard.append(
                {
                    "email": row["email"],
                    "quizzes_completed": row["quizzes_completed"],
                    "best_score": row["best_score"] or 0,
                    "total_questions": row["total_questions"] or 0,
                }
            )
        return leaderboard
    finally:
        conn.close()
