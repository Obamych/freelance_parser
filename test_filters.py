import sqlite3
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
import api
import bot
import worker
from storage import (
    get_notification_settings, notification_urls, read_orders, save_orders,
    set_notification_filters, set_notifications_paused,
)

class FilterTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        self.database = Path(directory) / "orders.sqlite3"
        self.enterContext(redirect_stdout(StringIO()))

    def test_old_pause_setting_survives_new_columns_and_restart(self):
        connection = sqlite3.connect(self.database)
        connection.execute("CREATE TABLE notification_settings (chat_id INTEGER PRIMARY KEY, paused INTEGER NOT NULL DEFAULT 0)")
        connection.execute("INSERT INTO notification_settings VALUES (42, 1)")
        connection.commit()
        connection.close()
        self.assertEqual(get_notification_settings(42, self.database), {"paused": True, "source": "all", "keywords": ""})
        set_notification_filters(42, source="kwork", keywords=" Blender, python, BLENDER, , 3D  модель ", database=self.database)
        saved = get_notification_settings(42, self.database)
        self.assertEqual(saved, {"paused": True, "source": "kwork", "keywords": "Blender, python, 3D модель"})
        set_notifications_paused(42, False, self.database)
        self.assertEqual(get_notification_settings(42, self.database)["keywords"], saved["keywords"])
        set_notification_filters(42, keywords="", database=self.database)
        self.assertEqual(get_notification_settings(42, self.database), {"paused": False, "source": "kwork", "keywords": ""})
        self.assertEqual(get_notification_settings(43, self.database), {"paused": False, "source": "all", "keywords": ""})

    def test_invalid_filters_leave_settings_unchanged(self):
        set_notification_filters(42, source="kwork", keywords="Python", database=self.database)
        for values in ({"source": "unknown"}, {"keywords": "x" * 301}, {"keywords": ", , "}, {"source": "all", "keywords": ",".join(str(n) for n in range(11))}):
            with self.assertRaises(ValueError):
                set_notification_filters(42, database=self.database, **values)
            self.assertEqual(get_notification_settings(42, self.database), {"paused": False, "source": "kwork", "keywords": "Python"})

    def test_source_and_any_keyword_must_match(self):
        settings = {"source": "kwork", "keywords": "блендер, Python, 3D модель"}
        for title in ("Нужен ПАЙТОН и PYTHON", "Сцена в БЛЕНДЕР", "Создать 3d модель"):
            self.assertTrue(worker.matches_notifications({"url": "https://kwork.ru/projects/1", "title": title}, settings))
        self.assertFalse(worker.matches_notifications({"url": "https://freelance.ru/task/view/1", "title": "Python"}, settings))
        self.assertFalse(worker.matches_notifications({"url": "https://kwork.ru/projects/1", "title": "Дизайн сайта"}, settings))
        self.assertTrue(worker.matches_notifications({"url": "https://freelance.ru/task/view/1", "title": "Дизайн"}, {"source": "all", "keywords": ""}))

    def prepare_worker(self):
        self.enterContext(patch("worker.collect_orders", return_value=True))
        self.enterContext(patch("worker.requests.get", side_effect=lambda url, timeout: Mock(json=lambda: read_orders(self.database))))
        self.enterContext(patch("worker.sleep"))
        send = self.enterContext(patch("worker.telegram", return_value={"message_id": 1}))
        save_orders([], self.database)
        worker.run_cycle("0:LOCAL_TEST", 42, self.database)
        return send

    def test_changing_filters_while_paused_does_not_resume_delivery(self):
        send = self.prepare_worker()
        set_notifications_paused(42, True, self.database)
        set_notification_filters(42, source="kwork", keywords="Python", database=self.database)
        save_orders([
            ("Python", "https://kwork.ru/projects/1", None),
            ("Дизайн", "https://kwork.ru/projects/2", None),
            ("Python", "https://freelance.ru/task/view/3", None),
        ], self.database)
        self.assertEqual(worker.run_cycle("0:LOCAL_TEST", 42, self.database), 0)
        send.assert_not_called()
        set_notifications_paused(42, False, self.database)
        self.assertEqual(worker.run_cycle("0:LOCAL_TEST", 42, self.database), 1)
        self.assertEqual(notification_urls(42, self.database), {"https://kwork.ru/projects/1"})
        self.assertEqual(get_notification_settings(42, self.database), {"paused": False, "source": "kwork", "keywords": "Python"})
        self.assertEqual(worker.run_cycle("0:LOCAL_TEST", 42, self.database), 0)
        self.assertEqual(send.call_count, 1)

    def test_filter_change_during_delivery_applies_before_next_card(self):
        send = self.prepare_worker()
        save_orders([
            ("Python", "https://kwork.ru/projects/1", None),
            ("Python", "https://freelance.ru/task/view/2", None),
        ], self.database)

        def change_filter(*args):
            set_notification_filters(42, source="kwork", database=self.database)
            return {"message_id": 1}

        send.side_effect = change_filter
        self.assertEqual(worker.run_cycle("0:LOCAL_TEST", 42, self.database), 1)
        self.assertEqual(send.call_count, 1)
        set_notification_filters(42, source="all", database=self.database)
        send.reset_mock(side_effect=True)
        self.assertEqual(worker.run_cycle("0:LOCAL_TEST", 42, self.database), 1)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(notification_urls(42, self.database), {"https://kwork.ru/projects/1", "https://freelance.ru/task/view/2"})

    def test_delivery_uses_filters_and_can_send_newly_matching_pending_orders(self):
        def orders_response(url, timeout):
            return Mock(json=lambda: read_orders(self.database))

        with patch("worker.collect_orders", return_value=True):
            with patch("worker.requests.get", side_effect=orders_response):
                with patch("worker.telegram", return_value={"message_id": 1}) as send:
                    with patch("worker.sleep"):
                        save_orders([], self.database)
                        worker.run_cycle("0:LOCAL_TEST", 42, self.database)
                        save_orders([
                            ("Скрипт PYTHON", "https://kwork.ru/projects/1", None),
                            ("Дизайн", "https://kwork.ru/projects/2", None),
                            ("Скрипт Python", "https://freelance.ru/task/view/3", None),
                        ], self.database)
                        set_notification_filters(42, source="kwork", keywords="python", database=self.database)
                        self.assertEqual(worker.run_cycle("0:LOCAL_TEST", 42, self.database), 1)
                        self.assertEqual(notification_urls(42, self.database), {"https://kwork.ru/projects/1"})
                        set_notification_filters(42, source="all", database=self.database)
                        self.assertEqual(worker.run_cycle("0:LOCAL_TEST", 42, self.database), 1)
                        set_notification_filters(42, keywords="", database=self.database)
                        self.assertEqual(worker.run_cycle("0:LOCAL_TEST", 42, self.database), 1)
                        self.assertEqual(worker.run_cycle("0:LOCAL_TEST", 42, self.database), 0)
                        self.assertEqual(send.call_count, 3)
                        with patch("api.read_orders", return_value=read_orders(self.database)):
                            self.assertEqual(len(api.find_orders("", "")), 3)

    def test_bot_settings_buttons_and_commands_use_api(self):
        settings = {"paused": False, "source": "kwork", "keywords": "Python"}
        with patch("bot.get_chat_id", return_value=42):
            with patch("bot.requests.get", return_value=Mock(status_code=200, json=lambda: settings)):
                reply = bot.reply_messages("Настройки", 42)[0]
                self.assertIn("Биржи: Kwork", reply["text"])
                self.assertIn("Ключевые слова: Python", reply["text"])
                self.assertEqual(reply["reply_markup"], bot.SETTINGS_MENU)
            with patch("bot.requests.post", return_value=Mock(status_code=200, json=lambda: settings)) as post:
                bot.reply_messages("Только Freelance.ru", 42)
                self.assertEqual(post.call_args.kwargs["params"], {"source": "freelance"})
                bot.reply_messages("/sources all", 42)
                self.assertEqual(post.call_args.kwargs["params"], {"source": "all"})
                bot.reply_messages("/keywords Blender, Python", 42)
                self.assertEqual(post.call_args.kwargs["params"], {"keywords": "Blender, Python"})
                bot.reply_messages("Сбросить слова", 42)
                self.assertEqual(post.call_args.kwargs["params"], {"keywords": ""})

    def test_other_chat_cannot_change_filters(self):
        with patch("bot.get_chat_id", return_value=42):
            with patch("bot.requests.post") as post:
                self.assertIn("Настройки доступны", bot.reply_messages("/keywords Python", 43)[0]["text"])
            post.assert_not_called()
        with patch("api.get_chat_id", return_value=42):
            with patch("api.set_notification_filters") as save:
                with self.assertRaises(api.HTTPException) as error:
                    api.change_notification_filters(43, "all", "")
            save.assert_not_called()
        self.assertEqual(error.exception.status_code, 403)

    def test_bad_keyword_input_shows_help_or_validation_error(self):
        self.assertIn("/keywords Blender", bot.reply_messages("Ключевые слова", 42)[0]["text"])
        self.assertIn("300", bot.reply_messages("/keywords " + "x" * 301, 42)[0]["text"])
        with patch("bot.get_chat_id", return_value=42):
            with patch("bot.requests.post", return_value=Mock(status_code=400, json=lambda: {"detail": "Не больше 10 фраз"})):
                self.assertEqual(bot.reply_messages("/keywords abc", 42)[0]["text"], "Не больше 10 фраз")

if __name__ == "__main__":
    unittest.main()