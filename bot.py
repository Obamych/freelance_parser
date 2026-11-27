import re
import sys
from datetime import datetime, timedelta, timezone
from getpass import getpass
from time import sleep
import requests
from config import get_api_url, get_bot_token, get_chat_id

MOSCOW = timezone(timedelta(hours=3))
SOURCE_BUTTONS = {"Kwork": "kwork", "Freelance.ru": "freelance"}
NOTIFICATION_SOURCES = {"Только Kwork": "kwork", "Только Freelance.ru": "freelance", "Обе биржи": "all"}
SOURCE_NAMES = {"all": "обе биржи", "kwork": "Kwork", "freelance": "Freelance.ru"}
MENU = {
    "keyboard": [
        [{"text": "Последние"}, {"text": "Поиск"}],
        [{"text": "Kwork"}, {"text": "Freelance.ru"}],
        [{"text": "Статус"}, {"text": "Настройки"}],
        [{"text": "Пауза"}, {"text": "Возобновить"}],
    ],
    "resize_keyboard": True,
}
SETTINGS_MENU = {
    "keyboard": [
        [{"text": "Только Kwork"}, {"text": "Только Freelance.ru"}],
        [{"text": "Обе биржи"}, {"text": "Ключевые слова"}],
        [{"text": "Сбросить слова"}, {"text": "Назад"}],
    ],
    "resize_keyboard": True,
}
SEARCH_HELP = "Добавь слово после команды. Например: /search Blender"
KEYWORDS_HELP = "Напиши /keywords Blender, Python. Совпадение хотя бы одного слова или фразы ищется в названии, регистр не важен. До 10 фраз, не больше 300 символов. Для очистки нажми «Сбросить слова» или отправь /keywords_clear."

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

def status_message():
    try:
        response = requests.get(get_api_url() + "/status", timeout=5)
        response.raise_for_status()
        data = response.json()
        collection = data.get("collection", {})
        worker = data.get("worker", {})
        notifications = data.get("notifications", {})
        lines = ["Состояние сбора"]
        if "paused" in notifications:
            state = "на паузе, сбор продолжается" if notifications["paused"] else "включены"
            lines.append("Уведомления: " + state)
        if "source" in notifications:
            lines.append("Биржи для уведомлений: " + SOURCE_NAMES[notifications["source"]])
            lines.append("Ключевые слова: " + (notifications.get("keywords") or "любые"))
        if not collection and not worker:
            lines.append("Данных о сборе пока нет. Они появятся после запуска автосбора.")
            return {"text": "\n".join(lines)}
        if collection:
            lines.append("Последний итог: " + format_found_at(collection.get("updated_at")))
            for name, source in collection.get("sources", {}).items():
                if source.get("error"):
                    lines.append(f"\n{name}: {source['error']}")
                else:
                    lines.append(f"\n{name}: получено {source['found']}")
                lines.append("Последний успешный сбор: " + format_found_at(source.get("last_success")))
            added = collection.get("added")
            if added is not None:
                lines.append(f"\nДобавлено новых в базу за сбор: {added}")
        if worker:
            phases = {
                "collecting": "идёт сбор", "sending": "отправка уведомлений",
                "finished": "цикл завершён", "waiting": "ожидание следующего сбора",
                "error": "ошибка", "stopped": "остановлен",
                "paused": "рассылка приостановлена",
            }
            lines.append("\nПоследняя запись автосбора: " + phases.get(worker.get("phase"), "неизвестно"))
            lines.append("Обновлено: " + format_found_at(worker.get("updated_at")))
            lines.append(f"Отправлено уведомлений за этот цикл: {worker.get('sent', 0)}")
            if worker.get("error"):
                lines.append("Ошибка: " + worker["error"])
            if worker.get("next_run"):
                lines.append("Следующий сбор по плану: " + format_found_at(worker["next_run"]))
                if datetime.fromisoformat(worker["next_run"]) < datetime.now(timezone.utc):
                    lines.append("Время следующего сбора уже прошло. Проверь окно run.py.")
        return {"text": "\n".join(lines)}
    except (requests.RequestException, ValueError, KeyError, TypeError, AttributeError):
        return {"text": "Не удалось получить состояние сбора. Проверь, что api.py запущен."}

