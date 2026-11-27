import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from time import monotonic, sleep
import requests
from bot import format_found_at, order_card, telegram
from config import get_api_url, get_bot_token, get_chat_id, get_collect_interval
from parser import main as collect_orders
from storage import DATABASE, get_notification_settings, mark_notified, notification_urls, notifications_paused, prepare_notifications, update_status

def matches_notifications(order, settings):
    prefixes = {"kwork": "https://kwork.ru/", "freelance": "https://freelance.ru/"}
    if settings["source"] != "all" and not order["url"].startswith(prefixes[settings["source"]]):
        return False
    words = [word.strip().casefold() for word in settings["keywords"].split(",") if word.strip()]
    return not words or any(word in order["title"].casefold() for word in words)

def worker_status(database=DATABASE, **values):
    try:
        update_status("worker", values, database)
    except (sqlite3.Error, ValueError):
        print("Не удалось сохранить состояние автосбора.")

def run_cycle(token, chat_id, database=DATABASE):
    worker_status(database, phase="collecting", sent=0, error=None, next_run=None)
    if not collect_orders(database):
        print("Сбор не удался. Отправка отложена до следующего цикла.")
        worker_status(database, phase="error", error="Не удалось собрать и сохранить заказы.")
        return 0
    if prepare_notifications(chat_id, database):
        print("Начальный список сохранён. Уведомления начнутся с новых заказов.")
        worker_status(database, phase="finished")
        return 0
    if notifications_paused(chat_id, database):
        worker_status(database, phase="paused")
        print("Уведомления на паузе. Заказы собраны и сохранены.")
        return 0
    worker_status(database, phase="sending")
    response = requests.get(get_api_url() + "/orders", params={"chat_id": chat_id}, timeout=10)
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
        settings = get_notification_settings(chat_id, database)
        if settings["paused"]:
            worker_status(database, phase="paused")
            print("Рассылка приостановлена. Отправлено в этом цикле:", sent)
            return sent
        if not matches_notifications(order, settings):
            continue
        telegram(token, "sendMessage", {
            "chat_id": chat_id, **order_card(order),
            "link_preview_options": {"is_disabled": True},
        })
        mark_notified(chat_id, url, database)
        processed.add(url)
        sent += 1
        worker_status(database, sent=sent)
        sleep(1)
    print("Отправлено новых заказов:", sent)
    worker_status(database, phase="finished")
    return sent

def run_worker(token, chat_id, minutes):
    while True:
        started = monotonic()
        try:
            run_cycle(token, chat_id)
        except requests.HTTPError as error:
            code = error.response.status_code
            print(f"Ошибка HTTP {code}. Проверь API и доступ бота к чату.")
            worker_status(phase="error", error=f"Ошибка HTTP {code} при получении или отправке заказов.")
            if code in (401, 403, 404):
                worker_status(phase="stopped", next_run=None)
                return
        except requests.RequestException:
            print("Ошибка соединения. Повтор в следующем цикле.")
            worker_status(phase="error", error="Ошибка соединения с API или Telegram.")
        except sqlite3.Error:
            print("Ошибка SQLite. Проверь доступ к базе и место на диске.")
            worker_status(phase="error", error="Ошибка SQLite. Проверь доступ к базе и место на диске.")
        except (ValueError, KeyError, TypeError):
            print("Не удалось обработать данные. Проверь ответы API.")
            worker_status(phase="error", error="Не удалось обработать ответ API или Telegram.")
        delay = max(0, minutes * 60 - (monotonic() - started))
        next_run = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat(timespec="seconds")
        worker_status(phase="waiting", next_run=next_run)
        print("Следующий сбор:", format_found_at(next_run), flush=True)
        sleep(delay)

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
        finally:
            worker_status(phase="stopped", next_run=None)
if __name__ == "__main__":
    main()