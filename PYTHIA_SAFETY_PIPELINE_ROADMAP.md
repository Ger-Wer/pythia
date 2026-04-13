# Pythia Safety Pipeline - Дорожная карта внедрения
## 2026-04-12

## Цель

Внедрить многоступенчатую систему безопасности для Pythia на основе лучших практик из Claw Arbs, чтобы минимизировать риски ошибочных сделок и улучшить надежность торговой логики.

## Контекст

**Текущее состояние:**
- SafeCompounder имеет базовую защиту (лимиты позиций, проверка edge)
- Нет проверки свежести сигналов
- Нет pre-execution price verification
- Нет защиты от дубликатов
- Нет cooldowns между сделками
- Нет централизованного safety pipeline

**Что берем из Claw Arbs:**
1. 14-ступенчатый safety pipeline
2. Pre-execution price verification (drift <3%)
3. Freshness gates (отклонение сигналов >30s)
4. Cooldowns и deduplication
5. Strategy Agents паттерн для изоляции стратегий

## Фазы внедрения

### Фаза 1: Safety Pipeline Core (Приоритет: P0)
**Срок:** 1-2 дня  
**Цель:** Создать базовую инфраструктуру safety pipeline

#### Задачи:

1. **Создать класс SafetyPipeline**
   - Файл: `src/safety/pipeline.py`
   - Централизованная система проверок перед сделками
   - Kill switch для аварийного отключения
   - Логирование всех проверок

2. **Freshness checks**
   - Отклонять сигналы старше 30 секунд
   - Конфигурируемый порог через settings
   - Метрика: `signal_age_seconds`

3. **Pre-execution price verification**
   - Проверять цену перед каждым ордером
   - Отклонять если drift >3%
   - Конфигурируемый tolerance через settings
   - Метрика: `price_drift_percentage`

4. **Cooldowns**
   - Задержка между сделками на одном рынке
   - По умолчанию: 60 секунд
   - Конфигурируемо через settings
   - Метрика: `cooldown_violations`

5. **Deduplication**
   - Отслеживать последние N сигналов
   - Отклонять дубликаты по (market_id, side, timestamp)
   - Окно дедупликации: 5 минут
   - Метрика: `duplicate_signals`

#### Структура кода:

