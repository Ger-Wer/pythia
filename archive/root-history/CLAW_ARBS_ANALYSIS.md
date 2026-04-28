# Claw Arbs - Анализ для Pythia
## 2026-04-12

## Что это?

**Claw Arbs** - десктопное приложение для автоматизации арбитражных и value-ставок на рынках прогнозов (Kalshi, Polymarket) и в букмекерских конторах.

**Сайт:** https://clawarbs.com  
**Статус:** Альфа-тестирование (~100 пользователей)  
**Стек:** Python + FastAPI, React + TypeScript, Playwright, SQLite, Nuitka

## Ключевые технические решения

### 1. Парсинг в реальном времени (~20ms задержка)

**Как работает:**
- Для API: прямое WebSocket подключение к Kalshi/Polymarket
- Для сайтов без API: headless браузер (Playwright) + MutationObserver
- MutationObserver отслеживает изменения DOM в реальном времени
- Задержка ~20ms vs 500-2000ms у традиционных парсеров

**Применимость для Pythia:**
✅ **Можем использовать** - у нас уже есть Playwright для парсинга
✅ **MutationObserver** - отличная идея для отслеживания изменений на Kalshi
⚠️ **Но:** мы работаем серверно, а Claw Arbs - десктопное приложение

### 2. Point-and-click калибровка парсеров

**Как работает:**
- Визуальный мастер: пользователь кликает на ячейку с коэффициентом
- Алгоритм автоматически вычисляет CSS-селектор
- Настройки сохраняются в JSON
- Community Hub для обмена калибровками

**Применимость для Pythia:**
❌ **Не подходит** - у нас серверный бот без GUI
❌ **Не нужно** - мы работаем только с Kalshi API, не парсим сайты
✅ **Идея Community Hub** - интересна, но не критична

### 3. Strategy Agents (изолированные стратегии)

**Как работает:**
- Каждый агент = независимая "песочница"
- Свои настройки: виды спорта, пороги edge, лимиты риска
- 30+ параметров конфигурации
- Параллельная работа нескольких агентов

**Применимость для Pythia:**
✅ **ОЧЕНЬ ПОЛЕЗНО** - у нас уже есть SafeCompounder, можем добавить другие стратегии
✅ **Изоляция стратегий** - отличный паттерн для масштабирования
✅ **Независимые настройки** - каждая стратегия со своими лимитами

**Что взять:**
```python
# Текущая архитектура Pythia
class SafeCompounder:
    def __init__(self, config):
        self.max_position = config.max_position
        self.min_edge = config.min_edge
        # ...

# Можем расширить до:
class StrategyAgent(ABC):
    @abstractmethod
    async def evaluate_signal(self, signal: Signal) -> Decision
    
class SafeCompounder(StrategyAgent):
    # Текущая логика
    
class AggressiveScalper(StrategyAgent):
    # Новая стратегия для быстрых сделок
    
class LongTermValue(StrategyAgent):
    # Стратегия для долгосрочных позиций
```

### 4. 14-ступенчатый Safety Pipeline

**Как работает:**
- Kill switch (аварийное отключение)
- Naked leg tracking (защита от частичного исполнения)
- Pre-click verification (проверка цены перед сделкой)
- Freshness gates (отклонение устаревших данных >30s)
- Cooldowns (задержки между сделками)
- Deduplication (защита от дублей)
- Rate limiting (ограничение частоты)

**Применимость для Pythia:**
✅ **КРИТИЧЕСКИ ВАЖНО** - у нас уже есть базовая защита, но можно усилить
✅ **Pre-execution verification** - проверять цену перед ордером
✅ **Freshness gates** - отклонять сигналы старше N секунд
✅ **Naked leg tracking** - важно для арбитража (если добавим)

**Что взять:**
```python
# Добавить в SafeCompounder
class SafetyPipeline:
    async def verify_signal_freshness(self, signal: Signal) -> bool:
        """Отклонить сигнал если старше 30 секунд"""
        age = datetime.now() - signal.created_at
        return age.total_seconds() < 30
    
    async def pre_execution_check(self, market_id: str, expected_price: float) -> bool:
        """Проверить цену перед ордером"""
        current_price = await self.kalshi.get_current_price(market_id)
        drift = abs(current_price - expected_price) / expected_price
        return drift < 0.03  # Отклонить если цена сдвинулась >3%
    
    async def check_naked_legs(self) -> bool:
        """Проверить нет ли незакрытых позиций"""
        # Для будущего арбитража
        pass
```

### 5. Sniper Mode (синтетические лимитные ордера)

