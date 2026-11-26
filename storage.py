import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DATABASE = Path(__file__).with_name("orders.sqlite3")

def order_columns(connection):
    return [column[1] for column in connection.execute("PRAGMA table_info(orders)")]


def save_orders(orders, database=DATABASE):
    connection = sqlite3.connect(database)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                title TEXT NOT NULL,
                url TEXT PRIMARY KEY NOT NULL,
                budget TEXT,
                found_at TEXT
            )
        """)
        columns = order_columns(connection)
        if "budget" not in columns:
            connection.execute("ALTER TABLE orders ADD COLUMN budget TEXT")
        if "found_at" not in columns:
            connection.execute("ALTER TABLE orders ADD COLUMN found_at TEXT")

        found_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        added = 0
        for title, url, budget in orders:
            result = connection.execute("""
                INSERT INTO orders (title, url, budget, found_at) VALUES (?, ?, ?, ?)
                ON CONFLICT (url) DO NOTHING
            """, (title, url, budget, found_at))
            if result.rowcount:
                added += 1
            else:
                connection.execute("UPDATE orders SET budget = ? WHERE url = ? AND budget IS NOT ?", (budget, url, budget))
        connection.commit()
        return added
    finally:
        connection.close()

def read_orders(database=DATABASE):
    if not database.exists():
        return []
    address = database.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(address, uri=True)
    try:
        columns = order_columns(connection)
        budget_column = "budget" if "budget" in columns else "NULL"
        time_column = "found_at" if "found_at" in columns else "NULL"
        rows = connection.execute(f"SELECT title, url, {budget_column}, {time_column} FROM orders ORDER BY rowid DESC").fetchall()
    finally:
        connection.close()
    orders = []
    for title, url, budget, found_at in rows:
        orders.append({"title": title, "url": url, "budget": budget, "found_at": found_at})
    return orders