```python
# src/safety/pipeline.py
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple
import logging

logger = logging.getLogger(__name__)

@dataclass
class SafetyCheckResult:
    passed: bool
    reason: str
    check_name: str
    metadata: Dict = None

class SafetyPipeline:
    """Многоступенчатая система проверок перед исполнением сделок"""
    
    def __init__(self, config):
        self.config = config
        self.kill_switch = False
        self.cooldowns: Dict[str, datetime] = {}
        self.recent_signals: List[Dict] = []
        self.max_signal_age = timedelta(seconds=config.get('max_signal_age_seconds', 30))
        self.price_drift_tolerance = config.get('price_drift_tolerance', 0.03)
        self.cooldown_seconds = config.get('cooldown_seconds', 60)
        self.dedup_window = timedelta(minutes=5)
    
    async def pre_execution_checks(
        self, 
        signal: Signal,
        kalshi_client
    ) -> Tuple[bool, str]:
        """Выполнить все проверки перед ордером"""
        
        checks = [
            await self._check_kill_switch(),
            await self._check_freshness(signal),
            await self._check_price_drift(signal, kalshi_client),
            await self._check_cooldown(signal.market_id),
            await self._check_duplicate(signal)
        ]
        
        # Логировать все проверки
        for check in checks:
            logger.info(
                f"Safety check: {check.check_name} - "
                f"{'PASSED' if check.passed else 'FAILED'} - {check.reason}"
            )
            
            if not check.passed:
                return False, f"{check.check_name}: {check.reason}"
        
        return True, "All safety checks passed"
    
    async def _check_kill_switch(self) -> SafetyCheckResult:
        """Проверка аварийного выключателя"""
        if self.kill_switch:
            return SafetyCheckResult(
                passed=False,
                reason="Kill switch is activated",
                check_name="kill_switch"
            )
        return SafetyCheckResult(
            passed=True,
            reason="Kill switch is off",
            check_name="kill_switch"
        )
    
    async def _check_freshness(self, signal: Signal) -> SafetyCheckResult:
        """Проверка свежести сигнала"""
        age = datetime.now() - signal.created_at
        
        if age > self.max_signal_age:
            return SafetyCheckResult(
                passed=False,
                reason=f"Signal too old: {age.total_seconds():.1f}s > {self.max_signal_age.total_seconds()}s",
                check_name="freshness",
                metadata={"age_seconds": age.total_seconds()}
            )
        
        return SafetyCheckResult(
            passed=True,
            reason=f"Signal fresh: {age.total_seconds():.1f}s",
            check_name="freshness",
            metadata={"age_seconds": age.total_seconds()}
        )
    
    async def _check_price_drift(
        self, 
        signal: Signal,
        kalshi_client
    ) -> SafetyCheckResult:
        """Проверка изменения цены перед ордером"""
        try:
            # Получить текущую цену с рынка
            current_price = await kalshi_client.get_current_price(
                signal.market_id,
                signal.side
            )
            
            expected_price = signal.yes_price if signal.side == "yes" else signal.no_price
            drift = abs(current_price - expected_price) / expected_price
            
            if drift > self.price_drift_tolerance:
                return SafetyCheckResult(
                    passed=False,
                    reason=f"Price drifted {drift:.2%} > {self.price_drift_tolerance:.2%}",
                    check_name="price_drift",
                    metadata={
                        "expected_price": expected_price,
                        "current_price": current_price,
                        "drift_percentage": drift
                    }
                )
            
            return SafetyCheckResult(
                passed=True,
                reason=f"Price drift acceptable: {drift:.2%}",
                check_name="price_drift",
                metadata={
                    "expected_price": expected_price,
                    "current_price": current_price,
                    "drift_percentage": drift
                }
            )
        except Exception as e:
            logger.error(f"Error checking price drift: {e}")
            return SafetyCheckResult(
                passed=False,
                reason=f"Failed to verify price: {str(e)}",
                check_name="price_drift"
            )
    
    async def _check_cooldown(self, market_id: str) -> SafetyCheckResult:
        """Проверка cooldown между сделками"""
        if market_id in self.cooldowns:
            last_trade = self.cooldowns[market_id]
            time_since = datetime.now() - last_trade
            cooldown_remaining = timedelta(seconds=self.cooldown_seconds) - time_since
            
            if cooldown_remaining.total_seconds() > 0:
                return SafetyCheckResult(
                    passed=False,
                    reason=f"Cooldown active: {cooldown_remaining.total_seconds():.1f}s remaining",
                    check_name="cooldown",
                    metadata={"cooldown_remaining_seconds": cooldown_remaining.total_seconds()}
                )
        
        return SafetyCheckResult(
            passed=True,
            reason="No cooldown active",
            check_name="cooldown"
        )
    
    async def _check_duplicate(self, signal: Signal) -> SafetyCheckResult:
        """Проверка дубликатов сигналов"""
        # Очистить старые сигналы
        cutoff = datetime.now() - self.dedup_window
        self.recent_signals = [
            s for s in self.recent_signals 
            if s['timestamp'] > cutoff
        ]
        
        # Проверить дубликат
        signal_key = {
            'market_id': signal.market_id,
            'side': signal.side,
            'timestamp': signal.created_at
        }
        
        for recent in self.recent_signals:
            if (recent['market_id'] == signal.market_id and
                recent['side'] == signal.side and
                abs((recent['timestamp'] - signal.created_at).total_seconds()) < 60):
                return SafetyCheckResult(
                    passed=False,
                    reason="Duplicate signal detected",
                    check_name="deduplication",
                    metadata={"duplicate_of": recent}
                )
        
        return SafetyCheckResult(
            passed=True,
            reason="No duplicate found",
            check_name="deduplication"
        )
    
    def record_trade(self, market_id: str, signal: Signal):
        """Записать сделку для cooldown и deduplication"""
        self.cooldowns[market_id] = datetime.now()
        self.recent_signals.append({
            'market_id': signal.market_id,
            'side': signal.side,
            'timestamp': signal.created_at
        })
    
    def activate_kill_switch(self, reason: str):
        """Активировать аварийное отключение"""
        self.kill_switch = True
        logger.critical(f"KILL SWITCH ACTIVATED: {reason}")
    
    def deactivate_kill_switch(self):
        """Деактивировать аварийное отключение"""
        self.kill_switch = False
        logger.info("Kill switch deactivated")
```

#### Интеграция в SafeCompounder:

```python
# src/strategies/safe_compounder.py
from safety.pipeline import SafetyPipeline

class SafeCompounder:
    def __init__(self, config):
        # ... existing code ...
        self.safety_pipeline = SafetyPipeline(config.get('safety', {}))
    
    async def execute_trade(self, signal: Signal):
        """Исполнить сделку с проверками безопасности"""
        
        # Выполнить все проверки безопасности
        passed, reason = await self.safety_pipeline.pre_execution_checks(
            signal,
            self.kalshi
        )
        
        if not passed:
            logger.warning(f"Trade rejected by safety pipeline: {reason}")
            await self.telegram.send_message(
                f"⚠️ Сделка отклонена: {reason}\n"
                f"Market: {signal.market_id}\n"
                f"Side: {signal.side}"
            )
            return
        
        # Продолжить с ордером
        try:
            order = await self.place_order(signal)
            
            # Записать сделку для cooldown/dedup
            self.safety_pipeline.record_trade(signal.market_id, signal)
            
            logger.info(f"Trade executed successfully: {order}")
        except Exception as e:
            logger.error(f"Trade execution failed: {e}")
            # Возможно активировать kill switch при критических ошибках
            if self._is_critical_error(e):
                self.safety_pipeline.activate_kill_switch(str(e))
```

#### Конфигурация:

