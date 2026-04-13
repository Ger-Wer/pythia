# Pythia SafetyPipeline - Отчет о деплое
## 2026-04-12 12:05 UTC

## ✅ Деплой завершен успешно

### Сервер: Amper (144.24.254.226)
- **Пользователь:** pythia
- **Путь:** /home/pythia/pythia
- **Docker:** 3 контейнера (scheduler, telegram, dashboard)

### Выполненные действия:

1. ✅ **Git pull** - обновлен код с GitHub
   ```
   e128bb4..a2f8340  main -> origin/main
   +847 строк, -28 строк
   ```

2. ✅ **Обновлен .env** - добавлены переменные SafetyPipeline
   ```bash
   SAFETY_MAX_SIGNAL_AGE_SECONDS=30
   SAFETY_PRICE_DRIFT_TOLERANCE=0.03
   SAFETY_COOLDOWN_SECONDS=60
   SAFETY_DEDUP_WINDOW_MINUTES=5
   ```

3. ✅ **Docker rebuild** - пересобран образ pythia:latest
   - Новый код включен
   - SafetyPipeline интегрирован

4. ✅ **Контейнеры перезапущены**
   - pythia-scheduler: healthy
   - pythia-telegram: healthy
   - pythia-dashboard: healthy

### Статус контейнеров:

```
CONTAINER ID   IMAGE          STATUS                    PORTS
3bfa369224ff   f73f83d49462   Up 3 minutes (healthy)    pythia-telegram
e0f0d51e667c   f73f83d49462   Up 3 minutes (healthy)    pythia-scheduler
97d5b96b45a9   f73f83d49462   Up 3 minutes (healthy)    pythia-dashboard
```

### Расписание запусков:

- **Forecast:** 6:00 UTC ежедневно (следующий: завтра в 6:00)
- **Watchlist refresh:** каждые 6 часов (следующий: 18:00 сегодня)
- **Watch alerts:** каждые 6 часов + 10 минут
- **Resolve:** 18:00 UTC ежедневно
- **Weekly:** 20:00 UTC по воскресеньям

### Что проверить при следующем forecast (завтра в 6:00):

1. **Логи SafetyPipeline**
   ```bash
   ssh amper "sudo docker logs pythia-scheduler | grep SafetyPipeline"
   ```

2. **Проверки работают**
   - Kill Switch: off
   - Freshness Check: отклоняет сигналы >30s
   - Price Drift Check: проверяет цену через Polymarket API
   - Cooldown: 60s между сделками
   - Deduplication: нет дублей в 5 минут

3. **Telegram уведомления**
   - Приходят при отклонении сделок
   - Формат корректный
   - Содержат детали (market, price, reason)

4. **Позиции открываются**
   - Shadow позиции создаются
   - Логи: "Trade approved"

### Команды для мониторинга:

```bash
# Проверить статус контейнеров
ssh amper "sudo docker ps | grep pythia"

# Логи scheduler (где SafetyPipeline)
ssh amper "sudo docker logs pythia-scheduler --tail 100"

# Логи с фильтром SafetyPipeline
ssh amper "sudo docker logs pythia-scheduler | grep -i safety"

# Проверить health
ssh amper "sudo docker inspect pythia-scheduler | grep -A 5 Health"

# Проверить .env
ssh amper "sudo -u pythia bash -c 'cd /home/pythia/pythia && cat .env | grep SAFETY'"
```

### Метрики для отслеживания:

После первого forecast запуска проверить:
- Сколько сигналов прошло проверки
- Сколько отклонено (и по каким причинам)
- Работает ли Polymarket API
- Приходят ли Telegram уведомления

### Риски и митигация:

1. **Polymarket API может быть медленным**
   - Митигация: retry с backoff уже есть
   - Мониторинг: проверить задержки в логах

2. **Слишком строгие проверки**
   - Митигация: все пороги конфигурируемы через .env
   - Можно изменить без пересборки

3. **Первый запуск может выявить баги**
   - Митигация: это tier-1, не production
   - Можно быстро откатить если что-то пойдет не так

### Откат (если нужно):

```bash
# Вернуться к предыдущей версии
ssh amper "sudo -u pythia bash -c 'cd /home/pythia/pythia && git checkout e128bb4'"

# Пересобрать образ
ssh amper "sudo -u pythia bash -c 'cd /home/pythia/pythia && docker build -t pythia:latest .'"

# Перезапустить контейнеры
ssh amper "sudo docker restart pythia-scheduler pythia-telegram pythia-dashboard"
```

### Следующие шаги:

1. **Сегодня 18:00 UTC** - watchlist-refresh (SafetyPipeline не задействован)
2. **Завтра 6:00 UTC** - forecast (первый запуск SafetyPipeline!)
3. **Завтра 7:00 UTC** - проверить логи и результаты
4. **Через 24 часа** - если все ок, можно считать деплой успешным

### Файлы на сервере:

```
/home/pythia/pythia/
├── src/services/
│   ├── safety_pipeline.ts          (новый, 285 строк)
│   ├── safety_pipeline.test.ts     (новый, 380 строк)
│   ├── telegram_alerts.ts          (новый, 48 строк)
│   └── decision_core.ts            (изменен, +53 строки)
├── src/
│   ├── main.ts                     (изменен, +1 строка)
│   └── config.ts                   (изменен, +5 строк)
├── .env                            (изменен, +5 строк)
└── .env.example                    (изменен, +4 строки)
```

## Итог

✅ **Деплой успешно завершен**
- Код обновлен
- Конфигурация добавлена
- Docker образ пересобран
- Контейнеры перезапущены и healthy

⏳ **Ожидание первого запуска**
- Следующий forecast: завтра в 6:00 UTC
- SafetyPipeline будет протестирован в боевых условиях

📊 **Мониторинг**
- Проверить логи после forecast
- Убедиться что проверки работают
- Проверить Telegram уведомления

---

**Дата деплоя:** 2026-04-12 12:05 UTC  
**Версия:** a2f8340  
**Сервер:** Amper (pythia@144.24.254.226)  
**Статус:** ✅ DEPLOYED & HEALTHY