def notification_reply(chat_id, paused):
    try:
        if chat_id != get_chat_id():
            return {"text": "Управлять уведомлениями можно только из чата, указанного в TELEGRAM_CHAT_ID."}
        response = requests.post(
            get_api_url() + f"/notifications/{chat_id}", params={"paused": paused}, timeout=5,
        )
        response.raise_for_status()
        if response.json().get("paused") is not paused:
            raise ValueError("API не подтвердил настройку")
    except (requests.RequestException, ValueError, AttributeError):
        return {"text": "Не удалось изменить уведомления. Проверь API и TELEGRAM_CHAT_ID в .env."}
    if paused:
        text = "Уведомления на паузе. Сбор заказов продолжается. Для включения нажми «Возобновить»."
    else:
        text = "Уведомления включены. В следующем цикле пришлю неприсланные заказы из свежей выдачи."
    return {"text": text, "reply_markup": MENU}

def settings_reply(chat_id, **changes):
    try:
        if chat_id != get_chat_id():
            return {"text": "Настройки доступны в чате, указанном в TELEGRAM_CHAT_ID."}
        if changes:
            response = requests.post(get_api_url() + f"/notification-filters/{chat_id}", params=changes, timeout=5)
        else:
            response = requests.get(get_api_url() + f"/notifications/{chat_id}", timeout=5)
        if response.status_code == 400:
            detail = response.json().get("detail")
            if isinstance(detail, str):
                return {"text": detail, "reply_markup": SETTINGS_MENU}
        response.raise_for_status()
        settings = response.json()
        state = "на паузе" if settings["paused"] else "включены"
        text = (
            f"Настройки уведомлений\nУведомления: {state}\n"
            f"Биржи: {SOURCE_NAMES[settings['source']]}\n"
            f"Ключевые слова: {settings['keywords'] or 'любые'}\n\n"
            "Выбери биржу кнопкой или нажми «Ключевые слова». "
            "Фильтры применяются к автоматическим уведомлениям. "
            "При включённых уведомлениях подходящие неприсланные заказы будут отправлены в цикле сбора."
        )
        return {"text": text, "reply_markup": SETTINGS_MENU}
    except (requests.RequestException, ValueError, KeyError, TypeError, AttributeError):
        return {"text": "Не удалось получить или сохранить настройки. Проверь API и TELEGRAM_CHAT_ID в .env."}

def reply_messages(text, chat_id=None):
    if text == "/start":
        return [{"text": "Выбери действие в меню.", "reply_markup": MENU}]
    if text in ("/latest", "Последние"):
        return order_messages()
    if text in ("/status", "Статус"):
        return [status_message()]
    if text in ("/pause", "Пауза"):
        return [notification_reply(chat_id, True)]
    if text in ("/resume", "Возобновить"):
        return [notification_reply(chat_id, False)]
    if text in ("/settings", "/sources", "Настройки"):
        return [settings_reply(chat_id)]
    if text in NOTIFICATION_SOURCES:
        return [settings_reply(chat_id, source=NOTIFICATION_SOURCES[text])]
    if text.startswith("/sources "):
        source = text[9:].strip().casefold()
        if source not in SOURCE_NAMES:
            return [{"text": "Выбери биржу в настройках или напиши /sources all, /sources kwork, /sources freelance."}]
        return [settings_reply(chat_id, source=source)]
    if text in ("/keywords", "Ключевые слова"):
        return [{"text": KEYWORDS_HELP}]
    if text in ("/keywords_clear", "Сбросить слова"):
        return [settings_reply(chat_id, keywords="")]
    if text.startswith("/keywords "):
        keywords = text[10:].strip()
        if not keywords:
            return [{"text": KEYWORDS_HELP}]
        if len(keywords) > 300:
            return [{"text": "Для ключевых слов доступно не больше 300 символов."}]
        return [settings_reply(chat_id, keywords=keywords)]
    if text == "Назад":
        return [{"text": "Главное меню.", "reply_markup": MENU}]
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
    answer = response.json()
    if not answer.get("ok"):
        raise ValueError("Telegram не подтвердил запрос")
    return answer["result"]

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
            if text == "/id":
                replies = [{"text": f"Твой номер чата: {chat['id']}"}]
            else:
                replies = reply_messages(text, chat["id"])
            for reply in replies:
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
            print("Бот запущен. Команды: /latest, /search, /status, /settings, /pause, /resume.")
            run_bot(token)
        else:
            print("Укажи токен от BotFather в BOT_TOKEN в файле .env.")
    except ValueError as error:
        print(error)
    except KeyboardInterrupt:
        print("\nБот остановлен.")  