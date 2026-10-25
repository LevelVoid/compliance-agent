import sqlite3


def find_account(conn, email):
    cursor = conn.cursor()
    return cursor.execute(
        "SELECT id, balance FROM accounts WHERE email = ?", (email,)
    ).fetchone()


def balance_for(conn, account_id):
    cursor = conn.cursor()
    return cursor.execute("SELECT balance FROM accounts WHERE id = ?", (account_id,)).fetchone()
