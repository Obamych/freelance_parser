import os
import shutil
import socket
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import requests
import run
from storage import notifications_paused, save_orders, update_status

class RunTests(unittest.TestCase):
    def setUp(self):
        self.output = self.enterContext(redirect_stdout(StringIO()))
        self.enterContext(patch("run.get_bot_token", return_value="0:LOCAL_TEST"))
        self.enterContext(patch("run.get_chat_id", return_value=42))
        self.enterContext(patch("run.get_collect_interval", return_value=10))
        self.processes = []
        self.addCleanup(lambda: run.stop_processes(self.processes))

    def use_port(self, port):
        self.enterContext(patch("run.get_api_port", return_value=port))
        self.enterContext(patch("run.get_api_url", return_value=f"http://127.0.0.1:{port}"))

    def test_occupied_port_does_not_start_or_stop_processes(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            self.use_port(listener.getsockname()[1])
            with patch("run.start_process") as start:
                self.assertEqual(run.main(), 1)
            start.assert_not_called()
            self.assertGreater(listener.fileno(), -1)
            self.assertIn("занят", self.output.getvalue())

    def test_bad_settings_do_not_start_processes(self):
        with patch("run.get_api_port", side_effect=ValueError("Неверный порт")):
            with patch("run.start_process") as start:
                self.assertEqual(run.main(), 1)
            start.assert_not_called()

    def test_remote_api_is_rejected_for_combined_start(self):
        self.use_port(8000)
        with patch("run.get_api_url", return_value="https://example.invalid"):
            with patch("run.start_process") as start:
                self.assertEqual(run.main(), 1)
            start.assert_not_called()
        self.assertIn("API_URL", self.output.getvalue())

    def make_project(self):
        directory = Path(self.enterContext(TemporaryDirectory()))
        for filename in ("api.py", "config.py", "storage.py"):
            shutil.copyfile(run.PROJECT / filename, directory / filename)
        for filename in ("bot.py", "worker.py"):
            (directory / filename).write_text("from time import sleep\nsleep(120)\n", encoding="utf-8")
        with socket.socket() as connection:
            connection.bind(("127.0.0.1", 0))
            port = connection.getsockname()[1]
        self.use_port(port)
        self.enterContext(patch.dict(os.environ, {"API_PORT": str(port), "TELEGRAM_CHAT_ID": "42"}))
        self.enterContext(patch("run.PROJECT", directory))
        original = run.start_process
        def start(filename):
            process = original(filename)
            self.processes.append((filename, process))
            return process
        self.enterContext(patch("run.start_process", side_effect=start))
        return directory

    def test_real_api_starts_before_clients_and_interrupt_stops_all(self):
        directory = self.make_project()
        update_status("worker", {"phase": "waiting", "sent": 3}, directory / "orders.sqlite3")
        save_orders([("Python", "https://kwork.ru/projects/1", "500 ₽", "Написать скрипт", "2026-11-27T12:00:00", "open")], directory / "orders.sqlite3")
        def interrupt(processes):
            self.assertEqual([name for name, process in processes], ["API", "Бот", "Автосбор"])
            self.assertTrue(all(process.poll() is None for name, process in processes))
            with requests.Session() as session:
                session.trust_env = False
                response = session.get(run.get_api_url() + "/status", timeout=2)
                paused = session.post(run.get_api_url() + "/notifications/42", params={"paused": True}, timeout=2)
                forbidden = session.post(run.get_api_url() + "/notifications/43", params={"paused": False}, timeout=2)
                invalid = session.post(run.get_api_url() + "/notifications/42", params={"paused": "wrong"}, timeout=2)
                status = session.get(run.get_api_url() + "/status", timeout=2).json()
                filters = session.post(run.get_api_url() + "/notification-filters/42", params={"source": "kwork", "keywords": "Python, BLENDER, python"}, timeout=2)
                stored = session.get(run.get_api_url() + "/notifications/42", timeout=2).json()
                bad_source = session.post(run.get_api_url() + "/notification-filters/42", params={"source": "unknown"}, timeout=2)
                too_many = session.post(run.get_api_url() + "/notification-filters/42", params={"keywords": ",".join(str(n) for n in range(11))}, timeout=2)
                empty_words = session.post(run.get_api_url() + "/notification-filters/42", params={"keywords": ", , "}, timeout=2)
                too_long = session.post(run.get_api_url() + "/notification-filters/42", params={"keywords": "x" * 301}, timeout=2)
                missing = session.post(run.get_api_url() + "/notification-filters/42", timeout=2)
                unchanged = session.get(run.get_api_url() + "/notifications/42", timeout=2).json()
                clear = session.post(run.get_api_url() + "/notification-filters/42", params={"keywords": ""}, timeout=2)
                order = session.get(run.get_api_url() + "/orders", params={"chat_id": 42}, timeout=2).json()[0]
                favorite_url = run.get_api_url() + f"/favorites/42/{order['id']}"
                session.post(favorite_url, params={"favorite": True}, timeout=2).raise_for_status()
                session.post(favorite_url, params={"favorite": True}, timeout=2).raise_for_status()
                saved = session.get(run.get_api_url() + "/favorites/42", timeout=2).json()
                other = session.get(run.get_api_url() + "/favorites/43", timeout=2).json()
                favorite_feed = session.get(run.get_api_url() + "/orders", params={"chat_id": 42}, timeout=2).json()
                save_orders([], directory / "orders.sqlite3", checked_sources=["https://kwork.ru/"])
                absent_feed = session.get(run.get_api_url() + "/orders", timeout=2).json()
                absent_saved = session.get(run.get_api_url() + "/favorites/42", timeout=2).json()
                missing_order = session.post(run.get_api_url() + "/favorites/42/999", params={"favorite": True}, timeout=2)
                session.post(favorite_url, params={"favorite": False}, timeout=2).raise_for_status()
                session.post(favorite_url, params={"favorite": False}, timeout=2).raise_for_status()
                removed = session.get(run.get_api_url() + "/favorites/42", timeout=2).json()
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["worker"]["sent"], 3)
            self.assertEqual(paused.status_code, 200)
            self.assertEqual(forbidden.status_code, 403)
            self.assertEqual(invalid.status_code, 422)
            self.assertTrue(status["notifications"]["paused"])
            self.assertTrue(notifications_paused(42, directory / "orders.sqlite3"))
            self.assertEqual(filters.status_code, 200)
            self.assertEqual(stored, {"paused": True, "source": "kwork", "keywords": "Python, BLENDER"})
            self.assertEqual(bad_source.status_code, 422)
            self.assertEqual(too_many.status_code, 400)
            self.assertEqual(empty_words.status_code, 400)
            self.assertEqual(too_long.status_code, 422)
            self.assertEqual(missing.status_code, 400)
            self.assertEqual(unchanged, stored)
            self.assertEqual(clear.json(), {"paused": True, "source": "kwork", "keywords": ""})
            self.assertEqual(len(saved), 1)
            self.assertEqual(saved[0]["description"], "Написать скрипт")
            self.assertEqual(saved[0]["published_at"], "2026-11-27T12:00:00")
            self.assertTrue(saved[0]["is_favorite"])
            self.assertEqual(other, [])
            self.assertTrue(favorite_feed[0]["is_favorite"])
            self.assertEqual(absent_feed, [])
            self.assertFalse(absent_saved[0]["is_current"])
            self.assertEqual(missing_order.status_code, 404)
            self.assertEqual(removed, [])
            raise KeyboardInterrupt
        with patch("run.watch_processes", side_effect=interrupt):
            self.assertEqual(run.main(), 0)
        self.assertEqual(len(self.processes), 3)
        self.assertTrue(all(process.poll() is not None for name, process in self.processes))

    def test_failed_api_does_not_start_clients(self):
        directory = self.make_project()
        (directory / "api.py").write_text("raise SystemExit(7)\n", encoding="utf-8")
        self.assertEqual(run.main(), 1)
        self.assertEqual(len(self.processes), 1)
        self.assertEqual(self.processes[0][1].returncode, 7)

    def test_client_exit_stops_api_and_other_client(self):
        directory = self.make_project()
        (directory / "bot.py").write_text("raise SystemExit(9)\n", encoding="utf-8")
        self.assertEqual(run.main(), 1)
        self.assertEqual(len(self.processes), 3)
        self.assertTrue(all(process.poll() is not None for name, process in self.processes))
        self.assertIn("Бот завершился (код 9)", self.output.getvalue())
if __name__ == "__main__":
    unittest.main()