import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
import requests
import worker
from storage import notification_urls, notifications_paused, read_orders, read_status, save_orders, set_notifications_paused

class NotificationTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        self.database = Path(directory) / "orders.sqlite3"
        self.chat_id = 42
        self.enterContext(redirect_stdout(StringIO()))
        self.collect = self.enterContext(patch("worker.collect_orders", return_value=True))
        self.send = self.enterContext(patch("worker.telegram", return_value={"message_id": 1}))
        self.enterContext(patch("worker.sleep"))
        self.enterContext(patch("worker.requests.get", side_effect=self.api_response))

    def api_response(self, url, timeout):
        return Mock(json=lambda: read_orders(self.database))

    def add(self, number):
        url = f"https://kwork.ru/projects/{number}"
        save_orders([(f"Заказ {number}", url, "до 500 ₽")], self.database)
        return url

    def cycle(self):
        return worker.run_cycle("0:LOCAL_TEST", self.chat_id, self.database)

    def test_first_run_does_not_send_existing_orders(self):
        self.add(1)
        self.collect.side_effect = lambda database: bool(self.add(2))
        self.assertEqual(self.cycle(), 0)
        self.assertEqual(len(notification_urls(self.chat_id, self.database)), 2)
        self.send.assert_not_called()

    def test_restart_does_not_resend_confirmed_cards(self):
        self.add(1)
        self.cycle()
        new_url = self.add(2)
        self.assertEqual(self.cycle(), 1)
        card = self.send.call_args.args[2]
        self.assertEqual(card["chat_id"], self.chat_id)
        self.assertEqual(card["reply_markup"]["inline_keyboard"][0][0]["url"], new_url)
        self.assertEqual(read_status(self.database)["worker"]["sent"], 1)
        self.assertEqual(self.cycle(), 0)
        self.assertEqual(self.send.call_count, 1)
        self.assertEqual(read_status(self.database)["worker"]["sent"], 0)

    def test_failed_send_is_retried_without_resending_confirmed_cards(self):
        self.add(1)
        self.cycle()
        confirmed = self.add(2)
        pending = self.add(3)
        self.send.side_effect = [{"message_id": 1}, requests.ConnectionError("offline")]
        with self.assertRaises(requests.ConnectionError):
            self.cycle()
        marks = notification_urls(self.chat_id, self.database)
        self.assertIn(confirmed, marks)
        self.assertNotIn(pending, marks)
        self.assertEqual(read_status(self.database)["worker"]["sent"], 1)
        self.send.reset_mock(side_effect=True)
        self.assertEqual(self.cycle(), 1)
        self.assertEqual(self.send.call_count, 1)
        self.assertEqual(self.cycle(), 0)

    def test_failed_collection_does_not_initialise_notifications(self):
        self.add(1)
        self.collect.return_value = False
        self.assertEqual(self.cycle(), 0)
        self.send.assert_not_called()
        self.collect.return_value = True
        self.assertEqual(self.cycle(), 0)
        self.assertEqual(len(notification_urls(self.chat_id, self.database)), 1)

    def test_another_chat_gets_its_own_initial_list(self):
        self.add(1)
        self.cycle()
        self.add(2)
        self.assertEqual(worker.run_cycle("0:LOCAL_TEST", 43, self.database), 0)
        self.assertEqual(len(notification_urls(43, self.database)), 2)
        self.assertEqual(self.cycle(), 1)

    def test_disappeared_order_is_not_sent(self):
        self.add(1)
        self.cycle()
        self.add(2)
        save_orders([], self.database, checked_sources=["https://kwork.ru/"])
        self.assertEqual(self.cycle(), 0)
        self.send.assert_not_called()

    def test_pause_keeps_collecting_and_resume_sends_pending_once(self):
        self.add(1)
        self.cycle()
        set_notifications_paused(self.chat_id, True, self.database)
        self.collect.side_effect = lambda database: bool(self.add(2))
        self.assertEqual(self.cycle(), 0)
        self.assertTrue(notifications_paused(self.chat_id, self.database))
        self.assertEqual(len(read_orders(self.database)), 2)
        self.assertEqual(len(notification_urls(self.chat_id, self.database)), 1)
        self.assertEqual(read_status(self.database)["worker"]["phase"], "paused")
        self.send.assert_not_called()
        set_notifications_paused(self.chat_id, False, self.database)
        self.collect.side_effect = None
        self.assertEqual(self.cycle(), 1)
        self.assertEqual(self.cycle(), 0)
        self.assertEqual(self.send.call_count, 1)

    def test_pause_during_delivery_stops_before_next_card(self):
        self.add(1)
        self.cycle()
        first = self.add(2)
        pending = self.add(3)

        def send(*args):
            set_notifications_paused(self.chat_id, True, self.database)
            return {"message_id": 1}

        self.send.side_effect = send
        self.assertEqual(self.cycle(), 1)
        self.assertIn(first, notification_urls(self.chat_id, self.database))
        self.assertNotIn(pending, notification_urls(self.chat_id, self.database))
        self.assertEqual(self.send.call_count, 1)
        self.assertEqual(read_status(self.database)["worker"]["phase"], "paused")
        set_notifications_paused(self.chat_id, False, self.database)
        self.send.reset_mock(side_effect=True)
        self.assertEqual(self.cycle(), 1)
        self.assertEqual(self.send.call_count, 1)

    def test_pause_is_separate_for_each_chat(self):
        set_notifications_paused(self.chat_id, True, self.database)
        self.assertTrue(notifications_paused(self.chat_id, self.database))
        self.assertFalse(notifications_paused(43, self.database))
