from __future__ import annotations

import json
import os
import re
import socket
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

BASE_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = Path(os.getenv("LOG_DIR", str(BASE_DIR / "logs")))
DB_PATH = Path(os.getenv("DB_PATH", str(BASE_DIR / "data" / "pythia.db")))
TELEGRAM_MESSAGE_LIMIT = 4096
LOG_TAIL_LINES = 150
DEFAULT_LAST_PREDICTIONS = 10

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
    ("details", "Подробности по рынку: /details <номер|market_id>"),
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
    "details": "details",
    "детали": "details",
}


def parse_command_request(text: str | None) -> tuple[str | None, str | None]:
    if not text:
        return None, None

    normalized = re.sub(r"\s+", " ", text.strip().lower())
    argument: str | None = None
    if normalized.startswith("/"):
        normalized = normalized[1:]
        command_part, _, remainder = normalized.partition(" ")
        normalized = command_part
        normalized = normalized.split("@", 1)[0]
        argument = remainder.strip() or None
    else:
        for alias in sorted(COMMAND_ALIASES, key=len, reverse=True):
            if normalized == alias:
                return COMMAND_ALIASES[alias], None
            if normalized.startswith(f"{alias} "):
                return COMMAND_ALIASES[alias], normalized[len(alias) :].strip() or None

        command_part, _, remainder = normalized.partition(" ")
        normalized = command_part
        argument = remainder.strip() or None

    return COMMAND_ALIASES.get(normalized), argument


def normalize_command(text: str | None) -> str | None:
    return parse_command_request(text)[0]


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
            "/details <номер|market_id> или детали <номер|market_id> - показать полный разбор рынка",
        ]
    )


def shorten(text: str, max_len: int) -> str:
    normalized = re.sub(r"\s+", " ", (text or "").strip())
    if len(normalized) <= max_len:
        return normalized
    return normalized[: max_len - 3].rstrip() + "..."


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


def _current_utc_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _current_month_start() -> str:
    return datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d")


