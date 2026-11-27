import json
import re
import sqlite3
from urllib.parse import urljoin, urlsplit
import requests
from bs4 import BeautifulSoup
from storage import DATABASE, save_orders

KWORK_URL = "https://kwork.ru/projects"
FREELANCE_URL = "https://freelance.ru/task"

def kwork_budget(project):
    price = str(project.get("priceLimit", ""))
    if project.get("lang") != "ru" or not re.fullmatch(r"[0-9]{1,12}(\.[0-9]{1,2})?", price):
        return None
    rubles, _, coins = price.partition(".")
    if int(rubles) == 0 and not coins.strip("0"):
        return None

    amount = f"{int(rubles):,}".replace(",", " ")
    if coins.strip("0"):
        amount += "," + coins.ljust(2, "0")
    return "до " + amount + " ₽"

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
            if project.get("isWantArchive") is True or project.get("isWantActive") is False:
                continue
            title = project.get("name")
            project_id = str(project.get("id", ""))
            if not isinstance(title, str) or not title.strip():
                continue
            if not project_id.isascii() or not project_id.isdecimal() or project_id.startswith("0"):
                continue
            link = f"{KWORK_URL}/{project_id}"
            orders.append((title.strip(), link, kwork_budget(project)))
        if projects and not orders:
            inactive = all(isinstance(project, dict) and (
                project.get("isWantArchive") is True or project.get("isWantActive") is False
            ) for project in projects)
            if not inactive:
                raise ValueError("Не удалось прочитать карточки заказов")
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
            card = item.find_parent(class_="task-card")
            price = card.select_one(".task-card__budget") if card else None
            budget = " ".join(price.get_text(" ", strip=True).split()) if price else ""
            orders.append((title, f"{FREELANCE_URL}/view/{task_id}", budget or None))
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
        return None
    except requests.RequestException:
        print(f"{name}: не удалось загрузить страницу")
        return None
    except (ValueError, KeyError, TypeError):
        print(f"{name}: не удалось прочитать список заказов")
        return None
    print(f"{name}: найдено заказов {len(orders)}")
    return orders

def main(database=DATABASE):
    orders = []
    checked_sources = []
    for name, url, parse in (("Kwork", KWORK_URL, parse_kwork), ("Freelance.ru", FREELANCE_URL, parse_freelance)):
        batch = load_orders(name, url, parse)
        if batch is not None:
            orders += batch
            checked_sources.append(urljoin(url, "/"))
    if not checked_sources:
        return False
    print("Найдено заказов:", len(orders))
    for title, link, budget in orders:
        print(title)
        print("Бюджет:", budget or "не указан")
        print(link)
    try:
        added = save_orders(orders, database, checked_sources=checked_sources)
    except sqlite3.Error:
        print("Не удалось сохранить заказы в базу")
        return False
    print("Добавлено новых в базу:", added)
    return True

if __name__ == "__main__":
    main()