**Как работает:**
- Пользователь задает целевую цену
- Бот мониторит рынок
- Исполняет ордер когда цена достигает порога
- Auto-Sniper Re-fire: докупает позицию если edge растет

**Применимость для Pythia:**
✅ **ПОЛЕЗНО** - у Kalshi нет лимитных ордеров
✅ **Auto-compounding** - похоже на нашу логику компаундинга
⚠️ **Но:** требует постоянного мониторинга цен

**Что взять:**
```python
class SniperMode:
    async def monitor_target_price(self, market_id: str, target_price: float):
        """Ждать пока цена достигнет целевого уровня"""
        while True:
            current_price = await self.kalshi.get_current_price(market_id)
            if current_price >= target_price:
                await self.execute_order(market_id, current_price)
                break
            await asyncio.sleep(1)
    
    async def auto_refire(self, position: Position):
        """Докупить позицию если edge вырос"""
        current_edge = await self.calculate_edge(position.market_id)
        if current_edge > position.initial_edge + 0.05:  # +5%
            await self.add_to_position(position)
```

### 6. Zero-cloud архитектура

**Как работает:**
- Все локально: SQLite, зашифрованное хранилище ключей
- Никакого облачного бэкенда
- Данные не покидают машину пользователя
- Paper trading по умолчанию

**Применимость для Pythia:**
❌ **НЕ ПОДХОДИТ** - мы серверный бот, работаем 24/7 в облаке
❌ **Архитектура несовместима** - у нас VPS + PostgreSQL + Telegram

## Что НЕ подходит для Pythia

### 1. Десктопная архитектура
- Claw Arbs = локальное приложение
- Pythia = серверный бот 24/7
- **Вывод:** архитектурно несовместимы

### 2. Point-and-click калибровка
- Claw Arbs = GUI для настройки парсеров
- Pythia = работает только с Kalshi API
- **Вывод:** не нужно

### 3. Community Hub
- Claw Arbs = краудсорсинг калибровок
- Pythia = закрытый проприетарный бот
- **Вывод:** не применимо

### 4. Поддержка множества площадок
- Claw Arbs = Kalshi, Polymarket, Cloudbet, букмекеры
- Pythia = только Kalshi
- **Вывод:** избыточно

### 5. LLM в торговой логике
- Claw Arbs **принципиально избегает** LLM в реальном времени
- Причина: медленно, недетерминированно
- Pythia использует LLM для исследований, но не в торговле
- **Вывод:** мы уже правильно делаем

## Риски и ограничения Claw Arbs

### 1. Selector Drift (поломка CSS-селекторов)
- При обновлении дизайна сайтов селекторы ломаются "без звука"
- Приводит к сбору неверных данных
- Решение: Community Hub + auto-heal (в планах)
- **Для Pythia:** не актуально, работаем с API

### 2. Альфа-версия
- Только ~100 пользователей
- Не проверено на больших масштабах
- Может быть нестабильно
- **Для Pythia:** не критично, берем только идеи

### 3. Компромисс скорость/безопасность
- Многоступенчатые проверки = потеря миллисекунд
- Нужен баланс
- **Для Pythia:** у нас не HFT, можем позволить проверки

## Конкретные рекомендации для Pythia

### ✅ Что взять ОБЯЗАТЕЛЬНО

#### 1. Strategy Agents паттерн
```python
# Создать базовый класс для стратегий
class StrategyAgent(ABC):
    def __init__(self, config: StrategyConfig):
        self.config = config
        self.limits = config.limits
        self.thresholds = config.thresholds
    
    @abstractmethod
    async def evaluate_signal(self, signal: Signal) -> Decision
    
    @abstractmethod
    async def manage_position(self, position: Position) -> Action

# Рефакторить SafeCompounder
class SafeCompounder(StrategyAgent):
    # Текущая логика остается
    pass

# Добавить новые стратегии
class AggressiveScalper(StrategyAgent):
    """Быстрые сделки на малых edge"""
    pass

class LongTermValue(StrategyAgent):
    """Долгосрочные позиции на больших edge"""
    pass
```

