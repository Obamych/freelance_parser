import json
import sqlite3
import requests
from bs4 import BeautifulSoup
from storage import save_orders

URL = "https://kwork.ru/projects"

def get_orders(html):
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.find_all("script"):
        text = script.string or ""
        parts = text.split("window.stateData", 1)
        if len(parts) == 1:
            continue
        value = parts[1].lstrip()
        if not value.startswith("="):
            continue
        data, _ = json.JSONDecoder().raw_decode(value[1:].lstrip())
        projects = data["wantsListData"]["pagination"]["data"]
        if not isinstance(projects, list):
            raise ValueError("Ожидался список заказов")
        orders = []
        for project in projects:
            if not isinstance(project, dict):
                continue
            title = project.get("name")
            project_id = str(project.get("id", ""))
            if not isinstance(title, str) or not title.strip():
                continue
            if not project_id.isascii() or not project_id.isdecimal() or project_id.startswith("0"):
                continue
            link = f"{URL}/{project_id}"
            orders.append((title.strip(), link))
        return orders
    raise ValueError("Список заказов не найден")

def main():
    try:
        response = requests.get(URL, timeout=10)
        response.raise_for_status()
        orders = get_orders(response.text)
    except requests.RequestException:
        print("Не удалось загрузить страницу")
        return
    except (ValueError, KeyError, TypeError):
        print("Не удалось прочитать список заказов Kwork")
        return
    print("Найдено заказов:", len(orders))
    for title, link in orders:
        print(title)
        print(link)
    try:
        added = save_orders(orders)
    except sqlite3.Error:
        print("Не удалось сохранить заказы в базу")
        return
    print("Добавлено новых в базу:", added)

if __name__ == "__main__":
    main()
