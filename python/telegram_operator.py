from __future__ import annotations

import os
import time
import traceback
from typing import Any

import requests

from python.operator_actions import (
    BOT_COMMANDS,
    HEAVY_COMMANDS,
    build_help_message,
    is_authorized_update,
    normalize_command,
    run_operator_command,
    trim_message,
)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ALLOWED_USER_ID = int(os.getenv("TELEGRAM_OPERATOR_USER_ID", "405690972"))
POLL_TIMEOUT = int(os.getenv("TELEGRAM_OPERATOR_POLL_TIMEOUT", "30"))

CURRENT_JOB: str | None = None


def api_url(method: str) -> str:
    return f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"


def api_post(method: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = requests.post(api_url(method), json=payload, timeout=POLL_TIMEOUT + 10)
    response.raise_for_status()
    data = response.json()
    if not data.get("ok", False):
        raise RuntimeError(f"Telegram API error in {method}: {data}")
    return data


def set_commands() -> None:
    commands = [{"command": name, "description": description} for name, description in BOT_COMMANDS]
    api_post("setMyCommands", {"commands": commands})


def send_message(chat_id: int, text: str) -> None:
    prepared = prepare_markdown_message(text)
    api_post(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": prepared,
            "parse_mode": "Markdown",
        },
    )


def get_updates(offset: int | None = None) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"timeout": POLL_TIMEOUT}
    if offset is not None:
        params["offset"] = offset

    response = requests.get(api_url("getUpdates"), params=params, timeout=POLL_TIMEOUT + 10)
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok", False):
        raise RuntimeError(f"Telegram API error in getUpdates: {payload}")
    return payload.get("result", [])


def handle_update(update: dict[str, Any]) -> None:
    global CURRENT_JOB

    message = update.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    text = message.get("text")

    if not chat_id:
        return

    if not is_authorized_update(update, ALLOWED_USER_ID):
        return

    command_key = normalize_command(text)
    if not command_key:
        send_message(chat_id, "Неизвестная команда. Напиши /help или помощь.")
        return

    if command_key == "help":
        send_message(chat_id, build_help_message())
        return

    if command_key in HEAVY_COMMANDS and CURRENT_JOB:
        send_message(chat_id, f"Сейчас уже выполняется тяжёлая команда: {CURRENT_JOB}. Дождись завершения.")
        return

    try:
        if command_key in HEAVY_COMMANDS:
            CURRENT_JOB = command_key
            send_message(chat_id, f"Запускаю {command_key}. Это может занять несколько минут.")

        result = run_operator_command(command_key)
        send_message(chat_id, result)
    except Exception as exc:
        traceback.print_exc()
        send_message(chat_id, f"Команда {command_key} завершилась с ошибкой: {exc}")
    finally:
        if CURRENT_JOB == command_key:
            CURRENT_JOB = None


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN")

    set_commands()
    offset: int | None = None

    while True:
        try:
            updates = get_updates(offset)
            for update in updates:
                offset = update.get("update_id", 0) + 1
                handle_update(update)
        except Exception:
            traceback.print_exc()
            time.sleep(5)


def escape_markdown(text: str) -> str:
    escaped = text.replace("\\", "\\\\")
    for char in ("`", "*", "_", "[", "]", "(", ")"):
        escaped = escaped.replace(char, f"\\{char}")
    return escaped


def prepare_markdown_message(text: str) -> str:
    escaped = escape_markdown(text)
    trimmed = trim_message(escaped)
    while trimmed.endswith("\\"):
        trimmed = trimmed[:-1]
    return trimmed


if __name__ == "__main__":
    main()