#### 2. Safety Pipeline
```python
class SafetyPipeline:
    def __init__(self):
        self.kill_switch = False
        self.cooldowns = {}
        self.recent_orders = []
    
    async def pre_execution_checks(self, signal: Signal) -> tuple[bool, str]:
        """Все проверки перед ордером"""
        
        # 1. Kill switch
        if self.kill_switch:
            return False, "Kill switch activated"
        
        # 2. Freshness check
        if not await self.check_freshness(signal):
            return False, "Signal too old"
        
        # 3. Price verification
        if not await self.verify_price(signal):
            return False, "Price drifted"
        
        # 4. Cooldown check
        if not await self.check_cooldown(signal.market_id):
            return False, "Cooldown active"
        
        # 5. Deduplication
        if await self.is_duplicate(signal):
            return False, "Duplicate signal"
        
        return True, "All checks passed"
    
    async def check_freshness(self, signal: Signal) -> bool:
        age = datetime.now() - signal.created_at
        return age.total_seconds() < 30
    
    async def verify_price(self, signal: Signal) -> bool:
        current = await kalshi.get_current_price(signal.market_id)
        drift = abs(current - signal.expected_price) / signal.expected_price
        return drift < 0.03  # 3% tolerance
```

#### 3. Pre-execution price verification
```python
# Добавить в SafeCompounder перед каждым ордером
async def execute_trade(self, signal: Signal):
    # Проверить цену прямо перед ордером
    current_price = await self.kalshi.get_current_price(signal.market_id)
    expected_price = signal.yes_price if signal.side == "yes" else signal.no_price
    
    drift = abs(current_price - expected_price) / expected_price
    if drift > 0.03:  # 3% tolerance
        logger.warning(f"Price drifted {drift:.2%}, canceling order")
        return
    
    # Продолжить с ордером
    await self.place_order(signal)
```

### ⚠️ Что взять ОПЦИОНАЛЬНО

#### 1. Sniper Mode (если нужны лимитные ордера)
```python
class SniperMode:
    async def wait_for_target_price(self, market_id: str, target_price: float):
        """Ждать пока цена достигнет целевого уровня"""
        while True:
            current = await self.kalshi.get_current_price(market_id)
            if current >= target_price:
                return True
            await asyncio.sleep(1)
```

#### 2. Auto-refire (докупка позиций)
```python
# Расширить логику компаундинга
async def check_refire_opportunity(self, position: Position):
    """Докупить если edge вырос"""
    current_edge = await self.calculate_edge(position.market_id)
    initial_edge = position.metadata.get("initial_edge", 0)
    
    if current_edge > initial_edge + 0.05:  # +5% рост edge
        logger.info(f"Edge increased from {initial_edge:.2%} to {current_edge:.2%}")
        await self.add_to_position(position)
```

#### 3. MutationObserver для WebSocket
```python
# Если добавим WebSocket подключение к Kalshi
class RealtimeMarketMonitor:
    async def subscribe_to_market(self, market_id: str):
        """Подписаться на изменения цен через WebSocket"""
        async with websockets.connect(KALSHI_WS_URL) as ws:
            await ws.send(json.dumps({"subscribe": market_id}))
            async for message in ws:
                data = json.loads(message)
                await self.on_price_update(data)
```

### ❌ Что НЕ брать

1. **Point-and-click калибровка** - не нужна, работаем с API
2. **Community Hub** - не применимо для закрытого бота
3. **Десктопная архитектура** - несовместимо с серверным ботом
4. **Поддержка множества площадок** - работаем только с Kalshi
5. **GUI** - у нас Telegram интерфейс

## План внедрения

### Фаза 1: Safety Pipeline (1-2 дня)
1. Создать класс `SafetyPipeline`
2. Добавить freshness checks
3. Добавить pre-execution price verification
4. Добавить cooldowns и deduplication
5. Интегрировать в SafeCompounder

### Фаза 2: Strategy Agents (3-5 дней)
1. Создать базовый класс `StrategyAgent`
2. Рефакторить `SafeCompounder` как наследник
3. Добавить конфигурацию для стратегий
4. Создать 1-2 новые стратегии (опционально)

### Фаза 3: Advanced Features (опционально)
1. Sniper Mode для лимитных ордеров
2. Auto-refire для докупки позиций
3. WebSocket для real-time мониторинга

## Выводы

**Claw Arbs - отличный источник идей для улучшения Pythia.**

### Главные takeaways:

1. **Strategy Agents** - паттерн изоляции стратегий для масштабирования
2. **Safety Pipeline** - многоступенчатые проверки перед сделками
3. **Pre-execution verification** - проверка цены перед ордером
4. **Freshness gates** - отклонение устаревших сигналов
5. **Sniper Mode** - синтетические лимитные ордера

### Что НЕ применимо:

1. Десктопная архитектура
2. Point-and-click калибровка
3. Community Hub
4. Поддержка множества площадок

### Приоритет внедрения:

1. **Высокий:** Safety Pipeline + Pre-execution checks
2. **Средний:** Strategy Agents паттерн
3. **Низкий:** Sniper Mode, Auto-refire

---

**Статус:** Исследование завершено  
**Следующий шаг:** Внедрить Safety Pipeline в SafeCompounder