```yaml
# config/settings.yaml
safety:
  max_signal_age_seconds: 30
  price_drift_tolerance: 0.03  # 3%
  cooldown_seconds: 60
  dedup_window_minutes: 5
```

#### Метрики:

```python
# Добавить в систему метрик
safety_checks_total = Counter('safety_checks_total', 'Total safety checks', ['check_name', 'result'])
safety_check_failures = Counter('safety_check_failures', 'Failed safety checks', ['check_name', 'reason'])
signal_age_seconds = Histogram('signal_age_seconds', 'Age of signals in seconds')
price_drift_percentage = Histogram('price_drift_percentage', 'Price drift percentage')
```

---

### Фаза 2: Strategy Agents Pattern (Приоритет: P1)
**Срок:** 3-5 дней  
**Цель:** Рефакторинг архитектуры для поддержки множественных стратегий

#### Задачи:

1. **Создать базовый класс StrategyAgent**
   - Файл: `src/strategies/base.py`
   - Абстрактные методы: `evaluate_signal()`, `manage_position()`
   - Изолированная конфигурация для каждой стратегии

2. **Рефакторить SafeCompounder**
   - Наследовать от StrategyAgent
   - Сохранить текущую логику
   - Добавить изоляцию конфигурации

3. **Создать конфигурацию стратегий**
   - Файл: `config/strategies.yaml`
   - Независимые настройки для каждой стратегии
   - Лимиты, пороги, правила

4. **Добавить систему выбора стратегии**
   - Роутинг сигналов к подходящей стратегии
   - Возможность запуска нескольких стратегий параллельно

#### Структура кода:

```python
# src/strategies/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Dict

@dataclass
class StrategyConfig:
    name: str
    enabled: bool
    max_position: float
    min_edge: float
    limits: Dict
    thresholds: Dict

class StrategyAgent(ABC):
    """Базовый класс для торговых стратегий"""
    
    def __init__(self, config: StrategyConfig, safety_pipeline: SafetyPipeline):
        self.config = config
        self.safety = safety_pipeline
        self.name = config.name
    
    @abstractmethod
    async def evaluate_signal(self, signal: Signal) -> Optional[Decision]:
        """Оценить сигнал и принять решение о сделке"""
        pass
    
    @abstractmethod
    async def manage_position(self, position: Position) -> Optional[Action]:
        """Управление существующей позицией"""
        pass
    
    @abstractmethod
    async def should_close_position(self, position: Position) -> bool:
        """Проверить нужно ли закрыть позицию"""
        pass
```

---

### Фаза 3: Advanced Features (Приоритет: P2)
**Срок:** 5-7 дней  
**Цель:** Добавить продвинутые возможности

#### Задачи:

1. **Sniper Mode (синтетические лимитные ордера)**
   - Мониторинг целевых цен
   - Автоматическое исполнение при достижении порога

2. **Auto-refire (докупка позиций)**
   - Отслеживание роста edge
   - Автоматическая докупка при +5% edge

3. **WebSocket для real-time мониторинга**
   - Подключение к Kalshi WebSocket API
   - Real-time обновления цен
   - Снижение задержки до ~20ms

---

## Метрики успеха

### Фаза 1:
- ✅ 0 сделок с устаревшими сигналами (>30s)
- ✅ 0 сделок с price drift >3%
- ✅ 0 дубликатов сделок
- ✅ Все сделки проходят через safety pipeline

### Фаза 2:
- ✅ SafeCompounder работает как StrategyAgent
- ✅ Возможность добавить новую стратегию без изменения существующего кода
- ✅ Изолированные конфигурации стратегий

### Фаза 3:
- ✅ Sniper Mode работает для целевых цен
- ✅ Auto-refire докупает позиции при росте edge
- ✅ WebSocket снижает задержку получения данных

---

## Риски и митигация

### Риск 1: Safety pipeline замедлит исполнение
**Митигация:** 
- Оптимизировать проверки (параллельное выполнение)
- Кэшировать цены с TTL 1s
- Измерить задержку и оптимизировать узкие места

### Риск 2: Слишком строгие проверки отклонят хорошие сделки
**Митигация:**
- Конфигурируемые пороги
- Логирование всех отклоненных сделок
- Анализ false positives
- Постепенная настройка параметров

### Риск 3: Рефакторинг сломает существующую логику
**Митигация:**
- Тесты для SafeCompounder перед рефакторингом
- Постепенная миграция
- Параллельный запуск старой и новой версии
- Rollback план

---

## Следующие шаги

1. ✅ Создать эту дорожную карту
2. ⏳ Начать Фазу 1: Создать SafetyPipeline
3. ⏳ Написать тесты для SafetyPipeline
4. ⏳ Интегрировать в SafeCompounder
5. ⏳ Деплой в tier-1 для тестирования
6. ⏳ Мониторинг метрик 24-48 часов
7. ⏳ Деплой в production
8. ⏳ Начать Фазу 2: Strategy Agents

---

**Статус:** Готов к началу работы  
**Приоритет:** P0 (критично для безопасности)  
**Владелец:** Claude + Gerwer  
**Дата начала:** 2026-04-12
