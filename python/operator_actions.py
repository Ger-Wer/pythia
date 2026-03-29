from __future__ import annotations

import json
import os
import re
import socket
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = Path(os.getenv("LOG_DIR", str(BASE_DIR / "logs")))
DB_PATH = Path(os.getenv("DB_PATH", str(BASE_DIR / "data" / "pythia.db")))
TELEGRAM_MESSAGE_LIMIT = 4096
LOG_TAIL_LINES = 40
DEFAULT_LAST_PREDICTIONS = 5

BOT_COMMANDS = [
    ("help", "Список команд и инструкция"),
    ("status", "Сводный статус системы"),
    ("forecast", "Ручной запуск дневного прогона"),
    ("resolve", "Проверка resolved маркетов"),
    ("weekly", "Недельный отчёт и анализ"),
    ("day0", "Быстрая проверка доступности рынков"),
    ("edge", "Проверка Brier score и edge"),
    ("logs", "Последние строки логов"),
    ("health", "Техническое состояние сервисов"),
    ("last_predictions", "Последние прогнозы из базы"),
]

HEAVY_COMMANDS = {"forecast", "resolve", "weekly", "day0", "edge"}

COMMAND_ALIASES = {
    "help": "help",
    "помощь": "help",
    "status": "status",
    "статус": "status",
    "forecast": "forecast",
    "прогноз": "forecast",
    "resolve": "resolve",
    "резолв": "resolve",
    "weekly": "weekly",
    "недельный отчет": "weekly",
    "недельный отчёт": "weekly",
    "day0": "day0",
    "проверка рынка": "day0",
    "edge": "edge",
    "logs": "logs",
    "логи": "logs",
    "health": "health",
    "здоровье": "health",
    "last_predictions": "last_predictions",
    "последние": "last_predictions",
}


def normalize_command(text: str | None) -> str | None:
    if not text:
        return None

    normalized = re.sub(r"\s+", " ", text.strip().lower())
    if normalized.startswith("/"):
        normalized = normalized[1:]
        normalized = normalized.split(" ", 1)[0]
        normalized = normalized.split("@", 1)[0]

    return COMMAND_ALIASES.get(normalized)


def is_authorized_update(update: dict[str, Any], allowed_user_id: int) -> bool:
    message = update.get("message") or {}
    sender = message.get("from") or {}
    chat = message.get("chat") or {}
    return sender.get("id") == allowed_user_id and chat.get("type") == "private"


