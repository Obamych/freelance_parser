import os
import re
import sqlite3
from time import monotonic, sleep
import requests
from bot import order_card, telegram
from config import get_api_url, get_bot_token, get_chat_id, get_collect_interval
from parser import main as collect_orders
from storage import DATABASE, mark_notified, notification_urls, prepare_notifications

def run_cycle(token, chat_id, database=DATABASE):
    if not collect_orders(database):
        print("Сбор не удался. Отправка отложена до следующего цикла.")
        return 0
    if prepare_notifications(chat_id, database):
        print("Начальный список сохранён. Уведомления начнутся с новых заказов.")
        return 0
    response = requests.get(get_api_url() + "/orders", timeout=10)
    response.raise_for_status()
    orders = response.json()
    if not isinstance(orders, list):
        raise ValueError("API должен вернуть список заказов.")
    processed = notification_urls(chat_id, database)
    sent = 0
    for order in reversed(orders):
        url = order["url"]
        if url in processed:
            continue
        telegram(token, "sendMessage", {
            "chat_id": chat_id, **order_card(order),
            "link_preview_options": {"is_disabled": True},
        })
        mark_notified(chat_id, url, database)
        processed.add(url)
        sent += 1
        sleep(1)
    print("Отправлено новых заказов:", sent)
    return sent

def run_worker(token, chat_id, minutes):
    while True:
        started = monotonic()
        try:
            run_cycle(token, chat_id)
        except requests.HTTPError as error:
            code = error.response.status_code
            print(f"Ошибка HTTP {code}. Проверь API и доступ бота к чату.")
            if code in (401, 403, 404):
                return
        except requests.RequestException:
            print("Ошибка соединения. Повтор в следующем цикле.")
        except sqlite3.Error:
            print("Ошибка SQLite. Проверь доступ к базе и место на диске.")
        except (ValueError, KeyError, TypeError):
            print("Не удалось обработать данные. Проверь ответы API.")
        print(f"Следующий сбор через {minutes} минут.", flush=True)
        sleep(max(0, minutes * 60 - (monotonic() - started)))

def main():
    try:
        get_api_url()
        token = get_bot_token()
        if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token):
            print("Укажи BOT_TOKEN в .env.")
            return
        chat_id = get_chat_id()
        minutes = get_collect_interval()
    except ValueError as error:
        print(error)
        return
    with open(str(DATABASE) + ".worker.lock", "a+b") as lock:
        if lock.seek(0, 2) == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("Автосбор уже запущен для этой базы.")
            return
        print(f"Автосбор запущен. Интервал: {minutes} минут.", flush=True)
        try:
            run_worker(token, chat_id, minutes)
        except KeyboardInterrupt:
            print("\nАвтосбор остановлен.")

if __name__ == "__main__":
    main()