/**
 * Safety Pipeline - многоступенчатая система проверок перед исполнением сделок
 *
 * Основано на лучших практиках из Claw Arbs:
 * - Kill switch для аварийного отключения
 * - Freshness checks (отклонение устаревших сигналов)
 * - Pre-execution price verification (проверка drift цены)
 * - Cooldowns между сделками
 * - Deduplication (защита от дубликатов)
 */

interface SafetyConfig {
  maxSignalAgeSeconds: number;
  priceDriftTolerance: number;
  cooldownSeconds: number;
  dedupWindowMinutes: number;
}

interface SafetyCheckResult {
  passed: boolean;
  reason: string;
  checkName: string;
  metadata?: Record<string, unknown>;
}

interface SignalForCheck {
  id: number;
  market_id: string;
  forecast_timestamp: string;
  market_price: number | null;
  best_probability: number | null;
  recommended_action: string;
}

interface RecentSignal {
  market_id: string;
  action: string;
  timestamp: Date;
}

export class SafetyPipeline {
  private killSwitch: boolean = false;
  private cooldowns: Map<string, Date> = new Map();
  private recentSignals: RecentSignal[] = [];

  constructor(private config: SafetyConfig) {}

  /**
   * Выполнить все проверки безопасности перед исполнением сделки
   */
  async preExecutionChecks(
    signal: SignalForCheck,
    getCurrentPrice: (marketId: string) => Promise<number | null>
  ): Promise<{ passed: boolean; reason: string }> {
    const checks: SafetyCheckResult[] = [
      this.checkKillSwitch(),
      this.checkFreshness(signal),
      await this.checkPriceDrift(signal, getCurrentPrice),
      this.checkCooldown(signal.market_id),
      this.checkDuplicate(signal),
    ];

    // Логировать все проверки
    for (const check of checks) {
      const status = check.passed ? '✓ PASSED' : '✗ FAILED';
      console.log(
        `[SafetyPipeline] ${check.checkName}: ${status} - ${check.reason}`,
        check.metadata ? JSON.stringify(check.metadata) : ''
      );

      if (!check.passed) {
        return {
          passed: false,
          reason: `${check.checkName}: ${check.reason}`,
        };
      }
    }

    return { passed: true, reason: 'All safety checks passed' };
  }

  /**
   * Проверка аварийного выключателя
   */
  private checkKillSwitch(): SafetyCheckResult {
    if (this.killSwitch) {
      return {
        passed: false,
        reason: 'Kill switch is activated',
        checkName: 'kill_switch',
      };
    }

    return {
      passed: true,
      reason: 'Kill switch is off',
      checkName: 'kill_switch',
    };
  }

  /**
   * Проверка свежести сигнала
   */
  private checkFreshness(signal: SignalForCheck): SafetyCheckResult {
    const signalTime = new Date(signal.forecast_timestamp);
    const now = new Date();
    const ageSeconds = (now.getTime() - signalTime.getTime()) / 1000;

    if (ageSeconds > this.config.maxSignalAgeSeconds) {
      return {
        passed: false,
        reason: `Signal too old: ${ageSeconds.toFixed(1)}s > ${this.config.maxSignalAgeSeconds}s`,
        checkName: 'freshness',
        metadata: { age_seconds: ageSeconds },
      };
    }

    return {
      passed: true,
      reason: `Signal fresh: ${ageSeconds.toFixed(1)}s`,
      checkName: 'freshness',
      metadata: { age_seconds: ageSeconds },
    };
  }

  /**
   * Проверка изменения цены перед ордером
   */
  private async checkPriceDrift(
    signal: SignalForCheck,
    getCurrentPrice: (marketId: string) => Promise<number | null>
  ): Promise<SafetyCheckResult> {
    try {
      const currentPrice = await getCurrentPrice(signal.market_id);

      if (currentPrice === null || signal.market_price === null) {
        return {
          passed: false,
          reason: 'Cannot verify price: current or signal price is null',
          checkName: 'price_drift',
        };
      }

      const expectedPrice = signal.market_price;
      const drift = Math.abs(currentPrice - expectedPrice) / expectedPrice;

      if (drift > this.config.priceDriftTolerance) {
        return {
          passed: false,
          reason: `Price drifted ${(drift * 100).toFixed(2)}% > ${(this.config.priceDriftTolerance * 100).toFixed(2)}%`,
          checkName: 'price_drift',
          metadata: {
            expected_price: expectedPrice,
            current_price: currentPrice,
            drift_percentage: drift,
          },
        };
      }

      return {
        passed: true,
        reason: `Price drift acceptable: ${(drift * 100).toFixed(2)}%`,
        checkName: 'price_drift',
        metadata: {
          expected_price: expectedPrice,
          current_price: currentPrice,
          drift_percentage: drift,
        },
      };
    } catch (error) {
      console.error('[SafetyPipeline] Error checking price drift:', error);
      return {
        passed: false,
        reason: `Failed to verify price: ${error instanceof Error ? error.message : String(error)}`,
        checkName: 'price_drift',
      };
    }
  }

