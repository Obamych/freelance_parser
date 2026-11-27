import os
import shutil
import socket
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import run

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
        self.enterContext(patch.dict(os.environ, {"API_PORT": str(port)}))
        self.enterContext(patch("run.PROJECT", directory))
        original = run.start_process

        def start(filename):
            process = original(filename)
            self.processes.append((filename, process))
            return process

        self.enterContext(patch("run.start_process", side_effect=start))
        return directory

    def test_real_api_starts_before_clients_and_interrupt_stops_all(self):
        self.make_project()

        def interrupt(processes):
            self.assertEqual([name for name, process in processes], ["API", "Бот", "Автосбор"])
            self.assertTrue(all(process.poll() is None for name, process in processes))
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