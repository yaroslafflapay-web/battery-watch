#!/usr/bin/env python3
"""
Мониторинг SOC батареи через DESS Monitor / SmartESS (Eybond) API.
Шлёт push-уведомление через ntfy.sh при падении заряда ниже порога,
с гистерезисом (не спамит повторно, пока заряд не восстановится выше
верхнего порога). Состояние (alerted/не alerted) хранится в state.json,
который workflow коммитит обратно в репозиторий.

ВАЖНО: это работает через неофициальный, реверс-инжинирированный доступ
к облаку Eybond (см. README) — производитель такой API не документирует
и не гарантирует. При обновлении приложения SmartESS ссылка может
перестать работать — тогда потребуется заново снять DESS_API_URL.
"""

import json
import os
import sys
import urllib.request

DESS_API_URL = os.environ["DESS_API_URL"]  # полный URL из Network tab браузера (см. README)
DESS_AUTH_HEADER = os.environ.get("DESS_AUTH_HEADER", "")  # значение заголовка Auth (JWT), см. README
NTFY_TOPIC = os.environ["NTFY_TOPIC"]  # длинная случайная строка — держать в секрете как пароль
NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
SOC_PARAM_ID = os.environ.get("SOC_PARAM_ID", "bt_battery_capacity")  # id параметра SOC в ответе API
LOW_THRESHOLD = float(os.environ.get("LOW_THRESHOLD", "25"))
RECOVER_THRESHOLD = float(os.environ.get("RECOVER_THRESHOLD", "40"))
SHELLY_ACTION_URL = os.environ.get("SHELLY_ACTION_URL", "")  # опционально, см. README про риски
STATE_FILE = "state.json"


def fetch_dess_data():
    headers = {}
    if DESS_AUTH_HEADER:
        headers["Auth"] = DESS_AUTH_HEADER
    req = urllib.request.Request(DESS_API_URL, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def extract_soc(data):
    """
    Ответ DESS Monitor: data['dat']['pars'] — словарь групп параметров вида
    { "bt_": [ {"id": "bt_battery_capacity", "par": "Battery percentage",
                "val": "79", "unit": "%"}, ... ], "pv_": [...], ... }.
    Внутри одной группы может быть несколько параметров, поэтому ищем именно
    по полю "id", а не по имени группы и не по позиции в списке.
    """
    pars = data.get("dat", {}).get("pars", {})
    for group in pars.values():
        for entry in group:
            if entry.get("id") == SOC_PARAM_ID:
                return float(entry["val"])

    # Не нашли — печатаем ВЕСЬ ответ сервера (не только pars), чтобы увидеть
    # code/msg, если запрос не прошёл авторизацию, а не только пустую структуру.
    print(f"Параметр с id='{SOC_PARAM_ID}' не найден.")
    print("Полный ответ сервера (проверь поля 'code' и 'msg' — это укажет на причину):")
    print(json.dumps(data, ensure_ascii=False, indent=2))
    sys.exit(1)


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"alerted": False}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f)


def send_ntfy(soc):
    actions = []
    if SHELLY_ACTION_URL:
        actions.append(
            {
                "action": "http",
                "label": "Включить реле сети",
                "url": SHELLY_ACTION_URL,
                "method": "POST",
            }
        )
    payload = {
        "topic": NTFY_TOPIC,
        "title": "⚠️ Низкий заряд батареи",
        "message": f"SOC батареи: {soc:.0f}% (порог {LOW_THRESHOLD:.0f}%).",
        "priority": 5,
        "tags": ["warning", "battery"],
    }
    if actions:
        payload["actions"] = actions

    req = urllib.request.Request(
        NTFY_SERVER,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=10)


def main():
    data = fetch_dess_data()
    soc = extract_soc(data)
    print(f"Текущий SOC: {soc}%")

    state = load_state()

    if soc <= LOW_THRESHOLD and not state["alerted"]:
        send_ntfy(soc)
        state["alerted"] = True
        print("Уведомление отправлено.")
    elif soc >= RECOVER_THRESHOLD and state["alerted"]:
        state["alerted"] = False
        print("Заряд восстановился выше верхнего порога — флаг сброшен.")
    else:
        print("Без изменений (порог не пройден или уже был отправлен алерт).")

    save_state(state)


if __name__ == "__main__":
    main()