def trim_message(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def build_help_message() -> str:
    return "\n".join(
        [
            "Команды Pythia Operator:",
            "/help или помощь - показать эту справку",
            "/status или статус - сводка по сервисам и базе",
            "/forecast или прогноз - вручную запустить forecast",
            "/resolve или резолв - проверить resolved-маркеты",
            "/weekly или недельный отчет - собрать недельную сводку",
            "/day0 или проверка рынка - выполнить day0 check",
            "/edge - посчитать edge и Brier score",
            "/logs или логи - показать последние строки логов",
            "/health или здоровье - показать состояние сервисов",
            "/last_predictions или последние - вывести последние прогнозы",
        ]
    )


def format_health_summary(services: list[dict[str, str]]) -> str:
    if not services:
        return "Сервисы Pythia не найдены."

    lines = ["Состояние сервисов Pythia:"]
    for service in services:
        name = service.get("name", "unknown")
        status = service.get("status", "unknown")
        state = service.get("state")
        if state and state != status:
            lines.append(f"- {name}: {status} ({state})")
        else:
            lines.append(f"- {name}: {status}")
    return "\n".join(lines)


def run_command(command: list[str], timeout: int = 600) -> tuple[int, str, str]:
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=BASE_DIR,
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def run_operator_command(command_key: str) -> str:
    if command_key == "help":
        return build_help_message()
    if command_key in {"forecast", "resolve", "weekly"}:
        return _run_entrypoint_command(command_key)
    if command_key == "day0":
        return _run_day0_check()
    if command_key == "edge":
        return _run_edge_check()
    if command_key == "logs":
        return render_logs()
    if command_key == "health":
        return render_health()
    if command_key == "status":
        return render_status()
    if command_key == "last_predictions":
        return render_last_predictions()
    return build_help_message()


def render_logs(lines: int = LOG_TAIL_LINES) -> str:
    log_files = _list_log_files()
    if not log_files:
        return f"Логи не найдены в {LOG_DIR}."

    latest = log_files[0]
    try:
        content = latest.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return f"Не удалось прочитать лог {latest.name}: {exc}"

    tail = content[-lines:] if content else []
    body = "\n".join(tail) if tail else "(лог пуст)"
    return f"Последний лог: {latest.name}\n{body}"


def render_health() -> str:
    services = read_compose_services()
    if services:
        return format_health_summary(services)
    return "Не удалось получить состояние сервисов через docker compose."


def render_status() -> str:
    services = read_compose_services()
    sections = [format_health_summary(services) if services else "Не удалось получить состояние сервисов через docker compose."]

    db_summary = _read_database_summary()
    if db_summary:
        sections.append(db_summary)

    log_files = sorted(LOG_DIR.glob("*.log"))
    sections.append(f"Логов в каталоге: {len(log_files)}")
    return "\n\n".join(section for section in sections if section)


def render_last_predictions(limit: int = DEFAULT_LAST_PREDICTIONS) -> str:
    if not DB_PATH.exists():
        return f"База данных не найдена: {DB_PATH}"

    with sqlite3.connect(DB_PATH) as conn:
        if not _predictions_table_exists(conn):
            return "Таблица predictions еще не создана."

        rows = conn.execute(
            """
            SELECT market_id, question, forecast_timestamp, dexter_p, naked_p, market_price
            FROM predictions
            ORDER BY forecast_timestamp DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    if not rows:
        return "В базе пока нет прогнозов."

    rendered_rows = ["Последние прогнозы:"]
    for market_id, question, forecast_timestamp, dexter_p, naked_p, market_price in rows:
        short_question = (question or "").strip()[:120] or "(без текста вопроса)"
        rendered_rows.append(
            f"- {forecast_timestamp}: {short_question} | dexter={_format_percent(dexter_p)} | naked={_format_percent(naked_p)} | market={_format_percent(market_price)} | id={market_id}"
        )
    return "\n".join(rendered_rows)


def read_compose_services() -> list[dict[str, str]]:
    payload = _docker_socket_get("/containers/json?all=1")
    if not payload:
        return []
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return []

    services: list[dict[str, str]] = []
    for item in parsed:
        labels = item.get("Labels") or {}
        project = labels.get("com.docker.compose.project")
        service = labels.get("com.docker.compose.service")
        if project != "pythia":
            continue
        if service not in {"pythia-scheduler", "pythia-telegram"}:
            continue

        name = service or _container_name_from_item(item)
        status = item.get("Status") or item.get("State") or "unknown"
        state = item.get("State") or ""
        services.append({"name": str(name), "status": str(status), "state": str(state)})

    services.sort(key=lambda item: item["name"])
    return services


def _run_entrypoint_command(command_key: str) -> str:
    script = BASE_DIR / "scripts" / "entrypoint.sh"
    if not script.exists():
        return f"Скрипт не найден: {script}"

    code, stdout, stderr = run_command(["bash", str(script), command_key], timeout=3600)
    return _render_command_result(command_key, code, stdout, stderr)


def _run_day0_check() -> str:
    script = BASE_DIR / "scripts" / "day0_check.sh"
    if not script.exists():
        return f"Скрипт не найден: {script}"

    code, stdout, stderr = run_command(["bash", str(script)], timeout=3600)
    return _render_command_result("day0", code, stdout, stderr)


def _run_edge_check() -> str:
    python_bin = BASE_DIR / "python" / "venv" / "bin" / "python"
    script = BASE_DIR / "python" / "edge_calculator.py"
    if not python_bin.exists():
        return f"Python runtime не найден: {python_bin}"
    if not script.exists():
        return f"Скрипт не найден: {script}"

    code, stdout, stderr = run_command([str(python_bin), str(script)], timeout=3600)
    return _render_command_result("edge", code, stdout, stderr)


def _render_command_result(command_key: str, code: int, stdout: str, stderr: str) -> str:
    parts = [f"Команда {command_key} завершилась с кодом {code}."]
    if stdout:
        parts.append(stdout)
    if stderr:
        parts.append(f"stderr:\n{stderr}")
    return "\n\n".join(parts)


def _read_database_summary() -> str:
    if not DB_PATH.exists():
        return f"База данных не найдена: {DB_PATH}"

    with sqlite3.connect(DB_PATH) as conn:
        if not _predictions_table_exists(conn):
            return "Таблица predictions еще не создана."

        total, resolved = conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                COALESCE(SUM(CASE WHEN outcome IS NOT NULL AND outcome IN (0, 1) THEN 1 ELSE 0 END), 0) AS resolved
            FROM predictions
            """
        ).fetchone()

        latest = conn.execute(
            """
            SELECT date(forecast_timestamp), COUNT(*), ROUND(COALESCE(SUM(api_cost_usd), 0), 4)
            FROM predictions
            GROUP BY date(forecast_timestamp)
            ORDER BY date(forecast_timestamp) DESC
            LIMIT 1
            """
        ).fetchone()

    lines = [
        "Сводка по базе:",
        f"- Всего прогнозов: {total}",
        f"- Разрешилось: {resolved}",
        f"- В ожидании: {total - resolved}",
    ]
    if latest:
        lines.append(f"- Последний день: {latest[0]} | прогнозов={latest[1]} | cost=${latest[2]}")
    return "\n".join(lines)


def _predictions_table_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='predictions'"
    ).fetchone()
    return row is not None


