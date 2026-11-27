import os
import re
import socket
import subprocess
import sys
from pathlib import Path
from time import monotonic, sleep
from urllib.parse import urlsplit
import requests
from config import (
    API_HOST, get_api_port, get_api_url, get_bot_token,
    get_chat_id, get_collect_interval,
)

PROJECT = Path(__file__).resolve().parent

def check_settings():
    port = get_api_port()
    address = urlsplit(get_api_url())
    if (address.scheme != "http" or address.hostname not in (API_HOST, "localhost")
            or (address.port or 80) != port or address.path
            or address.username is not None or address.password is not None):
        raise ValueError("Для общего запуска убери API_URL из .env. Адрес берётся из API_PORT.")
    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", get_bot_token()):
        raise ValueError("Укажи BOT_TOKEN в .env.")
    get_chat_id()
    get_collect_interval()
    with socket.socket() as connection:
        try:
            connection.bind((API_HOST, port))
        except OSError:
            raise ValueError(
                f"Порт {port} занят. Если api.py уже запущен, останови его через Ctrl+C. "
                "Или выбери другой API_PORT в .env."
            ) from None
    return f"http://{API_HOST}:{port}"

def start_process(filename):
    environment = os.environ.copy()
    environment["PYTHONUNBUFFERED"] = "1"
    return subprocess.Popen(
        [sys.executable, "-X", "utf8", str(PROJECT / filename)],
        cwd=PROJECT, env=environment,
    )

def wait_for_api(process, address):
    deadline = monotonic() + 30
    with requests.Session() as session:
        session.trust_env = False
        while monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("API завершился при запуске. Причина указана выше.")
            try:
                response = session.get(address + "/orders", params={"limit": 1}, timeout=1)
                if response.status_code == 200 and isinstance(response.json(), list):
                    return
            except (requests.RequestException, ValueError):
                pass
            sleep(0.2)
    raise RuntimeError("API не ответил за 30 секунд. Проверь сообщения выше и доступ к базе.")

def watch_processes(processes):
    print("Процессы запущены. Открой бота в Telegram. Остановка всех трёх: Ctrl+C.", flush=True)
    while True:
        for name, process in processes:
            code = process.poll()
            if code is not None:
                raise RuntimeError(
                    f"{name} завершился (код {code}). Проверь сообщения выше. "
                    "Общий запуск будет остановлен."
                )
        sleep(0.5)

def stop_processes(processes):
    for name, process in reversed(processes):
        if process.poll() is None:
            process.terminate()
    for name, process in reversed(processes):
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

def main():
    processes = []
    try:
        address = check_settings()
        print("Запускаю API...", flush=True)
        api = start_process("api.py")
        processes.append(("API", api))
        wait_for_api(api, address)
        print(f"API готов. Список заказов: {address}/", flush=True)
        processes.append(("Бот", start_process("bot.py")))
        processes.append(("Автосбор", start_process("worker.py")))
        watch_processes(processes)
    except KeyboardInterrupt:
        print(flush=True)
    except (ValueError, RuntimeError, OSError) as error:
        print(error, flush=True)
        return 1
    finally:
        stop_processes(processes)
        if processes:
            print("Стоп", flush=True)
    return 0
if __name__ == "__main__":
    sys.exit(main())