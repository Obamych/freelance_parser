import sqlite3
from html import escape
from typing import Literal
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from storage import read_orders

app = FastAPI()
SOURCE_URLS = {"kwork": "https://kwork.ru/", "freelance": "https://freelance.ru/"}

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
        if source and not order["url"].startswith(SOURCE_URLS[source]):
            continue
        found.append(order)
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
        content = "<p>Заказов пока нет. Запусти parser.py, чтобы их собрать.</p>"

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

@app.get("/orders")
def get_orders(
    q: str = Query(default="", max_length=100),
    source: Literal["", "kwork", "freelance"] = "",
    limit: int | None = Query(default=None, ge=1, le=100),
):
    orders = find_orders(q, source)
    return orders if limit is None else orders[:limit]
if __name__ == "__main__":
    host = "127.0.0.1"
    port = 8000
    print(f"Список заказов: http://{host}:{port}/", flush=True)
    uvicorn.run(app, host=host, port=port)
