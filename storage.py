import sqlite3
from pathlib import Path

DATABASE = Path(__file__).with_name("orders.sqlite3")

def save_orders(orders, database=DATABASE):
    connection = sqlite3.connect(database)
    try:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                title TEXT NOT NULL,
                url TEXT PRIMARY KEY NOT NULL
            )
        """)
        result = connection.executemany("""
            INSERT INTO orders (title, url) VALUES (?, ?)
            ON CONFLICT (url) DO NOTHING
        """, orders)
        connection.commit()
        return result.rowcount
    finally:
        connection.close()

def read_orders(database=DATABASE):
    if not database.exists():
        return []
    address = database.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(address, uri=True)
    try:
        rows = connection.execute("SELECT title, url FROM orders ORDER BY rowid DESC").fetchall()
    finally:
        connection.close()
    orders = []
    for title, url in rows:
        orders.append({"title": title, "url": url})
    return orders
