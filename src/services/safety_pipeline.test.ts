/**
 * Tests for SafetyPipeline
 */

import { describe, test, expect, beforeEach } from 'bun:test';
import { SafetyPipeline, createSafetyPipeline } from './safety_pipeline';

describe('SafetyPipeline', () => {
  let pipeline: SafetyPipeline;

  beforeEach(() => {
    pipeline = createSafetyPipeline({
      maxSignalAgeSeconds: 30,
      priceDriftTolerance: 0.03,
      cooldownSeconds: 60,
      dedupWindowMinutes: 5,
    });
  });

  describe('Kill Switch', () => {
    test('should pass when kill switch is off', async () => {
      const signal = createTestSignal();
      const result = await pipeline.preExecutionChecks(signal, mockGetCurrentPrice(50));

      expect(result.passed).toBe(true);
    });

    test('should fail when kill switch is activated', async () => {
      pipeline.activateKillSwitch('Test emergency');

      const signal = createTestSignal();
      const result = await pipeline.preExecutionChecks(signal, mockGetCurrentPrice(50));

      expect(result.passed).toBe(false);
      expect(result.reason).toContain('kill_switch');
    });

    test('should pass after kill switch is deactivated', async () => {
      pipeline.activateKillSwitch('Test emergency');
      pipeline.deactivateKillSwitch();

      const signal = createTestSignal();
      const result = await pipeline.preExecutionChecks(signal, mockGetCurrentPrice(50));

      expect(result.passed).toBe(true);
    });
  });

  describe('Freshness Check', () => {
    test('should pass for fresh signal', async () => {
      const signal = createTestSignal({
        forecast_timestamp: new Date().toISOString(),
      });

      const result = await pipeline.preExecutionChecks(signal, mockGetCurrentPrice(50));

      expect(result.passed).toBe(true);
    });

    test('should fail for old signal', async () => {
      const oldTime = new Date();
      oldTime.setSeconds(oldTime.getSeconds() - 60); // 60 seconds ago

      const signal = createTestSignal({
        forecast_timestamp: oldTime.toISOString(),
      });

      const result = await pipeline.preExecutionChecks(signal, mockGetCurrentPrice(50));

      expect(result.passed).toBe(false);
      expect(result.reason).toContain('freshness');
      expect(result.reason).toContain('too old');
    });

    test('should pass for signal at age threshold', async () => {
      const thresholdTime = new Date();
      thresholdTime.setSeconds(thresholdTime.getSeconds() - 29); // 29 seconds ago

      const signal = createTestSignal({
        forecast_timestamp: thresholdTime.toISOString(),
      });

      const result = await pipeline.preExecutionChecks(signal, mockGetCurrentPrice(50));

      expect(result.passed).toBe(true);
    });
  });

  describe('Price Drift Check', () => {
    test('should pass when price drift is within tolerance', async () => {
      const signal = createTestSignal({ market_price: 50 });
      const result = await pipeline.preExecutionChecks(signal, mockGetCurrentPrice(51)); // 2% drift

      expect(result.passed).toBe(true);
    });

    test('should fail when price drift exceeds tolerance', async () => {
      const signal = createTestSignal({ market_price: 50 });
      const result = await pipeline.preExecutionChecks(signal, mockGetCurrentPrice(52)); // 4% drift

      expect(result.passed).toBe(false);
      expect(result.reason).toContain('price_drift');
      expect(result.reason).toContain('drifted');
    });

    test('should fail when current price is null', async () => {
      const signal = createTestSignal({ market_price: 50 });
      const result = await pipeline.preExecutionChecks(signal, mockGetCurrentPrice(null));

      expect(result.passed).toBe(false);
      expect(result.reason).toContain('price_drift');
    });

    test('should fail when signal price is null', async () => {
      const signal = createTestSignal({ market_price: null });
      const result = await pipeline.preExecutionChecks(signal, mockGetCurrentPrice(50));

      expect(result.passed).toBe(false);
      expect(result.reason).toContain('price_drift');
    });

    test('should handle price fetch errors', async () => {
      const signal = createTestSignal({ market_price: 50 });
      const errorFn = async () => {
        throw new Error('API error');
      };

      const result = await pipeline.preExecutionChecks(signal, errorFn);

      expect(result.passed).toBe(false);
      expect(result.reason).toContain('Failed to verify price');
    });
  });

  describe('Cooldown Check', () => {
    test('should pass when no cooldown is active', async () => {
      const signal = createTestSignal();
      const result = await pipeline.preExecutionChecks(signal, mockGetCurrentPrice(50));

      expect(result.passed).toBe(true);
    });

    test('should fail when cooldown is active', async () => {
      const signal = createTestSignal({ market_id: 'test-market-1' });

      // Record a trade to start cooldown
      pipeline.recordTrade('test-market-1', 'enter_yes');

      // Try to trade again immediately
      const result = await pipeline.preExecutionChecks(signal, mockGetCurrentPrice(50));

      expect(result.passed).toBe(false);
      expect(result.reason).toContain('cooldown');
      expect(result.reason).toContain('active');
    });

    test('should pass after cooldown expires', async () => {
      // Create pipeline with 0 second cooldown for testing
      const fastPipeline = createSafetyPipeline({
        maxSignalAgeSeconds: 30,
        priceDriftTolerance: 0.03,
        cooldownSeconds: 0,
        dedupWindowMinutes: 5,
      });

      const signal = createTestSignal({ market_id: 'test-market-1' });

      fastPipeline.recordTrade('test-market-1', 'enter_yes');

      // Should pass immediately with 0 cooldown
      const result = await fastPipeline.preExecutionChecks(signal, mockGetCurrentPrice(50));

      expect(result.passed).toBe(true);
    });

    test('should allow trades on different markets', async () => {
      const signal1 = createTestSignal({ market_id: 'test-market-1' });
      const signal2 = createTestSignal({ market_id: 'test-market-2' });

      pipeline.recordTrade('test-market-1', 'enter_yes');

      // Should pass for different market
      const result = await pipeline.preExecutionChecks(signal2, mockGetCurrentPrice(50));

      expect(result.passed).toBe(true);
    });
  });

  describe('Deduplication Check', () => {
    test('should pass for first signal', async () => {
      const signal = createTestSignal();
      const result = await pipeline.preExecutionChecks(signal, mockGetCurrentPrice(50));

      expect(result.passed).toBe(true);
    });

    test('should fail for duplicate signal', async () => {
      const signal = createTestSignal({
        market_id: 'test-market-1',
        recommended_action: 'enter_yes',
      });

      // First trade
      await pipeline.preExecutionChecks(signal, mockGetCurrentPrice(50));
      pipeline.recordTrade('test-market-1', 'enter_yes');

      // Duplicate trade
      const result = await pipeline.preExecutionChecks(signal, mockGetCurrentPrice(50));

      expect(result.passed).toBe(false);
      expect(result.reason).toContain('deduplication');
      expect(result.reason).toContain('Duplicate');
    });

    test('should allow same market with different action', async () => {
      const signal1 = createTestSignal({
        market_id: 'test-market-1',
        recommended_action: 'enter_yes',
      });
      const signal2 = createTestSignal({
        market_id: 'test-market-1',
        recommended_action: 'enter_no',
      });

      await pipeline.preExecutionChecks(signal1, mockGetCurrentPrice(50));
      pipeline.recordTrade('test-market-1', 'enter_yes');

      const result = await pipeline.preExecutionChecks(signal2, mockGetCurrentPrice(50));

      expect(result.passed).toBe(true);
    });

    test('should clear old signals from dedup window', async () => {
      // Create pipeline with 0 minute dedup window for testing
      const fastPipeline = createSafetyPipeline({
        maxSignalAgeSeconds: 30,
        priceDriftTolerance: 0.03,
        cooldownSeconds: 0,
        dedupWindowMinutes: 0,
      });

      const signal = createTestSignal({
        market_id: 'test-market-1',
        recommended_action: 'enter_yes',
      });

      await fastPipeline.preExecutionChecks(signal, mockGetCurrentPrice(50));
      fastPipeline.recordTrade('test-market-1', 'enter_yes');

      // Should pass with 0 dedup window
      const result = await fastPipeline.preExecutionChecks(signal, mockGetCurrentPrice(50));

      expect(result.passed).toBe(true);
    });
  });

  describe('Full Pipeline Integration', () => {
    test('should pass all checks for valid signal', async () => {
      const signal = createTestSignal({
        forecast_timestamp: new Date().toISOString(),
        market_price: 50,
        market_id: 'test-market-1',
        recommended_action: 'enter_yes',
      });

      const result = await pipeline.preExecutionChecks(signal, mockGetCurrentPrice(50.5));

      expect(result.passed).toBe(true);
      expect(result.reason).toContain('All safety checks passed');
    });

    test('should fail on first failed check', async () => {
      pipeline.activateKillSwitch('Test');

      const signal = createTestSignal();
      const result = await pipeline.preExecutionChecks(signal, mockGetCurrentPrice(50));

      expect(result.passed).toBe(false);
      expect(result.reason).toContain('kill_switch');
    });

    test('should record trade after successful checks', async () => {
      const signal = createTestSignal({
        market_id: 'test-market-1',
        recommended_action: 'enter_yes',
      });

      const result1 = await pipeline.preExecutionChecks(signal, mockGetCurrentPrice(50));
      expect(result1.passed).toBe(true);

      pipeline.recordTrade('test-market-1', 'enter_yes');

      // Should fail due to cooldown
      const result2 = await pipeline.preExecutionChecks(signal, mockGetCurrentPrice(50));
      expect(result2.passed).toBe(false);
    });
  });

  describe('Utility Methods', () => {
    test('should check kill switch status', () => {
      expect(pipeline.isKillSwitchActive()).toBe(false);

      pipeline.activateKillSwitch('Test');
      expect(pipeline.isKillSwitchActive()).toBe(true);

      pipeline.deactivateKillSwitch();
      expect(pipeline.isKillSwitchActive()).toBe(false);
    });

    test('should clear cooldowns', async () => {
      const signal = createTestSignal({ market_id: 'test-market-1' });

      pipeline.recordTrade('test-market-1', 'enter_yes');

      let result = await pipeline.preExecutionChecks(signal, mockGetCurrentPrice(50));
      expect(result.passed).toBe(false);

      pipeline.clearCooldowns();

      result = await pipeline.preExecutionChecks(signal, mockGetCurrentPrice(50));
      expect(result.passed).toBe(true);
    });

    test('should clear signal history', async () => {
      const signal = createTestSignal({
        market_id: 'test-market-1',
        recommended_action: 'enter_yes',
      });

      await pipeline.preExecutionChecks(signal, mockGetCurrentPrice(50));
      pipeline.recordTrade('test-market-1', 'enter_yes');

      pipeline.clearSignalHistory();
      pipeline.clearCooldowns();

      const result = await pipeline.preExecutionChecks(signal, mockGetCurrentPrice(50));
      expect(result.passed).toBe(true);
    });
  });
});

// Helper functions

function createTestSignal(overrides = {}) {
  return {
    id: 1,
    market_id: 'test-market',
    forecast_timestamp: new Date().toISOString(),
    market_price: 50,
    best_probability: 60,
    recommended_action: 'enter_yes',
    ...overrides,
  };
}

function mockGetCurrentPrice(price: number | null) {
  return async () => price;
}
