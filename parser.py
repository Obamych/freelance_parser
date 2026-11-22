import json
import requests
from bs4 import BeautifulSoup
URL = "https://kwork.ru/projects"

def get_titles(html):
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
        titles = []
        for project in projects:
            if not isinstance(project, dict):
                continue
            title = project.get("name")
            if isinstance(title, str) and title.strip():
                titles.append(title.strip())
        return titles
    raise ValueError("Список заказов не найден")

def main():
    try:
        response = requests.get(URL, timeout=10)
        response.raise_for_status()
        titles = get_titles(response.text)
    except requests.RequestException:
        print("Не удалось загрузить страницу")
        return
    except (ValueError, KeyError, TypeError):
        print("Не удалось прочитать список заказов Kwork")
        return
    print("Найдено заказов:", len(titles))
    for title in titles:
        print(title)

if __name__ == "__main__":
    main()