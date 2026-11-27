import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import Mock, patch
import requests
import bot

class PaginationTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(bot.PAGES, {}, clear=True))
        self.enterContext(patch("bot.sleep"))
        self.enterContext(redirect_stdout(StringIO()))
        self.orders = [{
            "title": f"Python {number}", "url": f"https://kwork.ru/projects/{number}",
            "source": "Kwork", "budget": "500 ₽",
        } for number in range(1, 13)]
        self.get = self.enterContext(patch("bot.requests.get", return_value=Mock(json=lambda: self.orders)))
        self.send = self.enterContext(patch("bot.telegram", return_value={"message_id": 100}))

    def first_page(self, text="/latest", chat_id=42):
        replies = bot.reply_messages(text, chat_id)
        page = bot.PAGES.get(chat_id)
        bot.send_replies("0:LOCAL_TEST", chat_id, replies, page)
        return replies

    def callback(self, card, chat_id=42):
        button = card["reply_markup"]["inline_keyboard"][-1][0]
        return {
            "id": "callback-test", "data": button["callback_data"], "from": {"id": chat_id},
            "message": {"message_id": 100, "chat": {"id": chat_id, "type": "private"}, "reply_markup": card["reply_markup"]},
        }

    def sent_cards(self):
        return [call.args[2] for call in self.send.call_args_list if call.args[1] == "sendMessage"]

    def test_next_pages_end_and_repeated_click_do_not_repeat_cards(self):
        first = self.first_page()
        self.assertEqual(len(first), 5)
        self.assertEqual(first[-1]["reply_markup"]["inline_keyboard"][-1][0]["text"], "Ещё")
        callback = self.callback(first[-1])
        bot.show_more("0:LOCAL_TEST", callback)
        self.assertEqual(len(self.sent_cards()), 10)
        second_last = self.sent_cards()[-1]
        bot.show_more("0:LOCAL_TEST", callback)
        self.assertEqual(len(self.sent_cards()), 10)
        bot.show_more("0:LOCAL_TEST", self.callback(second_last))
        cards = self.sent_cards()
        self.assertEqual(len(cards), 12)
        self.assertIn("Заказы в этом списке закончились", cards[-1]["text"])
        self.assertEqual(len(cards[-1]["reply_markup"]["inline_keyboard"]), 1)
        links = [card["reply_markup"]["inline_keyboard"][0][0]["url"] for card in cards]
        self.assertEqual(len(set(links)), 12)
        edits = [call.args[2] for call in self.send.call_args_list if call.args[1] == "editMessageReplyMarkup"]
        self.assertEqual(edits[0]["reply_markup"], {"inline_keyboard": [first[-1]["reply_markup"]["inline_keyboard"][0]]})

    def test_exact_page_boundaries_do_not_offer_an_empty_page(self):
        for count in (1, 4, 5, 6, 9, 10, 11, 12):
            with self.subTest(count=count):
                self.send.reset_mock()
                self.orders = [{"title": f"Заказ {number}", "url": f"https://kwork.ru/projects/{number}"} for number in range(1, count + 1)]
                last = self.first_page()[-1]
                while bot.PAGES[42]["index"] < count:
                    bot.show_more("0:LOCAL_TEST", self.callback(last))
                    last = self.sent_cards()[-1]
                self.assertEqual(len(self.sent_cards()), count)
                self.assertEqual(len(last["reply_markup"]["inline_keyboard"]), 1)
                self.assertIn("Заказы в этом списке закончились", last["text"])

    def test_search_source_and_snapshot_survive_later_api_changes(self):
        first = self.first_page("/search Python")
        self.assertEqual(self.get.call_args.kwargs["params"], {"q": "Python", "source": "", "chat_id": 42})
        self.get.return_value = Mock(json=lambda: list(reversed(self.orders)))
        bot.show_more("0:LOCAL_TEST", self.callback(first[-1]))
        self.get.assert_called_once()
        self.assertTrue(self.sent_cards()[5]["text"].startswith("Python 6\n"))
        self.first_page("Freelance.ru")
        self.assertEqual(self.get.call_args.kwargs["params"], {"q": "", "source": "freelance", "chat_id": 42})

    def test_new_search_invalidates_old_buttons_and_starts_at_first_result(self):
        first = self.first_page()
        old = self.callback(first[-1])
        self.orders = [{"title": "Blender", "url": "https://kwork.ru/projects/99"}]
        new = self.first_page("/search Blender")
        self.assertTrue(new[0]["text"].startswith("Blender\n"))
        self.send.reset_mock()
        bot.show_more("0:LOCAL_TEST", old)
        self.assertEqual(self.sent_cards(), [])
        self.assertIn("устарел", self.send.call_args.args[2]["text"])

    def test_expired_or_restarted_session_has_a_readable_answer(self):
        first = self.first_page()
        callback = self.callback(first[-1])
        bot.PAGES[42]["expires"] = -1
        self.send.reset_mock()
        bot.show_more("0:LOCAL_TEST", callback)
        self.assertIn("устарел", self.send.call_args.args[2]["text"])
        bot.PAGES.clear()
        bot.show_more("0:LOCAL_TEST", callback)
        self.assertEqual(self.sent_cards(), [])

    def test_chats_are_separate_and_wrong_sender_cannot_use_button(self):
        first = self.first_page(chat_id=42)
        self.first_page(chat_id=43)
        self.send.reset_mock()
        wrong_chat = self.callback(first[-1], 43)
        bot.show_more("0:LOCAL_TEST", wrong_chat)
        wrong_sender = self.callback(first[-1])
        wrong_sender["from"]["id"] = 43
        bot.show_more("0:LOCAL_TEST", wrong_sender)
        self.assertEqual(self.sent_cards(), [])
        self.assertEqual(bot.PAGES[42]["index"], 5)
        self.assertEqual(bot.PAGES[43]["index"], 5)

    def test_failed_delivery_can_resume_without_skipping_confirmed_cards(self):
        first = self.first_page()
        callback = self.callback(first[-1])
        delivered = []
        failed = False
        def send(token, method, data):
            nonlocal failed
            if method == "sendMessage":
                if len(delivered) == 2 and not failed:
                    failed = True
                    raise requests.ConnectionError
                delivered.append(data["text"].split("\n")[0])
            return {"message_id": 100}
        self.send.side_effect = send
        bot.show_more("0:LOCAL_TEST", callback)
        self.assertEqual(bot.PAGES[42]["index"], 7)
        bot.show_more("0:LOCAL_TEST", callback)
        self.assertEqual(delivered, [f"Python {number}" for number in range(6, 11)])
        self.assertEqual(bot.PAGES[42]["index"], 10)

    def test_empty_results_and_api_failure_clear_old_list(self):
        self.first_page()
        self.get.return_value = Mock(json=lambda: [])
        self.assertIn("не найдены", bot.reply_messages("/search missing", 42)[0]["text"])
        self.assertNotIn(42, bot.PAGES)
        self.get.side_effect = requests.ConnectionError
        self.assertIn("Не удалось получить", bot.reply_messages("/latest", 42)[0]["text"])

    def test_invalid_callback_and_markup_edit_failure_do_not_break_browsing(self):
        first = self.first_page()
        callback = self.callback(first[-1])
        callback["data"] = f"more:{bot.PAGES[42]['id']}:999"
        self.send.reset_mock()
        bot.show_more("0:LOCAL_TEST", callback)
        self.assertEqual(self.sent_cards(), [])
        def send(token, method, data):
            if method == "editMessageReplyMarkup":
                raise requests.ConnectionError
            return {"message_id": 100}
        self.send.side_effect = send
        callback = self.callback(first[-1])
        bot.show_more("0:LOCAL_TEST", callback)
        self.assertEqual(bot.PAGES[42]["index"], 10)
        bot.show_more("0:LOCAL_TEST", callback)
        self.assertEqual(len(self.sent_cards()), 5)

    def test_polling_handles_button_updates(self):
        def updates(token, method, data):
            if method != "getUpdates":
                return {"message_id": 100}
            self.assertIn("callback_query", data["allowed_updates"])
            if 42 not in bot.PAGES:
                return [{"update_id": 1, "message": {"chat": {"id": 42, "type": "private"}, "text": "/latest"}}]
            if bot.PAGES[42]["index"] == 5:
                card = self.sent_cards()[-1]
                return [{"update_id": 2, "callback_query": self.callback(card)}]
            raise KeyboardInterrupt
        self.send.side_effect = updates
        with self.assertRaises(KeyboardInterrupt):
            bot.run_bot("0:LOCAL_TEST")
        self.assertEqual(len(self.sent_cards()), 10)
if __name__ == "__main__":
    unittest.main()