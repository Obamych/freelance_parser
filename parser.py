import json
import sqlite3
from urllib.parse import urljoin, urlsplit
import requests
from bs4 import BeautifulSoup
from storage import save_orders

KWORK_URL = "https://kwork.ru/projects"
FREELANCE_URL = "https://freelance.ru/task"

def parse_kwork(html):
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
            link = f"{KWORK_URL}/{project_id}"
            orders.append((title.strip(), link))
        return orders
    raise ValueError("Список заказов не найден")

def parse_freelance(html):
    soup = BeautifulSoup(html, "html.parser")
    orders = []
    for item in soup.select("a.task-card__title-link[href]"):
        title = item.get_text(" ", strip=True)
        try:
            address = urlsplit(urljoin(FREELANCE_URL, item["href"]))
        except ValueError:
            continue
        if address.scheme not in ("http", "https") or address.netloc not in ("freelance.ru", "www.freelance.ru"):
            continue
        if not title or not address.path.startswith("/task/view/"):
            continue
        task_id = address.path.removeprefix("/task/view/").rstrip("/")
        if task_id.isascii() and task_id.isdecimal() and not task_id.startswith("0"):
            orders.append((title, f"{FREELANCE_URL}/view/{task_id}"))
    if not orders:
        raise ValueError("Ссылки на заказы не найдены")
    return orders

def load_orders(name, url, parse):
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        orders = parse(response.text)
    except requests.HTTPError as error:
        print(f"{name}: ошибка HTTP {error.response.status_code}")
        return []
    except requests.RequestException:
        print(f"{name}: не удалось загрузить страницу")
        return []
    except (ValueError, KeyError, TypeError):
        print(f"{name}: не удалось прочитать список заказов")
        return []
    print(f"{name}: найдено заказов {len(orders)}")
    return orders

def main():
    orders = load_orders("Kwork", KWORK_URL, parse_kwork)
    orders += load_orders("Freelance.ru", FREELANCE_URL, parse_freelance)
    if not orders:
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
