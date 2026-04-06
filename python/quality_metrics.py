from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from python.edge_calculator import check_edge


def prediction_columns(conn: sqlite3.Connection) -> set[str]:
    table_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='predictions'"
    ).fetchone()
    if not table_exists:
        return set()
    return {str(row[1]) for row in conn.execute("PRAGMA table_info(predictions)").fetchall()}


def valid_filter(columns: set[str]) -> str:
    if "is_valid" not in columns:
        return ""
    return " AND is_valid = 1"


def _latest_calibration_image(log_dir: Path) -> str | None:
    if not log_dir.exists():
        return None
    images = sorted(Path(log_dir).glob("calibration_*.png"))
    if not images:
        return None
    return str(images[-1].relative_to(log_dir.parent))


def _glossary() -> list[dict[str, str]]:
    return [
        {
            "id": "brier",
            "label": "Brier score",
            "what": "Средняя квадратичная ошибка вероятности по resolved-рынкам.",
            "why": "Показывает, насколько вероятности Pythia близки к факту и рынку.",
            "how": "Ниже лучше. Сравнивайте Research+R1, Naked и рынок между собой.",
        },
        {
            "id": "edge",
            "label": "Edge vs market",
            "what": "Разница качества между Pythia и рыночной ценой.",
            "why": "Помогает понять, есть ли у модели устойчивое преимущество.",
            "how": "Положительный edge означает, что Pythia лучше рынка; отрицательный — хуже.",
        },
        {
            "id": "decision",
            "label": "Decision",
            "what": "Операционная рекомендация по текущему quality snapshot.",
            "why": "Сводит метрики в простое действие для оператора.",
            "how": "COLLECT = собирать данные, GO = продолжать, PIVOT = менять подход, STOP = остановиться.",
        },
    ]


def _category_breakdown(conn: sqlite3.Connection, columns: set[str]) -> list[dict[str, Any]]:
    if "category" not in columns or "outcome" not in columns:
        return []

    filter_suffix = valid_filter(columns)
    rows = conn.execute(
        """
        SELECT
            COALESCE(category, 'Unknown') AS category,
            COUNT(*) AS resolved,
            ROUND(AVG(CASE
                WHEN dexter_p IS NOT NULL THEN (dexter_p / 100.0 - outcome) * (dexter_p / 100.0 - outcome)
            END), 4) AS brier_research,
            ROUND(AVG((market_price / 100.0 - outcome) * (market_price / 100.0 - outcome)), 4) AS brier_market
        FROM predictions
        WHERE outcome IN (0, 1)
        """
        + filter_suffix
        + """
        GROUP BY COALESCE(category, 'Unknown')
        ORDER BY resolved DESC, category ASC
        """
    ).fetchall()
    return [
        {
            "category": str(row[0]),
            "resolved": int(row[1]),
            "brier_research": float(row[2]) if row[2] is not None else None,
            "brier_market": float(row[3]) if row[3] is not None else None,
        }
        for row in rows
    ]


def load_quality_snapshot(db_path: Path | str, log_dir: Path | str) -> dict[str, Any]:
    path = Path(db_path)
    log_dir_path = Path(log_dir)
    minimum_needed = 20

    def insufficient_payload(*, summary: str, category_breakdown: list[dict[str, Any]], resolved_count: int) -> dict[str, Any]:
        return {
            "resolved_predictions": resolved_count,
            "minimum_needed": minimum_needed,
            "status": "INSUFFICIENT_DATA",
            "decision": "COLLECT",
            "summary": summary,
            "metrics": {
                "brier_research": None,
                "brier_naked": None,
                "brier_market": None,
                "edge_research_vs_market": None,
                "edge_naked_vs_market": None,
            },
            "category_breakdown": category_breakdown,
            "calibration_image": _latest_calibration_image(log_dir_path),
            "glossary": _glossary(),
        }

    if not path.exists():
        return insufficient_payload(
            summary="База данных ещё не создана, поэтому quality snapshot пока собрать нельзя.",
            category_breakdown=[],
            resolved_count=0,
        )

    with sqlite3.connect(path) as conn:
        columns = prediction_columns(conn)
        if not columns:
            return insufficient_payload(
                summary="Таблица predictions ещё не готова, поэтому quality snapshot пока недоступен.",
                category_breakdown=[],
                resolved_count=0,
            )

        required_columns = {"outcome", "dexter_p", "naked_p", "market_price"}
        if not required_columns.issubset(columns):
            return insufficient_payload(
                summary="Таблица predictions ещё не содержит все колонки для расчёта quality snapshot.",
                category_breakdown=[],
                resolved_count=0,
            )

        filter_suffix = valid_filter(columns)
        total_resolved_row = conn.execute(
            "SELECT COUNT(*) FROM predictions WHERE outcome IN (0, 1)" + filter_suffix
        ).fetchone()
        total_resolved = (
            int(total_resolved_row[0]) if total_resolved_row and total_resolved_row[0] is not None else 0
        )
        category_breakdown = _category_breakdown(conn, columns)

        if total_resolved < minimum_needed:
            missing = minimum_needed - total_resolved
            return insufficient_payload(
                summary=(
                    f"Пока есть {total_resolved} resolved-рынков, нужно ещё {missing}, "
                    "чтобы честно считать качество."
                ),
                category_breakdown=category_breakdown,
                resolved_count=total_resolved,
            )

    edge_result = check_edge(str(path), min_samples=minimum_needed)
    if edge_result.get("status") != "COMPLETE":
        usable_rows = int(edge_result.get("n", 0) or 0)
        missing = minimum_needed - usable_rows
        summary = (
            f"Есть {total_resolved} resolved-рынков, но только {usable_rows} подходят для расчёта edge; "
            f"нужно ещё {missing}, чтобы перейти к quality decision."
        )
        return {
            "resolved_predictions": total_resolved,
            "minimum_needed": minimum_needed,
            "status": "INSUFFICIENT_DATA",
            "decision": "COLLECT",
            "summary": summary,
            "metrics": {
                "brier_research": None,
                "brier_naked": None,
                "brier_market": None,
                "edge_research_vs_market": None,
                "edge_naked_vs_market": None,
            },
            "category_breakdown": category_breakdown,
            "calibration_image": _latest_calibration_image(log_dir_path),
            "glossary": _glossary(),
        }

    return {
        "resolved_predictions": total_resolved,
        "minimum_needed": minimum_needed,
        "status": "COMPLETE",
        "decision": str(edge_result.get("decision", "PIVOT")),
        "summary": (
            "Качество достаточно для операционного решения: "
            f"{edge_result.get('decision', 'PIVOT')} при {total_resolved} resolved-рынках."
        ),
        "metrics": {
            "brier_research": edge_result.get("brier_dexter"),
            "brier_naked": edge_result.get("brier_naked"),
            "brier_market": edge_result.get("brier_market"),
            "edge_research_vs_market": edge_result.get("edge_dexter_vs_market"),
            "edge_naked_vs_market": edge_result.get("edge_naked_vs_market"),
        },
        "category_breakdown": category_breakdown,
        "calibration_image": _latest_calibration_image(log_dir_path),
        "glossary": _glossary(),
    }
