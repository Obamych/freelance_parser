import re
import sys
from getpass import getpass
from time import sleep
import requests

ORDERS_URL = "http://127.0.0.1:8000/orders"

def orders_reply(query=""):
    try:
        response = requests.get(ORDERS_URL, params={"q": query, "limit": 5}, timeout=5)
        response.raise_for_status()
        orders = response.json()
        if not isinstance(orders, list):
            raise ValueError("Ожидался список заказов")
        if not orders:
            if query:
                return "По этому запросу заказы не найдены."
            return "Заказов пока нет. Сначала запусти parser.py."
        lines = []
        for order in orders[:5]:
            lines.append(f"{order['title'][:200]}\n{order['url']}")
        return "\n\n".join(lines)
    except (requests.RequestException, ValueError, KeyError, TypeError):
        return "Не удалось получить заказы. Проверь, что api.py запущен."

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
            if text == "/latest":
                reply = orders_reply()
            elif text == "/search" or text.startswith("/search "):
                query = text[7:].strip()
                if not query:
                    reply = "Добавь слово после команды. Например: /search Blender"
                elif len(query) > 100:
                    reply = "Запрос слишком длинный. Максимум 100 символов."
                else:
                    reply = orders_reply(query)
            elif text == "/start":
                reply = "/latest — последние заказы\n/search Blender — поиск по названию"
            else:
                continue
            try:
                telegram(token, "sendMessage", {
                    "chat_id": chat["id"], "text": reply,
                    "link_preview_options": {"is_disabled": True},
                })
            except (requests.RequestException, ValueError, KeyError):
                print("Не удалось отправить ответ в Telegram.")

if __name__ == "__main__":
    try:
        if sys.stdin.isatty():
            token = getpass("Токен учебного бота: ").strip()
        else:
            token = sys.stdin.readline().strip()
        if re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token):
            print("Бот запущен. Команды: /latest и /search.")
            run_bot(token)
        else:
            print("Введи токен, который выдал BotFather.")
    except KeyboardInterrupt:
        print("\nБот остановлен.")
