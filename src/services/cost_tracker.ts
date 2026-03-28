import { CONFIG } from '../config';
import type { Database } from './db';
import { getTodayCost } from './db';

const MODEL_PRICING = {
  research: {
    inputPerMillion: 0.27,
    outputPerMillion: 1.1,
  },
  synthesis: {
    inputPerMillion: 0.55,
    outputPerMillion: 2.19,
  },
} as const;

function pricingForModel(model: string) {
  const normalized = model.toLowerCase();

  if (normalized.includes('reasoner') || normalized.includes('r1')) {
    return MODEL_PRICING.synthesis;
  }

  return MODEL_PRICING.research;
}

export class CostLimitError extends Error {
  constructor(public spent: number, public limit: number) {
    super(`Daily cost limit exceeded: $${spent.toFixed(4)} >= $${limit}`);
  }
}

export class CostTracker {
  private sessionCost = 0;

  constructor(
    private readonly db: Database,
    private readonly dailyLimit: number = CONFIG.limits.dailyCostUsd,
  ) {}

  trackCall(model: string, inputTokens: number, outputTokens: number): number {
    const pricing = pricingForModel(model);
    const cost =
      (inputTokens / 1_000_000) * pricing.inputPerMillion +
      (outputTokens / 1_000_000) * pricing.outputPerMillion;

    this.sessionCost += cost;

    const totalToday = getTodayCost(this.db) + this.sessionCost;
    if (this.sessionCost > this.dailyLimit || totalToday > this.dailyLimit) {
      throw new CostLimitError(totalToday, this.dailyLimit);
    }

    return cost;
  }

  getSessionCost(): number {
    return this.sessionCost;
  }
}
