import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
import requests
import api
import bot
from storage import favorite_urls, read_orders, save_orders, set_favorite

class FavoriteTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        self.database = Path(directory) / "orders.sqlite3"
        self.enterContext(patch.dict(bot.PAGES, {}, clear=True))
        self.enterContext(redirect_stdout(StringIO()))
        self.enterContext(patch("bot.sleep"))
        self.telegram = self.enterContext(patch("bot.telegram", return_value={"message_id": 1}))
        save_orders([("Python", "https://kwork.ru/projects/1", "500 ₽", "Скрипт", None, "open")], self.database)
        self.order = read_orders(self.database)[0]

    def query(self, action="add", sender=42):
        card = bot.order_card(self.order)
        return {"id": "favorite-test", "from": {"id": sender}, "data": f"fav:{action}:{self.order['id']}", "message": {"message_id": 1, "chat": {"id": 42, "type": "private"}, "reply_markup": card["reply_markup"]}}

    def post(self, url, params, timeout):
        set_favorite(42, int(url.rsplit("/", 1)[1]), params["favorite"], self.database)
        return Mock(json=lambda: {"favorite": params["favorite"]})

    def test_add_remove_are_idempotent_persistent_and_separate_by_chat(self):
        for chat_id in (42, 43):
            set_favorite(chat_id, self.order["id"], True, self.database)
            set_favorite(chat_id, self.order["id"], True, self.database)
            self.assertEqual(len(read_orders(self.database, favorite_chat=chat_id)), 1)
        set_favorite(42, self.order["id"], False, self.database)
        set_favorite(42, self.order["id"], False, self.database)
        self.assertEqual(favorite_urls(42, self.database), set())
        self.assertEqual(favorite_urls(43, self.database), {self.order["url"]})
        self.assertEqual(read_orders(self.database, favorite_chat=42), [])

    def test_unknown_order_does_not_create_a_favorite(self):
        with self.assertRaises(ValueError):
            set_favorite(42, 999, True, self.database)
        self.assertEqual(favorite_urls(42, self.database), set())

    def test_buttons_work_after_restart_and_do_not_send_duplicate_messages(self):
        with patch("bot.requests.post", side_effect=self.post):
            bot.favorite_button("0:LOCAL_TEST", self.query())
            bot.favorite_button("0:LOCAL_TEST", self.query())
            self.assertEqual(len(favorite_urls(42, self.database)), 1)
            edit = [call.args[2] for call in self.telegram.call_args_list if call.args[1] == "editMessageReplyMarkup"][-1]
            self.assertEqual(edit["reply_markup"]["inline_keyboard"][1][0]["text"], "Убрать из избранного")
            bot.favorite_button("0:LOCAL_TEST", self.query("remove"))
        self.assertEqual(favorite_urls(42, self.database), set())
        self.assertFalse(any(call.args[1] == "sendMessage" for call in self.telegram.call_args_list))

    def test_wrong_sender_and_api_failure_do_not_claim_success(self):
        with patch("bot.requests.post") as post:
            bot.favorite_button("0:LOCAL_TEST", self.query(sender=43))
            post.assert_not_called()
        with patch("bot.requests.post", side_effect=requests.ConnectionError):
            bot.favorite_button("0:LOCAL_TEST", self.query())
        self.assertIn("Не удалось изменить избранное", self.telegram.call_args.args[2]["text"])
        self.assertEqual(favorite_urls(42, self.database), set())

    def test_favorites_use_pagination_and_keep_favorite_buttons(self):
        save_orders([(f"Python {n}", f"https://kwork.ru/projects/{n}", None) for n in range(2, 8)], self.database)
        for order in read_orders(self.database):
            set_favorite(42, order["id"], True, self.database)
        orders = [{**order, "is_favorite": True} for order in read_orders(self.database, favorite_chat=42)]
        with patch("bot.requests.get", return_value=Mock(json=lambda: orders)) as get:
            replies = bot.reply_messages("Избранное", 42)
        self.assertTrue(get.call_args.args[0].endswith("/favorites/42"))
        self.assertEqual(len(replies), 5)
        bot.send_replies("0:LOCAL_TEST", 42, replies, bot.PAGES[42])
        query = self.query()
        query["data"] = replies[-1]["reply_markup"]["inline_keyboard"][-1][0]["callback_data"]
        query["message"]["reply_markup"] = replies[-1]["reply_markup"]
        bot.show_more("0:LOCAL_TEST", query)
        self.assertEqual(bot.PAGES[42]["index"], 7)
        edit = [call.args[2] for call in self.telegram.call_args_list if call.args[1] == "editMessageReplyMarkup"][-1]
        self.assertEqual(edit["reply_markup"]["inline_keyboard"][1][0]["text"], "Убрать из избранного")

    def test_favorite_change_preserves_more_button_and_page_position(self):
        save_orders([(f"Python {n}", f"https://kwork.ru/projects/{n}", None) for n in range(2, 8)], self.database)
        with patch("bot.requests.get", return_value=Mock(json=lambda: read_orders(self.database))):
            replies = bot.reply_messages("/latest", 42)
        bot.send_replies("0:LOCAL_TEST", 42, replies, bot.PAGES[42])
        last = replies[-1]
        query = self.query()
        query["data"] = last["reply_markup"]["inline_keyboard"][1][0]["callback_data"]
        query["message"]["reply_markup"] = last["reply_markup"]
        with patch("bot.requests.post", side_effect=self.post):
            bot.favorite_button("0:LOCAL_TEST", query)
        self.assertEqual(bot.PAGES[42]["index"], 5)
        edit = [call.args[2] for call in self.telegram.call_args_list if call.args[1] == "editMessageReplyMarkup"][-1]
        self.assertEqual(edit["reply_markup"]["inline_keyboard"][-1], last["reply_markup"]["inline_keyboard"][-1])
        self.assertEqual(edit["reply_markup"]["inline_keyboard"][1][0]["text"], "Убрать из избранного")
        self.assertEqual(bot.PAGES[42]["cards"][4]["reply_markup"]["inline_keyboard"][1][0]["text"], "Убрать из избранного")

    def test_api_missing_order_returns_404(self):
        with patch("api.set_favorite", side_effect=ValueError):
            with self.assertRaises(api.HTTPException) as error:
                api.change_favorite(42, 999, True)
        self.assertEqual(error.exception.status_code, 404)

    def test_polling_processes_favorite_buttons(self):
        polled = False
        def telegram(token, method, data):
            nonlocal polled
            if method == "getUpdates":
                if polled:
                    raise KeyboardInterrupt
                polled = True
                return [{"update_id": 1, "callback_query": self.query()}]
            return {"message_id": 1}
        self.telegram.side_effect = telegram
        with patch("bot.requests.post", side_effect=self.post):
            with self.assertRaises(KeyboardInterrupt):
                bot.run_bot("0:LOCAL_TEST")
        self.assertEqual(favorite_urls(42, self.database), {self.order["url"]})
if __name__ == "__main__":
    unittest.main()