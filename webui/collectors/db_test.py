from __future__ import annotations

from typing import Tuple

try:
    import mysql.connector
    from mysql.connector import Error
except ImportError:  # pragma: no cover
    mysql = None  # type: ignore
    Error = Exception  # type: ignore


def test_database_connection(
    host: str,
    port: int,
    database: str,
    user: str,
    password: str,
    timeout_sec: float = 5.0,
) -> Tuple[bool, str]:
    if mysql is None:
        return False, 'mysql-connector-python is not installed in the webui venv'
    try:
        conn = mysql.connector.connect(
            host=str(host).strip(),
            port=int(port),
            user=str(user).strip(),
            password=str(password),
            database=str(database).strip(),
            connection_timeout=max(1, int(timeout_sec)),
        )
        try:
            cur = conn.cursor()
            cur.execute('SELECT 1')
            cur.fetchone()
            cur.close()
        finally:
            conn.close()
        return True, f'Connected to {user}@{host}:{port}/{database}'
    except Error as exc:
        return False, str(exc)
    except Exception as exc:
        return False, str(exc)
