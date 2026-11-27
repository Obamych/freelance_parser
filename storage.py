import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

DATABASE = Path(__file__).with_name("orders.sqlite3")

def order_columns(connection):
    return [column[1] for column in connection.execute("PRAGMA table_info(orders)")]

def save_orders(orders, database=DATABASE, checked_sources=()):
    connection = sqlite3.connect(database)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                title TEXT NOT NULL,
                url TEXT PRIMARY KEY NOT NULL,
                budget TEXT,
                found_at TEXT,
                last_seen TEXT,
                is_current INTEGER NOT NULL DEFAULT 0
            )
        """)
        columns = order_columns(connection)
        if "budget" not in columns:
            connection.execute("ALTER TABLE orders ADD COLUMN budget TEXT")
        if "found_at" not in columns:
            connection.execute("ALTER TABLE orders ADD COLUMN found_at TEXT")
        if "last_seen" not in columns:
            connection.execute("ALTER TABLE orders ADD COLUMN last_seen TEXT")
        if "is_current" not in columns:
            connection.execute("ALTER TABLE orders ADD COLUMN is_current INTEGER NOT NULL DEFAULT 0")
        found_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for prefix in checked_sources:
            connection.execute("UPDATE orders SET is_current = 0 WHERE substr(url, 1, length(?)) = ?", (prefix, prefix))
        added = 0
        for title, url, budget in orders:
            result = connection.execute("""
                INSERT INTO orders (title, url, budget, found_at, last_seen, is_current)
                VALUES (?, ?, ?, ?, ?, 1)
                ON CONFLICT (url) DO NOTHING
            """, (title, url, budget, found_at, found_at))
            if result.rowcount:
                added += 1
            else:
                connection.execute("""
                    UPDATE orders SET title = ?, budget = ?, last_seen = ?, is_current = 1
                    WHERE url = ?
                """, (title, budget, found_at, url))
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
        if "is_current" not in columns or "last_seen" not in columns:
            return []
        budget_column = "budget" if "budget" in columns else "NULL"
        time_column = "found_at" if "found_at" in columns else "NULL"
        since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
        rows = connection.execute(f"""
            SELECT title, url, {budget_column}, {time_column}, last_seen
            FROM orders WHERE is_current = 1 AND last_seen >= ?
            ORDER BY last_seen DESC, rowid DESC
        """, (since,)).fetchall()
    finally:
        connection.close()
    orders = []
    for title, url, budget, found_at, last_seen in rows:
        orders.append({"title": title, "url": url, "budget": budget, "found_at": found_at, "last_seen": last_seen})
    return orders

def prepare_notifications(chat_id, database=DATABASE):
    connection = sqlite3.connect(database)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("CREATE TABLE IF NOT EXISTS notification_chats (chat_id INTEGER PRIMARY KEY)")
        connection.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                chat_id INTEGER NOT NULL,
                url TEXT NOT NULL,
                PRIMARY KEY (chat_id, url)
            )
        """)
        result = connection.execute("INSERT INTO notification_chats VALUES (?) ON CONFLICT DO NOTHING", (chat_id,))
        first_run = result.rowcount == 1
        if first_run:
            connection.execute("INSERT INTO notifications SELECT ?, url FROM orders", (chat_id,))
        connection.commit()
        return first_run
    finally:
        connection.close()

def notification_urls(chat_id, database=DATABASE):
    connection = sqlite3.connect(database)
    try:
        rows = connection.execute("SELECT url FROM notifications WHERE chat_id = ?", (chat_id,)).fetchall()
        return {row[0] for row in rows}
    finally:
        connection.close()

def mark_notified(chat_id, url, database=DATABASE):
    connection = sqlite3.connect(database)
    try:
        connection.execute("INSERT INTO notifications VALUES (?, ?) ON CONFLICT DO NOTHING", (chat_id, url))
        connection.commit()
    finally:
        connection.close()

def update_status(name, values, database=DATABASE):
    connection = sqlite3.connect(database)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("CREATE TABLE IF NOT EXISTS app_status (name TEXT PRIMARY KEY, data TEXT NOT NULL)")
        row = connection.execute("SELECT data FROM app_status WHERE name = ?", (name,)).fetchone()
        data = json.loads(row[0]) if row else {}
        data.update(values)
        data["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        connection.execute("""
            INSERT INTO app_status VALUES (?, ?)
            ON CONFLICT (name) DO UPDATE SET data = excluded.data
        """, (name, json.dumps(data, ensure_ascii=False)))
        connection.commit()
    finally:
        connection.close()

def read_status(database=DATABASE):
    if not database.exists():
        return {}
    connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        table = connection.execute("SELECT name FROM sqlite_master WHERE name = 'app_status' AND type = 'table'").fetchone()
        if not table:
            return {}
        return {name: json.loads(data) for name, data in connection.execute("SELECT name, data FROM app_status")}
    finally:
        connection.close()