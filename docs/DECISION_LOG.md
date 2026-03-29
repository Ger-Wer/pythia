# Pythia Decision Log

## Template
| Date | Decision | Data | Rationale |
|------|----------|------|-----------|
| YYYY-MM-DD | GO/PIVOT/STOP/CHANGE | n=X, Brier=Y | Why |

## Decisions
| 2026-03-29 | CHANGE | models: research=`llama-3.3-70b-versatile`, synthesis=`openai/gpt-oss-120b`; valid_n=5 | Перевели production research на более сильную Groq-модель, ограничили отбор рынков горизонтом 60 дней и начали новый чистый цикл сбора данных с `forecast_timestamp >= 2026-03-29T19:46:00.115Z`. |
