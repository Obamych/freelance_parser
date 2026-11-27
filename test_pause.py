import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
import requests
import api
import bot
from storage import notifications_paused, set_notifications_paused

class PauseTests(unittest.TestCase):
    def test_old_database_and_first_run_have_notifications_enabled(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "orders.sqlite3"
            self.assertFalse(notifications_paused(42, database))
            self.assertFalse(database.exists())
            connection = sqlite3.connect(database)
            connection.execute("CREATE TABLE orders (url TEXT)")
            connection.close()
            self.assertFalse(notifications_paused(42, database))
            set_notifications_paused(42, True, database)
            self.assertTrue(notifications_paused(42, database))
            set_notifications_paused(42, True, database)
            set_notifications_paused(42, False, database)
            self.assertFalse(notifications_paused(42, database))

    def test_command_and_button_send_settings_through_api(self):
        with patch("bot.get_chat_id", return_value=42):
            with patch("bot.requests.post", return_value=Mock(json=lambda: {"paused": True})) as post:
                for text in ("/pause", "Пауза"):
                    self.assertIn("Уведомления на паузе", bot.reply_messages(text, 42)[0]["text"])
                self.assertEqual(post.call_args.kwargs["params"], {"paused": True})
            with patch("bot.requests.post", return_value=Mock(json=lambda: {"paused": False})) as post:
                for text in ("/resume", "Возобновить"):
                    self.assertIn("Уведомления включены", bot.reply_messages(text, 42)[0]["text"])
                self.assertEqual(post.call_args.kwargs["params"], {"paused": False})

    def test_another_chat_cannot_pause_owner_notifications(self):
        with patch("bot.get_chat_id", return_value=42):
            with patch("bot.requests.post") as post:
                reply = bot.reply_messages("/pause", 43)
            post.assert_not_called()
        self.assertIn("только из чата", reply[0]["text"])
        with patch("api.get_chat_id", return_value=42):
            with patch("api.set_notifications_paused") as save:
                with self.assertRaises(api.HTTPException) as error:
                    api.change_notifications(43, True)
            save.assert_not_called()
        self.assertEqual(error.exception.status_code, 403)

    def test_unavailable_api_does_not_claim_pause_was_saved(self):
        with patch("bot.get_chat_id", return_value=42):
            with patch("bot.requests.post", side_effect=requests.ConnectionError):
                reply = bot.reply_messages("/pause", 42)
        self.assertIn("Не удалось изменить", reply[0]["text"])

    def test_status_shows_pause_without_collection_history(self):
        data = {"notifications": {"paused": True}}
        with patch("bot.requests.get", return_value=Mock(json=lambda: data)):
            text = bot.status_message()["text"]
        self.assertIn("Уведомления: на паузе, сбор продолжается", text)
        self.assertIn("Данных о сборе пока нет", text)

    def test_database_failure_returns_503(self):
        with patch("api.get_chat_id", return_value=42):
            with patch("api.set_notifications_paused", side_effect=sqlite3.DatabaseError):
                with self.assertRaises(api.HTTPException) as error:
                    api.change_notifications(42, True)
        self.assertEqual(error.exception.status_code, 503)

if __name__ == "__main__":
    unittest.main()