  /**
   * Проверка cooldown между сделками на одном рынке
   */
  private checkCooldown(marketId: string): SafetyCheckResult {
    const lastTrade = this.cooldowns.get(marketId);

    if (lastTrade) {
      const now = new Date();
      const timeSinceMs = now.getTime() - lastTrade.getTime();
      const cooldownRemainingMs = this.config.cooldownSeconds * 1000 - timeSinceMs;

      if (cooldownRemainingMs > 0) {
        return {
          passed: false,
          reason: `Cooldown active: ${(cooldownRemainingMs / 1000).toFixed(1)}s remaining`,
          checkName: 'cooldown',
          metadata: { cooldown_remaining_seconds: cooldownRemainingMs / 1000 },
        };
      }
    }

    return {
      passed: true,
      reason: 'No cooldown active',
      checkName: 'cooldown',
    };
  }

  /**
   * Проверка дубликатов сигналов
   */
  private checkDuplicate(signal: SignalForCheck): SafetyCheckResult {
    // Очистить старые сигналы
    const cutoffTime = new Date();
    cutoffTime.setMinutes(cutoffTime.getMinutes() - this.config.dedupWindowMinutes);

    this.recentSignals = this.recentSignals.filter(
      (s) => s.timestamp > cutoffTime
    );

    // Проверить дубликат
    const signalTime = new Date(signal.forecast_timestamp);

    for (const recent of this.recentSignals) {
      if (
        recent.market_id === signal.market_id &&
        recent.action === signal.recommended_action &&
        Math.abs(recent.timestamp.getTime() - signalTime.getTime()) < 60000 // 60 секунд
      ) {
        return {
          passed: false,
          reason: 'Duplicate signal detected',
          checkName: 'deduplication',
          metadata: { duplicate_of: recent },
        };
      }
    }

    return {
      passed: true,
      reason: 'No duplicate found',
      checkName: 'deduplication',
    };
  }

  /**
   * Записать сделку для cooldown и deduplication
   */
  recordTrade(marketId: string, action: string): void {
    this.cooldowns.set(marketId, new Date());
    this.recentSignals.push({
      market_id: marketId,
      action,
      timestamp: new Date(),
    });
  }

  /**
   * Активировать аварийное отключение
   */
  activateKillSwitch(reason: string): void {
    this.killSwitch = true;
    console.error(`[SafetyPipeline] 🚨 KILL SWITCH ACTIVATED: ${reason}`);

    // Отправить уведомление в Telegram
    this.sendKillSwitchAlert(reason).catch((error) => {
      console.error('[SafetyPipeline] Failed to send kill switch alert:', error);
    });
  }

  /**
   * Отправить уведомление о kill switch в Telegram
   */
  private async sendKillSwitchAlert(reason: string): Promise<void> {
    try {
      const { sendTelegramAlert, formatKillSwitchAlert } = await import('./telegram_alerts');
      await sendTelegramAlert(formatKillSwitchAlert(reason));
    } catch (error) {
      // Игнорируем ошибки отправки уведомлений
    }
  }

  /**
   * Деактивировать аварийное отключение
   */
  deactivateKillSwitch(): void {
    this.killSwitch = false;
    console.log('[SafetyPipeline] Kill switch deactivated');
  }

  /**
   * Получить статус kill switch
   */
  isKillSwitchActive(): boolean {
    return this.killSwitch;
  }

  /**
   * Очистить все cooldowns (для тестирования)
   */
  clearCooldowns(): void {
    this.cooldowns.clear();
  }

  /**
   * Очистить историю сигналов (для тестирования)
   */
  clearSignalHistory(): void {
    this.recentSignals = [];
  }
}

/**
 * Создать SafetyPipeline с конфигурацией по умолчанию
 */
export function createSafetyPipeline(config?: Partial<SafetyConfig>): SafetyPipeline {
  const defaultConfig: SafetyConfig = {
    maxSignalAgeSeconds: 30,
    priceDriftTolerance: 0.03, // 3%
    cooldownSeconds: 60,
    dedupWindowMinutes: 5,
  };

  return new SafetyPipeline({ ...defaultConfig, ...config });
}
