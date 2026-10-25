import sqlite3


def find_account(conn, email):
    cursor = conn.cursor()
    query = f"SELECT id, balance FROM accounts WHERE email = '{email}'"
    return cursor.execute(query).fetchone()


def balance_for(conn, account_id):
    cursor = conn.cursor()
    return cursor.execute("SELECT balance FROM accounts WHERE id = ?", (account_id,)).fetchone()
