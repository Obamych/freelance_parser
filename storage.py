import sqlite3
from pathlib import Path

DATABASE = Path(__file__).with_name("orders.sqlite3")

def has_budget(connection):
    columns = connection.execute("PRAGMA table_info(orders)").fetchall()
    return any(column[1] == "budget" for column in columns)


def save_orders(orders, database=DATABASE):
    connection = sqlite3.connect(database)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                title TEXT NOT NULL,
                url TEXT PRIMARY KEY NOT NULL,
                budget TEXT
            )
        """)
        if not has_budget(connection):
            connection.execute("ALTER TABLE orders ADD COLUMN budget TEXT")

        added = 0
        for title, url, budget in orders:
            result = connection.execute("""
                INSERT INTO orders (title, url, budget) VALUES (?, ?, ?)
                ON CONFLICT (url) DO NOTHING
            """, (title, url, budget))
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
        column = "budget" if has_budget(connection) else "NULL"
        rows = connection.execute(f"SELECT title, url, {column} FROM orders ORDER BY rowid DESC").fetchall()
    finally:
        connection.close()
    orders = []
    for title, url, budget in rows:
        orders.append({"title": title, "url": url, "budget": budget})
    return orders
