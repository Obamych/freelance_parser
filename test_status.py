import sqlite3
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
import requests
import api
import bot
import parser
import worker
from storage import read_status, update_status

class StatusTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        self.database = Path(directory) / "orders.sqlite3"
        self.enterContext(redirect_stdout(StringIO()))

    def collect(self, kwork_error=False):
        def get(url, timeout):
            if url == parser.KWORK_URL and kwork_error:
                response = Mock(status_code=401)
                raise requests.HTTPError(response=response)
            return Mock(text="page")

        with patch("parser.requests.get", side_effect=get):
            with patch("parser.parse_kwork", return_value=[("Заказ", "https://kwork.ru/projects/1", None)]):
                with patch("parser.parse_freelance", return_value=[]):
                    return parser.main(self.database)

    def status_reply(self):
        with patch("bot.requests.get", return_value=Mock(json=lambda: read_status(self.database))):
            return bot.reply_messages("/status")[0]["text"]

    def test_old_database_without_status_is_supported(self):
        self.assertEqual(read_status(self.database), {})
        with sqlite3.connect(self.database) as connection:
            connection.execute("CREATE TABLE orders (url TEXT)")
        connection.close()
        self.assertEqual(read_status(self.database), {})
        self.assertIn("Данных о сборе пока нет", self.status_reply())

    def test_collection_is_saved_and_new_count_survives_restart(self):
        self.assertTrue(self.collect())
        first = read_status(self.database)["collection"]
        self.assertEqual(first["added"], 1)
        self.assertEqual(first["sources"]["Kwork"]["found"], 1)
        self.assertEqual(first["sources"]["Freelance.ru"]["found"], 0)
        self.assertTrue(self.collect())
        self.assertEqual(read_status(self.database)["collection"]["added"], 0)
        self.assertIn("Kwork: получено 1", self.status_reply())

    def test_partial_failure_keeps_previous_success_time(self):
        self.collect()
        previous = read_status(self.database)["collection"]["sources"]["Kwork"]["last_success"]
        self.assertTrue(self.collect(kwork_error=True))
        source = read_status(self.database)["collection"]["sources"]["Kwork"]
        self.assertEqual(source["last_success"], previous)
        self.assertIsNone(source["found"])
        self.assertEqual(source["error"], "Ошибка HTTP 401")
        self.assertIn("Kwork: Ошибка HTTP 401", self.status_reply())

    def test_total_failure_is_recorded_without_a_fake_success(self):
        with patch("parser.requests.get", side_effect=requests.ConnectionError):
            self.assertFalse(parser.main(self.database))
        collection = read_status(self.database)["collection"]
        self.assertIsNone(collection["added"])
        self.assertTrue(all(source["last_success"] is None for source in collection["sources"].values()))
        self.assertIn("Не удалось загрузить страницу", self.status_reply())

    def test_worker_error_and_next_attempt_are_saved(self):
        def save(name, values, database=None):
            update_status(name, values, self.database)

        with patch("worker.update_status", side_effect=save):
            with patch("worker.run_cycle", side_effect=requests.ConnectionError):
                with patch("worker.sleep", side_effect=KeyboardInterrupt):
                    with self.assertRaises(KeyboardInterrupt):
                        worker.run_worker("0:LOCAL_TEST", 42, 10)
        state = read_status(self.database)["worker"]
        self.assertEqual(state["phase"], "waiting")
        self.assertIn("Ошибка соединения", state["error"])
        self.assertIsNotNone(state["next_run"])

    def test_status_shows_moscow_time_and_overdue_schedule(self):
        update_status("worker", {
            "phase": "waiting", "sent": 2, "error": None,
            "next_run": "2020-01-01T10:00:00+00:00",
        }, self.database)
        text = self.status_reply()
        self.assertIn("01.01.2020 13:00 МСК", text)
        self.assertIn("Отправлено уведомлений за этот цикл: 2", text)
        self.assertIn("Время следующего сбора уже прошло", text)
        with patch("bot.status_message", return_value={"text": text}):
            self.assertEqual(bot.reply_messages("Статус"), [{"text": text}])

    def test_unavailable_api_has_a_readable_reply(self):
        with patch("bot.requests.get", side_effect=requests.ConnectionError):
            self.assertIn("Не удалось получить состояние", bot.status_message()["text"])

    def test_api_returns_503_when_database_is_unreadable(self):
        with patch("api.read_status", side_effect=sqlite3.DatabaseError):
            with self.assertRaises(api.HTTPException) as error:
                api.get_status()
        self.assertEqual(error.exception.status_code, 503)

if __name__ == "__main__":
    unittest.main()