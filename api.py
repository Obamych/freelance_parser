import sqlite3
from html import escape
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from storage import read_orders

app = FastAPI()

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def home():
    items = []
    for order in get_orders():
        title = escape(order["title"])
        url = order["url"]
        if url.startswith(("https://", "http://")):
            items.append(f'<li><a href="{escape(url)}">{title}</a></li>')
        else:
            items.append(f"<li>{title}</li>")

    if items:
        content = "<ol>" + "".join(items) + "</ol>"
    else:
        content = "<p>Заказов пока нет. Запусти parser.py, чтобы их собрать.</p>"

    return f"""<!doctype html>
<html lang="ru">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Заказы с бирж</title>
    <style>
        body {{ font-family: Arial, sans-serif; max-width: 800px; margin: 32px auto; padding: 0 16px; }}
        li {{ margin: 10px 0; overflow-wrap: anywhere; }}
    </style>
</head>
<body>
    <h1>Заказы с бирж</h1>
    {content}
</body>
</html>"""

@app.get("/orders")
def get_orders():
    try:
        return read_orders()
    except sqlite3.Error:
        raise HTTPException(status_code=503, detail="Не удалось прочитать заказы из базы") from None

if __name__ == "__main__":
    host = "127.0.0.1"
    port = 8000
    print(f"Список заказов: http://{host}:{port}/", flush=True)
    uvicorn.run(app, host=host, port=port)