def _latest_forecast_timestamp(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT MAX(forecast_timestamp) FROM predictions").fetchone()
    if not row or not row[0]:
        return None
    return str(row[0])


def _prediction_columns(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(predictions)").fetchall()
    }


def _has_column(conn: sqlite3.Connection, column_name: str) -> bool:
    return column_name in _prediction_columns(conn)


def _valid_where_clause(conn: sqlite3.Connection, alias: str = "") -> str:
    if not _has_column(conn, "is_valid"):
        return ""
    prefix = f"{alias}." if alias else ""
    return f" AND {prefix}is_valid = 1"


def _api_cost_label(total_cost: float | None = None) -> str:
    base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1").lower()
    if "groq.com" in base_url:
        return "бесплатно (Groq)"
    if total_cost is None:
        return "проверьте биллинг провайдера"
    return f"${float(total_cost):.2f}"


def _confidence_ru(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    if normalized in {"high", "высокая", "высокий"}:
        return "высокая"
    if normalized in {"low", "низкая", "низкий"}:
        return "низкая"
    return "средняя"


def _infer_icon(question: str) -> str:
    haystack = (question or "").lower()
    if any(keyword in haystack for keyword in ("nba", "western conference", "eastern conference", "rookie of the year", "mvp")):
        return "🏀"
    if any(keyword in haystack for keyword in ("premier league", "bundesliga", "serie a", "ligue 1", "world cup", "fifa")):
        return "⚽"
    if any(keyword in haystack for keyword in ("nhl", "stanley cup")):
        return "🏒"
    if any(keyword in haystack for keyword in ("masters tournament", "golf", "pga")):
        return "⛳"
    if any(keyword in haystack for keyword in ("bond", "oscar", "emmy", "grammy", "movie", "album")):
        return "🎬"
    if any(keyword in haystack for keyword in ("election", "presidential", "primary", "congress", "senate")):
        return "🗳️"
    if any(keyword in haystack for keyword in ("bitcoin", "ethereum", "crypto", "btc", "eth")):
        return "₿"
    return "•"


def _collect_command_issues(stdout: str, stderr: str) -> list[str]:
    combined = f"{stdout}\n{stderr}"
    issues: list[str] = []

    if "Exa HTTP 401" in combined:
        issues.append("Exa API вернул 401. Поиск новостей через Exa недоступен.")
    elif "Exa search failed" in combined or "поиск через Exa завершился ошибкой" in combined:
        issues.append("Exa API недоступен. Используется fallback, если он настроен.")

    if "Tavily HTTP 401" in combined or "Tavily authentication failed" in combined or "Tavily отклонил ключ" in combined:
        issues.append("Tavily API отклонил ключ.")
    elif "Tavily search failed" in combined or "поиск через Tavily завершился ошибкой" in combined:
        issues.append("Tavily API недоступен.")

    if "no working search provider configured" in combined or "не настроен ни один рабочий search provider" in combined:
        issues.append("Research-слой работает без внешнего поиска новостей.")

    unique_issues: list[str] = []
    for issue in issues:
        if issue not in unique_issues:
            unique_issues.append(issue)
    return unique_issues


def _derive_research_status(issues: list[str]) -> str:
    has_exa_key = bool(os.getenv("EXA_API_KEY", "").strip())
    has_tavily_key = bool(os.getenv("TAVILY_API_KEY", "").strip())

    if not has_exa_key and not has_tavily_key:
        return "OFF"

    if any("Exa API вернул 401" in issue or "Tavily API отклонил ключ" in issue for issue in issues):
        if has_tavily_key or has_exa_key:
            return "DEGRADED"
        return "OFF"

    if any("Research-слой работает без внешнего поиска" in issue for issue in issues):
        return "OFF"

    return "ON"


def _derive_research_label(issues: list[str]) -> str:
    status = _derive_research_status(issues)
    has_exa_key = bool(os.getenv("EXA_API_KEY", "").strip())
    has_tavily_key = bool(os.getenv("TAVILY_API_KEY", "").strip())

    if status == "ON":
        if has_exa_key:
            return "ON (Exa)"
        if has_tavily_key:
            return "ON (Tavily)"
        return "ON"

    if status == "DEGRADED":
        if has_tavily_key:
            return "DEGRADED (Tavily)"
        return "DEGRADED"

    return "OFF"


def format_forecast_report(
    db_path: Path | str,
    run_timestamp: str | None = None,
    research_status: str | None = None,
    issues: list[str] | None = None,
) -> str:
    db_path = Path(db_path)
    issues = issues or []

    if not db_path.exists():
        return f"📊 База данных не найдена: {db_path}"

    with sqlite3.connect(db_path) as conn:
        if not _predictions_table_exists(conn):
            return "📊 Таблица predictions ещё не создана."

        latest_run = run_timestamp or _latest_forecast_timestamp(conn)
        if not latest_run:
            return "📊 Прогнозов пока нет."

        valid_filter = _valid_where_clause(conn)
        rows = conn.execute(
            """
            SELECT question, market_price, naked_p, dexter_p,
                   dexter_confidence, dexter_reasoning, naked_reasoning,
                   api_cost_usd, volume
            FROM predictions
            WHERE forecast_timestamp = ?
            """ + valid_filter + """
            ORDER BY ABS(COALESCE(dexter_p, naked_p, market_price) - market_price) DESC
            """,
            (latest_run,),
        ).fetchall()

    if not rows:
        return "📊 Для последнего прогона записи не найдены."

    total_cost = sum(float(row[7] or 0) for row in rows)
    status = research_status or _derive_research_label(issues)
    run_day = str(latest_run)[:10]

    lines = [
        f"📊 Pythia — прогнозы за {run_day}",
        f"Обработано рынков: {len(rows)}",
        f"Стоимость: {_api_cost_label(total_cost)}",
        f"Исследование: {status}",
    ]

    if issues:
        lines.append("")
        lines.append("Предупреждения:")
        lines.extend(f"- {issue}" for issue in issues[:3])

    alerts: list[str] = []
    for question, market_price, naked_p, dexter_p, _confidence, dexter_reasoning, naked_reasoning, _cost, _volume in rows:
        best_p = dexter_p if dexter_p is not None else naked_p
        if best_p is None or market_price is None:
            continue
        edge = float(best_p) - float(market_price)
        if abs(edge) > 10:
            direction = "↑" if edge > 0 else "↓"
            reasoning = (dexter_reasoning or naked_reasoning or "Подробное объяснение смотрите через /details.")
            alerts.append(
                "\n".join(
                    [
                        f"{direction} {shorten(str(question), 55)}",
                        f"  Толпа: {float(market_price):.0f}% | Pythia: {float(best_p):.0f}% ({edge:+.0f}%)",
                        f"  Почему: {shorten(str(reasoning), 110)}",
                    ]
                )
            )

    lines.append("")
    if alerts:
        lines.append("⚡ Расхождения с рынком > 10%:")
        lines.extend(alerts[:5])
    else:
        lines.append("✅ Значимых расхождений с рынком нет.")

    lines.append("")
    lines.append("📋 Топ-5 прогнозов:")
    for index, (question, market_price, naked_p, dexter_p, _confidence, _dexter_reasoning, _naked_reasoning, _cost, volume) in enumerate(rows[:5], start=1):
        best_p = dexter_p if dexter_p is not None else naked_p
        best_p_str = "N/A" if best_p is None else f"{float(best_p):.0f}%"
        lines.append(
            f"{index}. {_infer_icon(str(question))} {shorten(str(question), 46)}\n"
            f"   рынок: {float(market_price or 0):.0f}% | Pythia: {best_p_str} | объём: ${float(volume or 0):,.0f}"
        )

    if len(rows) > 5:
        lines.append("")
        lines.append(f"...и ещё {len(rows) - 5} рынков. Команда /last_predictions покажет последние записи.")
    lines.append("📊 Подробности по рынку: /details <номер или market_id>")

    return "\n".join(lines)


def format_status_report(
    db_path: Path | str,
    services: list[dict[str, str]] | None = None,
) -> str:
    db_path = Path(db_path)
    lines = ["📈 Статус Pythia"]

    if services:
        lines.append("")
        lines.append(format_health_summary(services))

    if not db_path.exists():
        lines.append("")
        lines.append(f"База данных не найдена: {db_path}")
        return "\n".join(lines)

    with sqlite3.connect(db_path) as conn:
        if not _predictions_table_exists(conn):
            lines.append("")
            lines.append("Таблица predictions ещё не создана.")
            return "\n".join(lines)

        valid_filter = _valid_where_clause(conn)
        total = conn.execute(f"SELECT COUNT(*) FROM predictions WHERE 1=1{valid_filter}").fetchone()[0]
        resolved = conn.execute(
            f"SELECT COUNT(*) FROM predictions WHERE outcome IS NOT NULL AND outcome IN (0,1){valid_filter}"
        ).fetchone()[0]
        today = _current_utc_date()
        today_count = conn.execute(
            f"SELECT COUNT(*) FROM predictions WHERE forecast_timestamp LIKE ?{valid_filter}",
            (f"{today}%",),
        ).fetchone()[0]
        month_cost = conn.execute(
            f"SELECT COALESCE(SUM(api_cost_usd), 0) FROM predictions WHERE forecast_timestamp >= ?{valid_filter}",
            (_current_month_start(),),
        ).fetchone()[0]
        brier = conn.execute(
            """
            SELECT
                ROUND(AVG(brier_naked), 4),
                ROUND(AVG(brier_dexter), 4),
                ROUND(AVG(brier_market), 4)
            FROM predictions
            WHERE outcome IS NOT NULL AND outcome IN (0,1)
            """
            + valid_filter
        ).fetchone()

    lines.extend(
        [
            "",
            "🟢 Система работает",
            f"Всего прогнозов: {total}",
            f"Сегодня: {today_count}",
            f"Разрешено: {resolved}",
            f"Ожидают: {total - resolved}",
            "",
            f"💰 API: {_api_cost_label(month_cost)}",
            f"🔍 Исследование: {_derive_research_label([])}",
        ]
    )

    if brier and brier[0] is not None:
        lines.extend(
            [
                "",
                "Brier score (чем ниже, тем лучше):",
                f"- Naked R1: {brier[0]}",
                f"- Research+R1: {brier[1] if brier[1] is not None else 'N/A'}",
                f"- Рынок: {brier[2]}",
            ]
        )
    else:
        lines.extend(["", f"Brier score пока недоступен: разрешён {resolved} из минимум 20 валидных рынков."])

    next_run = "завтра 06:00 UTC"
    lines.extend(["", f"Следующий автоматический прогон: {next_run}"])

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


def run_operator_command(command_key: str, command_arg: str | None = None) -> str:
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
    if command_key == "details":
        return render_details(command_arg)
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
    checks = ["🏥 Здоровье Pythia", ""]

    if services:
        checks.append("Сервисы:")
        checks.extend(f"  {line}" for line in format_health_summary(services).splitlines()[1:])
        checks.append("")

    checks.extend(_collect_runtime_health_checks())
    return "\n".join(checks)


def render_status() -> str:
    return format_status_report(DB_PATH, read_compose_services())


def format_last_predictions_report(db_path: Path | str, limit: int = DEFAULT_LAST_PREDICTIONS) -> str:
    db_path = Path(db_path)
    if not db_path.exists():
        return f"База данных не найдена: {db_path}"

    with sqlite3.connect(db_path) as conn:
        if not _predictions_table_exists(conn):
            return "Таблица predictions еще не создана."

        valid_filter = _valid_where_clause(conn)
        rows = conn.execute(
            """
            SELECT market_id, question, market_price, naked_p, dexter_p, forecast_timestamp
            FROM predictions
            WHERE 1=1
            """
            + valid_filter
            + """
            ORDER BY forecast_timestamp DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    if not rows:
        return "В базе пока нет прогнозов."

    rendered_rows = [f"📋 Последние {len(rows)} прогнозов:", ""]
    for index, (market_id, question, market_price, naked_p, dexter_p, _forecast_timestamp) in enumerate(rows, start=1):
        best_p = dexter_p if dexter_p is not None else naked_p
        edge = None if best_p is None or market_price is None else float(best_p) - float(market_price)
        edge_text = "" if edge is None else f" ({edge:+.0f}%)"
        rendered_rows.append(
            f"{index}. {_infer_icon(str(question))} {shorten(str(question), 52)}\n"
            f"   Толпа: {_format_percent(market_price)} → Pythia: {_format_percent(best_p)}{edge_text}\n"
            f"   детали: /details {market_id}"
        )

    return "\n".join(rendered_rows)


def render_last_predictions(limit: int = DEFAULT_LAST_PREDICTIONS) -> str:
    return format_last_predictions_report(DB_PATH, limit)


def _load_details_row(conn: sqlite3.Connection, reference: str) -> tuple[Any, ...] | None:
    valid_filter = _valid_where_clause(conn)

    if reference.isdigit():
        offset = max(int(reference) - 1, 0)
        rows = conn.execute(
            """
            SELECT market_id, question, market_price, dexter_p, naked_p, dexter_confidence,
                   dexter_reasoning, dexter_key_risk, dexter_base_rate, research_brief_json,
                   volume, end_date, forecast_timestamp
            FROM predictions
            WHERE 1=1
            """
            + valid_filter
            + """
            ORDER BY forecast_timestamp DESC, id DESC
            LIMIT 1 OFFSET ?
            """,
            (offset,),
        ).fetchall()
        return rows[0] if rows else None

    rows = conn.execute(
        """
        SELECT market_id, question, market_price, dexter_p, naked_p, dexter_confidence,
               dexter_reasoning, dexter_key_risk, dexter_base_rate, research_brief_json,
               volume, end_date, forecast_timestamp
        FROM predictions
        WHERE market_id = ?
        """
        + valid_filter
        + """
        ORDER BY forecast_timestamp DESC, id DESC
        LIMIT 1
        """,
        (reference,),
    ).fetchall()
    return rows[0] if rows else None


def format_details_report(db_path: Path | str, reference: str | None) -> str:
    db_path = Path(db_path)
    if not reference:
        return "Использование: /details <номер или market_id>"
    if not db_path.exists():
        return f"База данных не найдена: {db_path}"

    with sqlite3.connect(db_path) as conn:
        if not _predictions_table_exists(conn):
            return "Таблица predictions еще не создана."

        row = _load_details_row(conn, reference.strip())

    if not row:
        return f"Не удалось найти валидный прогноз по ссылке '{reference}'."

    (
        market_id,
        question,
        market_price,
        dexter_p,
        naked_p,
        dexter_confidence,
        dexter_reasoning,
        dexter_key_risk,
        dexter_base_rate,
        research_brief_json,
        volume,
        end_date,
        forecast_timestamp,
    ) = row

    best_p = dexter_p if dexter_p is not None else naked_p
    edge = None if best_p is None or market_price is None else float(best_p) - float(market_price)

    summary = ""
    key_facts: list[str] = []
    sources: list[str] = []
    if research_brief_json:
        try:
            research_payload = json.loads(str(research_brief_json))
            summary = str(research_payload.get("summary") or "").strip()
            key_facts = [str(item).strip() for item in research_payload.get("key_facts") or [] if str(item).strip()]
            sources = [str(item).strip() for item in research_payload.get("sources") or [] if str(item).strip()]
        except json.JSONDecodeError:
            summary = ""

    lines = [
        "🔍 Подробный анализ",
        "",
        f"{_infer_icon(str(question))} {question}",
        "",
        f"Рынок: {_format_percent(market_price)} | Pythia: {_format_percent(best_p)} | Расхождение: {edge:+.1f}%"
        if edge is not None
        else f"Рынок: {_format_percent(market_price)} | Pythia: {_format_percent(best_p)}",
        f"Уверенность: {_confidence_ru(dexter_confidence)}",
        f"Объём: ${float(volume or 0):,.0f}",
        f"Дедлайн: {str(end_date)[:10]}",
        f"Последний прогон: {str(forecast_timestamp)[:19]}",
    ]

    if dexter_base_rate:
        lines.extend(["", f"📈 Базовая частота: {dexter_base_rate}"])

    if dexter_reasoning:
        lines.extend(["", "📊 Рассуждение Pythia:", str(dexter_reasoning)])

    if dexter_key_risk:
        lines.extend(["", "⚠️ Ключевой риск:", str(dexter_key_risk)])

    if summary or key_facts:
        lines.extend(["", "🔎 Исследовательская сводка:"])
        if summary:
            lines.append(summary)
        if key_facts:
            lines.extend(f"- {fact}" for fact in key_facts[:5])

    if sources:
        lines.extend(["", f"Источники: {', '.join(sources[:3])}"])

    lines.extend(["", f"market_id: {market_id}"])
    return "\n".join(lines)


def render_details(reference: str | None) -> str:
    return format_details_report(DB_PATH, reference)


def render_last_predictions(limit: int = DEFAULT_LAST_PREDICTIONS) -> str:
    if not DB_PATH.exists():
        return f"База данных не найдена: {DB_PATH}"
    return format_last_predictions_report(DB_PATH, limit)


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
        oneoff = labels.get("com.docker.compose.oneoff")
        if project != "pythia":
            continue
        if str(oneoff).lower() == "true":
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
    issues = _collect_command_issues(stdout, stderr)

    if command_key == "forecast":
        if code == 0:
            return format_forecast_report(
                DB_PATH,
                research_status=_derive_research_status(issues),
                issues=issues,
            )
        return _render_command_failure("forecast", code, issues)

    if code == 0:
        lines = [f"✅ Команда {command_key} завершилась успешно."]
        if issues:
            lines.append("")
            lines.append("Предупреждения:")
            lines.extend(f"- {issue}" for issue in issues[:3])
        lines.append("")
        lines.append("Подробности: /logs")
        return "\n".join(lines)

    return _render_command_failure(command_key, code, issues)


def _run_day0_check() -> str:
    script = BASE_DIR / "scripts" / "day0_check.sh"
    if not script.exists():
        return f"Скрипт не найден: {script}"

    code, stdout, stderr = run_command(["bash", str(script)], timeout=3600)
    issues = _collect_command_issues(stdout, stderr)
    if code != 0:
        return _render_command_failure("day0", code, issues)
    return "\n".join(
        [
            "📦 Проверка доступности рынков завершена.",
            "",
            stdout.strip() or "Вывод пуст.",
        ]
    )


def _run_edge_check() -> str:
    from python.edge_calculator import check_edge

    result = check_edge(str(DB_PATH), min_samples=20)
    if result["status"] == "INSUFFICIENT_DATA":
        return (
            "📊 Оценка edge\n\n"
            f"Недостаточно данных: {result['n']} из {result['needed']} необходимых resolved-рынков.\n"
            "Продолжайте сбор данных."
        )

    emoji = {"GO": "🟢", "PIVOT": "🟡", "STOP": "🔴"}
    return "\n".join(
        [
            "📊 Оценка edge",
            "",
            f"Resolved-рынков: {result['n']}",
            "",
            "Brier score:",
            f"- Naked R1: {result['brier_naked']}",
            f"- Research+R1: {result['brier_dexter']}",
            f"- Рынок: {result['brier_market']}",
            "",
            "Edge vs рынок:",
            f"- Naked: {result['edge_naked_vs_market']:+.4f}",
            f"- Research+R1: {result['edge_dexter_vs_market']:+.4f}",
            f"- p-value Research+R1: {result['p_value_dexter']}",
            "",
            f"Решение: {emoji.get(result['decision'], '⚪')} {result['decision']}",
            result["interpretation"],
        ]
    )


def _render_command_failure(command_key: str, code: int, issues: list[str]) -> str:
    lines = [f"❌ Команда {command_key} завершилась с кодом {code}."]
    if issues:
        lines.append("")
        lines.append("Причины:")
        lines.extend(f"- {issue}" for issue in issues[:3])
    lines.append("")
    lines.append("Подробности: /logs")
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


def _collect_runtime_health_checks() -> list[str]:
    lines: list[str] = ["API:"]
    llm_line = _check_llm_backend()
    gamma_line = _check_gamma_api()
    exa_ok, exa_line = _check_exa_api()
    tavily_ok, tavily_line = _check_tavily_api()
    sqlite_ok, sqlite_line = _check_sqlite()

    lines.append(f"  {llm_line}")
    lines.append(f"  {gamma_line}")
    lines.append(f"  {exa_line}")
    lines.append(f"  {tavily_line}")
    lines.append("")
    lines.append("База данных:")
    lines.append(f"  {sqlite_line}")
    lines.append("")

    if exa_ok:
        lines.append("🔍 Исследование: ON (Exa)")
    elif tavily_ok:
        lines.append("🔍 Исследование: DEGRADED (Tavily fallback)")
    else:
        lines.append("🔍 Исследование: OFF")

    if llm_line.startswith("✅") and gamma_line.startswith("✅") and sqlite_ok and (exa_ok or tavily_ok):
        lines.append("")
        lines.append("Всё в порядке ✅")

    return lines


def _check_llm_backend() -> str:
    base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
    api_key = os.getenv("DEEPSEEK_API_KEY", "") or os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        return "❌ LLM backend: ключ не задан"

    backend_name = "LLM backend"
    if "groq.com" in base_url:
        backend_name = "Groq"
    elif "openrouter.ai" in base_url:
        backend_name = "OpenRouter"
    elif "deepseek.com" in base_url:
        backend_name = "DeepSeek"

    try:
        response = requests.get(
            f"{base_url}/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
        if response.status_code == 200:
            return f"✅ {backend_name}: доступен"
        return f"❌ {backend_name}: HTTP {response.status_code}"
    except Exception as exc:
        return f"❌ {backend_name}: {shorten(str(exc), 60)}"


def _check_gamma_api() -> str:
    try:
        response = requests.get(
            "https://gamma-api.polymarket.com/markets?limit=1",
            timeout=10,
        )
        if response.status_code == 200:
            return "✅ Polymarket Gamma: доступен"
        return f"❌ Polymarket Gamma: HTTP {response.status_code}"
    except Exception as exc:
        return f"❌ Polymarket Gamma: {shorten(str(exc), 60)}"


def _check_exa_api() -> tuple[bool, str]:
    api_key = os.getenv("EXA_API_KEY", "").strip()
    if not api_key:
        return False, "⚠️ Exa API: ключ не задан"

    try:
        response = requests.post(
            "https://api.exa.ai/search",
            json={"query": "test", "numResults": 1},
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
            timeout=10,
        )
        if response.status_code == 200:
            return True, "✅ Exa API: доступен"
        return False, f"❌ Exa API: HTTP {response.status_code}"
    except Exception as exc:
        return False, f"❌ Exa API: {shorten(str(exc), 60)}"


def _check_tavily_api() -> tuple[bool, str]:
    api_key = os.getenv("TAVILY_API_KEY", "").strip()
    if not api_key:
        return False, "⚠️ Tavily API: ключ не задан"

    try:
        response = requests.post(
            "https://api.tavily.com/search",
            json={"api_key": api_key, "query": "test", "max_results": 1},
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        if response.status_code == 200:
            return True, "✅ Tavily API: доступен"
        return False, f"❌ Tavily API: HTTP {response.status_code}"
    except Exception as exc:
        return False, f"❌ Tavily API: {shorten(str(exc), 60)}"


def _check_sqlite() -> tuple[bool, str]:
    if not DB_PATH.exists():
        return False, f"❌ SQLite: база не найдена ({DB_PATH})"
    try:
        with sqlite3.connect(DB_PATH) as conn:
            if not _predictions_table_exists(conn):
                return False, "❌ SQLite: таблица predictions ещё не создана"
            total = conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
            valid = conn.execute(
                f"SELECT COUNT(*) FROM predictions WHERE 1=1{_valid_where_clause(conn)}"
            ).fetchone()[0]
        return True, f"✅ SQLite: {valid} валидных записей ({total} всего)"
    except Exception as exc:
        return False, f"❌ SQLite: {shorten(str(exc), 60)}"


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