def _format_percent(value: Any) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if numeric <= 1:
        numeric *= 100
    return f"{numeric:.1f}%"


def _list_log_files() -> list[Path]:
    try:
        candidates = list(LOG_DIR.glob("*.log"))
    except OSError:
        return []

    scored: list[tuple[float, Path]] = []
    for log_path in candidates:
        try:
            scored.append((log_path.stat().st_mtime, log_path))
        except OSError:
            continue

    scored.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in scored]


def _docker_socket_get(path: str, timeout: int = 5) -> str | None:
    socket_path = os.getenv("DOCKER_SOCKET_PATH", "/var/run/docker.sock")
    if not os.path.exists(socket_path):
        return None

    request = (
        f"GET {path} HTTP/1.1\r\n"
        "Host: docker\r\n"
        "User-Agent: pythia-telegram\r\n"
        "Connection: close\r\n\r\n"
    )

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(timeout)
            client.connect(socket_path)
            client.sendall(request.encode("utf-8"))

            chunks: list[bytes] = []
            while True:
                data = client.recv(65536)
                if not data:
                    break
                chunks.append(data)
    except OSError:
        return None

    raw = b"".join(chunks).decode("utf-8", errors="replace")
    headers, separator, body = raw.partition("\r\n\r\n")
    if not separator or " 200 " not in headers.splitlines()[0]:
        return None
    if "transfer-encoding: chunked" in headers.lower():
        return _decode_chunked_body(body)
    return body


def _container_name_from_item(item: dict[str, Any]) -> str:
    names = item.get("Names") or []
    if isinstance(names, list) and names:
        return str(names[0]).lstrip("/")
    return "unknown"


def _decode_chunked_body(body: str) -> str:
    remainder = body
    decoded_chunks: list[str] = []

    while remainder:
        size_line, separator, rest = remainder.partition("\r\n")
        if not separator:
            break

        try:
            size = int(size_line.split(";", 1)[0], 16)
        except ValueError:
            return body

        if size == 0:
            break

        chunk = rest[:size]
        decoded_chunks.append(chunk)
        remainder = rest[size + 2 :]

    return "".join(decoded_chunks)
