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
