import sqlite3
from html import escape
from typing import Literal
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from config import API_HOST, get_api_port, get_chat_id
from storage import favorite_urls, get_notification_settings, read_orders, read_status, set_favorite, set_notification_filters, set_notifications_paused

app = FastAPI()
SOURCES = {
    "kwork": ("https://kwork.ru/", "Kwork"),
    "freelance": ("https://freelance.ru/", "Freelance.ru"),
}

def source_name(order):
    for prefix, label in SOURCES.values():
        if order["url"].startswith(prefix):
            return {**order, "source": label}
    return {**order, "source": "Неизвестен"}

def find_orders(q, source):
    try:
        orders = read_orders()
    except sqlite3.Error:
        raise HTTPException(status_code=503, detail="Не удалось прочитать заказы из базы") from None
    query = q.strip().casefold()
    found = []
    for order in orders:
        if query not in order["title"].casefold():
            continue
        if source and not order["url"].startswith(SOURCES[source][0]):
            continue
        found.append(source_name(order))
    return found

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def home(q: str = Query(default="", max_length=100), source: Literal["", "kwork", "freelance"] = ""):
    orders = find_orders(q, source)
    items = []
    for order in orders:
        title = escape(order["title"])
        url = order["url"]
        if url.startswith(("https://", "http://")):
            items.append(f'<li><a href="{escape(url)}">{title}</a></li>')
        else:
            items.append(f"<li>{title}</li>")
    if items:
        content = "<ol>" + "".join(items) + "</ol>"
    elif q.strip() or source:
        content = "<p>По этим условиям заказы не найдены.</p>"
    else:
        content = "<p>Свежих заказов пока нет. Запусти parser.py, чтобы обновить список.</p>"
    kwork_selected = "selected" if source == "kwork" else ""
    freelance_selected = "selected" if source == "freelance" else ""
    return f"""<!doctype html>
<html lang="ru">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Заказы с бирж</title>
    <style>
        body {{ font-family: Arial, sans-serif; max-width: 800px; margin: 32px auto; padding: 0 16px; }}
        form {{ display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }}
        input, select, button {{ font: inherit; padding: 6px; }}
        li {{ margin: 10px 0; overflow-wrap: anywhere; }}
    </style>
</head>
<body>
    <h1>Заказы с бирж</h1>
    <form method="get" action="/">
        <label for="q">Название</label>
        <input type="search" id="q" name="q" maxlength="100" value="{escape(q)}">
        <label for="source">Биржа</label>
        <select id="source" name="source">
            <option value="">Все</option>
            <option value="kwork" {kwork_selected}>Kwork</option>
            <option value="freelance" {freelance_selected}>Freelance.ru</option>
        </select>
        <button type="submit">Найти</button>
        <a href="/">Сбросить</a>
    </form>
    <p>Найдено заказов: {len(orders)}</p>
    {content}
</body>
</html>"""

@app.get("/status")
def get_status():
    try:
        status = read_status()
        try:
            chat_id = get_chat_id()
        except ValueError:
            return status
        status["notifications"] = get_notification_settings(chat_id)
        return status
    except (sqlite3.Error, ValueError):
        raise HTTPException(status_code=503, detail="Не удалось прочитать состояние сбора") from None

def check_notification_owner(chat_id):
    try:
        owner = get_chat_id()
    except ValueError:
        raise HTTPException(status_code=503, detail="Не настроен чат для уведомлений") from None
    if chat_id != owner:
        raise HTTPException(status_code=403, detail="Управление доступно в настроенном чате")

@app.get("/notifications/{chat_id}")
def notification_settings(chat_id: int):
    check_notification_owner(chat_id)
    try:
        return get_notification_settings(chat_id)
    except sqlite3.Error:
        raise HTTPException(status_code=503, detail="Не удалось прочитать настройки") from None

@app.post("/notification-filters/{chat_id}")
def change_notification_filters(
    chat_id: int,
    source: Literal["all", "kwork", "freelance"] | None = Query(default=None),
    keywords: str | None = Query(default=None, max_length=300),
):
    check_notification_owner(chat_id)
    if source is None and keywords is None:
        raise HTTPException(status_code=400, detail="Укажи биржу или ключевые слова")
    try:
        set_notification_filters(chat_id, source, keywords)
        return get_notification_settings(chat_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None
    except sqlite3.Error:
        raise HTTPException(status_code=503, detail="Не удалось сохранить фильтры") from None

@app.post("/notifications/{chat_id}")
def change_notifications(chat_id: int, paused: bool = Query(...)):
    check_notification_owner(chat_id)
    try:
        set_notifications_paused(chat_id, paused)
    except sqlite3.Error:
        raise HTTPException(status_code=503, detail="Не удалось сохранить настройку уведомлений") from None
    return {"paused": paused}

@app.get("/orders")
def get_orders(
    q: str = Query(default="", max_length=100),
    source: Literal["", "kwork", "freelance"] = "",
    limit: int | None = Query(default=None, ge=1, le=100),
    chat_id: int | None = Query(default=None, gt=0),
):
    orders = find_orders(q, source)
    if chat_id is not None:
        try:
            saved = favorite_urls(chat_id)
        except sqlite3.Error:
            raise HTTPException(status_code=503, detail="Не удалось прочитать избранное") from None
        orders = [{**order, "is_favorite": order["url"] in saved} for order in orders]
    return orders if limit is None else orders[:limit]

@app.get("/favorites/{chat_id}")
def list_favorites(chat_id: int):
    if chat_id <= 0:
        raise HTTPException(status_code=400, detail="Некорректный номер чата")
    try:
        return [{**source_name(order), "is_favorite": True} for order in read_orders(favorite_chat=chat_id)]
    except sqlite3.Error:
        raise HTTPException(status_code=503, detail="Не удалось прочитать избранное") from None

@app.post("/favorites/{chat_id}/{order_id}")
def change_favorite(chat_id: int, order_id: int, favorite: bool = Query(...)):
    if chat_id <= 0 or order_id <= 0:
        raise HTTPException(status_code=400, detail="Некорректный номер чата или заказа")
    try:
        set_favorite(chat_id, order_id, favorite)
    except ValueError:
        raise HTTPException(status_code=404, detail="Заказ не найден") from None
    except sqlite3.Error:
        raise HTTPException(status_code=503, detail="Не удалось сохранить избранное") from None
    return {"favorite": favorite}
if __name__ == "__main__":
    try:
        port = get_api_port()
    except ValueError as error:
        print(error)
    else:
        print(f"Список заказов: http://{API_HOST}:{port}/", flush=True)
        uvicorn.run(app, host=API_HOST, port=port)