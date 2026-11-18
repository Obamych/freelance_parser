import requests

url = "https://kwork.ru/projects"

try:
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    print("Статус:", response.status_code)
    print(response.text[:200])
except requests.RequestException:
    print("Не удалось загрузить страницу")