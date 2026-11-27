import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
import bot
import parser
from storage import notification_urls, prepare_notifications, read_orders, save_orders, set_favorite

class OrderDetailsTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        self.database = Path(directory) / "orders.sqlite3"

    def test_kwork_description_publication_and_explicit_inactive_state(self):
        projects = [
            {"id": 1, "name": "Python", "description": "<p>Скрипт <b>Python</b></p>", "date_create": "2026-11-27 12:00:00", "isWantActive": True, "isWantArchive": False},
            {"id": 2, "name": "Архив", "description": "Описание", "isWantActive": False, "isWantArchive": True},
        ]
        data = {"wantsListData": {"pagination": {"data": projects}}}
        html = "<script>window.stateData = " + json.dumps(data) + ";</script>"
        orders = parser.parse_kwork(html)
        self.assertEqual(orders[0][3:], ("Скрипт Python", "2026-11-27T12:00:00", "open"))
        self.assertEqual(orders[1][5], "inactive")
        save_orders(orders, self.database)
        self.assertEqual([order["url"] for order in read_orders(self.database)], ["https://kwork.ru/projects/1"])
        set_favorite(42, 2, True, self.database)
        saved = read_orders(self.database, favorite_chat=42)[0]
        self.assertEqual(saved["status"], "inactive")
        self.assertIn("Состояние при последнем сборе: неактивен", bot.order_card(saved)["text"])

    def test_freelance_description_and_absolute_date_without_guessed_status(self):
        html = '''<article class="task-card">
<a class="task-card__title-link" href="/task/view/12">Дизайн</a>
<p class="task-card__desc">Сделать<br>новую обложку...</p>
<div class="task-card__budget">950 ₽ / заказ</div>
<span class="task-card__foot-item" title="27.11.2026 12:54">3 минуты назад</span>
</article>'''
        order = parser.parse_freelance(html)[0]
        self.assertEqual(order[3:], ("Сделать новую обложку...", "2026-11-27T12:54:00", "unknown"))
        card = bot.order_card({"title": order[0], "url": order[1], "published_at": order[4]})
        self.assertIn("27.11.2026 12:54 (время площадки)", card["text"])
        self.assertIn("Состояние при последнем сборе: неизвестно", card["text"])

    def test_schema_upgrade_preserves_saved_order_and_found_time(self):
        connection = sqlite3.connect(self.database)
        connection.execute("CREATE TABLE orders (title TEXT, url TEXT PRIMARY KEY, budget TEXT, found_at TEXT, last_seen TEXT, is_current INTEGER)")
        connection.execute("INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?)", ("Старый", "https://kwork.ru/projects/1", "500 ₽", "2026-11-01T10:00:00+00:00", "2026-11-27T10:00:00+00:00", 1))
        connection.commit()
        connection.close()
        prepare_notifications(42, self.database)
        set_favorite(42, 1, True, self.database)
        save_orders([("Новый заголовок", "https://kwork.ru/projects/1", "1000 ₽", "Описание", "2026-11-01T12:00:00", "open")], self.database)
        order = read_orders(self.database, favorite_chat=42)[0]
        self.assertEqual(order["id"], 1)
        self.assertEqual(order["found_at"], "2026-11-01T10:00:00+00:00")
        self.assertEqual(order["description"], "Описание")
        self.assertEqual(order["status"], "open")
        self.assertEqual(notification_urls(42, self.database), {"https://kwork.ru/projects/1"})

    def test_missing_and_aware_publication_times_are_rendered_correctly(self):
        self.assertEqual(bot.format_publication(None), "не указано")
        self.assertEqual(bot.format_publication("вчера"), "не указано")
        self.assertEqual(bot.format_publication("2026-11-27T12:00:00+00:00"), "27.11.2026 15:00 МСК")
        self.assertIsNone(parser.publication_date("вчера"))

    def test_absence_from_feed_does_not_mean_closed(self):
        save_orders([("Python", "https://kwork.ru/projects/1", None, "Описание", None, "open")], self.database)
        number = read_orders(self.database)[0]["id"]
        set_favorite(42, number, True, self.database)
        save_orders([], self.database, checked_sources=["https://kwork.ru/"])
        self.assertEqual(read_orders(self.database), [])
        order = read_orders(self.database, favorite_chat=42)[0]
        self.assertEqual(order["status"], "open")
        self.assertFalse(order["is_current"])
        card = bot.order_card(order)
        self.assertIn("Закрытие не подтверждено", card["text"])

    def test_stale_favorite_and_description_length_are_explicit(self):
        save_orders([("Python", "https://kwork.ru/projects/1", None, "x" * 5000, None, "unknown")], self.database)
        set_favorite(42, 1, True, self.database)
        connection = sqlite3.connect(self.database)
        connection.execute("UPDATE orders SET last_seen = '2020-01-01T00:00:00+00:00'")
        connection.commit()
        connection.close()
        self.assertEqual(read_orders(self.database), [])
        card = bot.order_card(read_orders(self.database, favorite_chat=42)[0])
        self.assertIn("Свежей проверки нет больше суток", card["text"])
        self.assertLess(len(card["text"]), 4096)
        self.assertIn("x" * 700 + "…", card["text"])
if __name__ == "__main__":
    unittest.main()