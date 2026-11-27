import os
from pathlib import Path
from urllib.parse import urlsplit
from dotenv import load_dotenv

API_HOST = "127.0.0.1"
load_dotenv(Path(__file__).with_name(".env"), encoding="utf-8-sig")

def get_api_port():
    try:
        port = int(os.getenv("API_PORT", "8000"))
    except ValueError:
        port = 0
    if not 1 <= port <= 65535:
        raise ValueError("API_PORT должен быть числом от 1 до 65535.")
    return port

def get_api_url():
    address = os.getenv("API_URL", "").strip().rstrip("/")
    if not address:
        return f"http://{API_HOST}:{get_api_port()}"
    parts = urlsplit(address)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.query or parts.fragment:
        raise ValueError("Укажи в API_URL адрес API с http:// или https://.")
    return address

def get_bot_token():
    return os.getenv("BOT_TOKEN", "").strip()

def get_chat_id():
    try:
        chat_id = int(os.getenv("TELEGRAM_CHAT_ID", ""))
    except ValueError:
        chat_id = 0
    if chat_id <= 0:
        raise ValueError("Укажи TELEGRAM_CHAT_ID в .env. Свой номер чата можно узнать командой /id у бота.")
    return chat_id

def get_collect_interval():
    try:
        minutes = int(os.getenv("COLLECT_INTERVAL_MINUTES", "10"))
    except ValueError:
        minutes = 0
    if not 1 <= minutes <= 1440:
        raise ValueError("COLLECT_INTERVAL_MINUTES должен быть числом от 1 до 1440.")
    return minutes