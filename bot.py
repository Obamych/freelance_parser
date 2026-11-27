import re
import sys
from datetime import datetime, timedelta, timezone
from getpass import getpass
from time import sleep
import requests
from config import get_api_url, get_bot_token

MOSCOW = timezone(timedelta(hours=3))
SOURCE_BUTTONS = {"Kwork": "kwork", "Freelance.ru": "freelance"}
MENU = {
    "keyboard": [
        [{"text": "Последние"}, {"text": "Поиск"}],
        [{"text": "Kwork"}, {"text": "Freelance.ru"}],
    ],
    "resize_keyboard": True,
}
SEARCH_HELP = "Добавь слово после команды. Например: /search Blender"

def format_found_at(value):
    try:
        found_at = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return "неизвестно"
    if found_at.tzinfo is None:
        return "неизвестно"
    return found_at.astimezone(MOSCOW).strftime("%d.%m.%Y %H:%M МСК")

def order_card(order):
    title = order["title"][:200]
    url = order["url"]
    if not isinstance(url, str) or not url.startswith(("https://", "http://")):
        raise ValueError("Некорректная ссылка на заказ")
    source = order.get("source") or "Неизвестен"
    budget = order.get("budget") or "не указан"
    found_at = format_found_at(order.get("found_at"))
    last_seen = format_found_at(order.get("last_seen"))
    return {
        "text": f"{title}\n\nИсточник: {source}\nБюджет: {budget}\nОбнаружено: {found_at}\nПоследний сбор: {last_seen}",
        "reply_markup": {"inline_keyboard": [[{"text": "Открыть заказ", "url": url}]]},
    }


def order_messages(query="", source=""):
    try:
        response = requests.get(get_api_url() + "/orders", params={"q": query, "source": source, "limit": 5}, timeout=5)
        response.raise_for_status()
        orders = response.json()
        if not isinstance(orders, list):
            raise ValueError("Ожидался список заказов")
        if not orders:
            if query:
                return [{"text": "По этому запросу заказы не найдены."}]
            if source:
                return [{"text": "С этой биржи пока нет заказов."}]
            return [{"text": "Свежих заказов пока нет. Запусти parser.py, чтобы обновить список."}]
        return [order_card(order) for order in orders[:5]]
    except (requests.RequestException, ValueError, KeyError, TypeError):
        return [{"text": "Не удалось получить заказы. Проверь, что api.py запущен."}]

def reply_messages(text):
    if text == "/start":
        return [{"text": "Выбери действие в меню.", "reply_markup": MENU}]
    if text in ("/latest", "Последние"):
        return order_messages()
    if text in SOURCE_BUTTONS:
        return order_messages(source=SOURCE_BUTTONS[text])
    if text in ("/search", "Поиск"):
        return [{"text": SEARCH_HELP}]
    if text.startswith("/search "):
        query = text[7:].strip()
        if not query:
            return [{"text": SEARCH_HELP}]
        if len(query) > 100:
            return [{"text": "Запрос слишком длинный. Максимум 100 символов."}]
        return order_messages(query)
    return []

def telegram(token, method, data):
    url = f"https://api.telegram.org/bot{token}/{method}"
    response = requests.post(url, json=data, timeout=30)
    response.raise_for_status()
    return response.json()["result"]

def run_bot(token):
    offset = 0
    while True:
        try:
            updates = telegram(token, "getUpdates", {
                "offset": offset, "timeout": 20, "allowed_updates": ["message"],
            })
        except requests.HTTPError as error:
            code = error.response.status_code
            if code in (401, 404):
                print("Telegram не принял токен бота.")
                return
            if code == 409:
                print("Telegram отклонил получение сообщений. Проверь, не запущен ли бот в другом окне.")
                return
            print(f"Telegram вернул ошибку HTTP {code}. Повтор через 5 секунд.")
            sleep(5)
            continue
        except (requests.RequestException, ValueError, KeyError):
            print("Не удалось получить сообщения. Повтор через 5 секунд.")
            sleep(5)
            continue
        for update in updates:
            offset = update["update_id"] + 1
            message = update.get("message", {})
            chat = message.get("chat", {})
            if chat.get("type") != "private":
                continue
            text = message.get("text", "").strip()
            for reply in reply_messages(text):
                try:
                    telegram(token, "sendMessage", {
                        "chat_id": chat["id"], **reply,
                        "link_preview_options": {"is_disabled": True},
                    })
                except (requests.RequestException, ValueError, KeyError):
                    print("Не удалось отправить ответ в Telegram.")
                    break
                sleep(1)

if __name__ == "__main__":
    try:
        get_api_url()
        token = get_bot_token()
        if not token:
            if sys.stdin.isatty():
                token = getpass("Токен учебного бота: ").strip()
            else:
                token = sys.stdin.readline().strip()
        if re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token):
            print("Бот запущен. Команды: /latest и /search.")
            run_bot(token)
        else:
            print("Укажи токен от BotFather в BOT_TOKEN в файле .env.")
    except ValueError as error:
        print(error)
    except KeyboardInterrupt:
        print("\nБот остановлен.")