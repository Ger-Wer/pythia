# Pythia Decision Log

## Template
| Date | Decision | Data | Rationale |
|------|----------|------|-----------|
| YYYY-MM-DD | GO/PIVOT/STOP/CHANGE | n=X, Brier=Y | Why |

## Decisions
| 2026-03-29 | CHANGE | models: research=`llama-3.3-70b-versatile`, synthesis=`openai/gpt-oss-120b`; valid_n=5 | Перевели production research на более сильную Groq-модель, ограничили отбор рынков горизонтом 60 дней и начали новый чистый цикл сбора данных с `forecast_timestamp >= 2026-03-29T19:46:00.115Z`. |
| 2026-04-04 | CHANGE | Catalyst + Spillover v1 | Расширили `research_brief_json` новыми полями (`catalyst_type`, `primary_driver`, `second_order_effects`, `disconfirming_signal`) и начали показывать их оператору в `/details`, не меняя схему SQLite и не добавляя новый scoring feature. |
| 2026-04-04 | CHANGE | Operator Dashboard v1 | Добавили `pythia-dashboard` (React SPA + FastAPI API), оставив Telegram как fallback-канал. Dashboard публикуется только на `127.0.0.1:8787` и должен отдаваться наружу через reverse proxy на `b1w.duckdns.org`. |
