import json
import re
import sqlite3
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit
import requests
from bs4 import BeautifulSoup
from storage import DATABASE, read_status, save_orders, update_status

KWORK_URL = "https://kwork.ru/projects"
FREELANCE_URL = "https://freelance.ru/task"

def plain_text(value):
    if not isinstance(value, str):
        return None
    return " ".join(BeautifulSoup(value, "html.parser").get_text(" ", strip=True).split())[:10000] or None

def publication_date(value, pattern=None):
    try:
        date = datetime.strptime(value, pattern) if pattern else datetime.fromisoformat(value)
        return date.isoformat(timespec="seconds")
    except (TypeError, ValueError):
        return None

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
            title = project.get("name")
            project_id = str(project.get("id", ""))
            if not isinstance(title, str) or not title.strip():
                continue
            if not project_id.isascii() or not project_id.isdecimal() or project_id.startswith("0"):
                continue
            link = f"{KWORK_URL}/{project_id}"
            status = "unknown"
            if project.get("isWantArchive") is True or project.get("isWantActive") is False:
                status = "inactive"
            elif project.get("isWantActive") is True and project.get("isWantArchive") is False:
                status = "open"
            orders.append((title.strip(), link, kwork_budget(project), plain_text(project.get("description")), publication_date(project.get("date_create")), status))
        if projects and not orders:
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
            description = card.select_one(".task-card__desc") if card else None
            published = card.select_one(".task-card__foot-item[title]") if card else None
            published_at = publication_date(published.get("title"), "%d.%m.%Y %H:%M") if published else None
            orders.append((title, f"{FREELANCE_URL}/view/{task_id}", budget or None, plain_text(str(description)) if description else None, published_at, "unknown"))
    if not orders:
        raise ValueError("Ссылки на заказы не найдены")
    return orders

def load_orders(name, url, parse, result=None):
    if result is None:
        result = {}
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        orders = parse(response.text)
    except requests.HTTPError as error:
        result["error"] = f"Ошибка HTTP {error.response.status_code}"
        print(f"{name}: {result['error']}")
        return None
    except requests.RequestException:
        result["error"] = "Не удалось загрузить страницу"
        print(f"{name}: не удалось загрузить страницу")
        return None
    except (ValueError, KeyError, TypeError):
        result["error"] = "Не удалось прочитать список заказов"
        print(f"{name}: не удалось прочитать список заказов")
        return None
    print(f"{name}: найдено заказов {len(orders)}")
    return orders

def main(database=DATABASE):
    orders = []
    checked_sources = []
    try:
        previous = read_status(database).get("collection", {}).get("sources", {})
    except (sqlite3.Error, ValueError):
        previous = {}
    results = {}
    for name, url, parse in (("Kwork", KWORK_URL, parse_kwork), ("Freelance.ru", FREELANCE_URL, parse_freelance)):
        result = {"found": None, "error": None, "last_success": previous.get(name, {}).get("last_success")}
        batch = load_orders(name, url, parse, result)
        result["checked_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if batch is not None:
            result["found"] = len(batch)
            orders += batch
            checked_sources.append(urljoin(url, "/"))
        results[name] = result
    print("Найдено заказов:", len(orders))
    for order in orders:
        title, link, budget = order[:3]
        print(title)
        print("Бюджет:", budget or "не указан")
        print(link)
    try:
        added = None
        if checked_sources:
            added = save_orders(orders, database, checked_sources=checked_sources)
            for result in results.values():
                if result["error"] is None:
                    result["last_success"] = result["checked_at"]
        update_status("collection", {"sources": results, "added": added}, database)
    except sqlite3.Error:
        print("Не удалось сохранить заказы в базу")
        return False
    if added is not None:
        print("Добавлено новых в базу:", added)
    return bool(checked_sources)
if __name__ == "__main__":
    main()