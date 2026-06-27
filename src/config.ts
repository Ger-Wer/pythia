function env(key: string, fallback: string): string {
  return process.env[key] || fallback;
}

function requireEnv(key: string): string {
  const val = process.env[key];
  if (!val) {
    throw new Error(`Missing required env var: ${key}`);
  }
  return val;
}

function requireOneOf(keys: string[]): string {
  for (const key of keys) {
    const value = process.env[key];
    if (value) {
      return value;
    }
  }

  throw new Error(`Missing required env var: one of ${keys.join(', ')}`);
}

function numberEnv(key: string, fallback: string): number {
  const raw = env(key, fallback);
  const parsed = Number(raw);

  if (!Number.isFinite(parsed)) {
    throw new Error(`Invalid numeric env var: ${key}=${raw}`);
  }

  return parsed;
}

const isUsingOpenRouter = !process.env.DEEPSEEK_API_KEY && Boolean(process.env.OPENROUTER_API_KEY);

export const CONFIG = {
  deepseek: {
    apiKey: requireOneOf(['DEEPSEEK_API_KEY', 'OPENROUTER_API_KEY']),
    baseUrl: isUsingOpenRouter
      ? env('OPENROUTER_BASE_URL', 'https://openrouter.ai/api/v1')
      : env('DEEPSEEK_BASE_URL', 'https://api.deepseek.com/v1'),
    modelResearch: env('MODEL_RESEARCH', 'deepseek-chat'),
    modelSynthesis: env('MODEL_SYNTHESIS', 'deepseek-reasoner'),
  },
  search: {
    braveApiKey: env('BRAVE_SEARCH_API_KEY', ''),
    searxngBaseUrl: env('SEARXNG_BASE_URL', ''),
  },
  telegram: {
    botToken: env('TELEGRAM_BOT_TOKEN', ''),
    chatId: env('TELEGRAM_CHAT_ID', ''),
  },
  limits: {
    dailyCostUsd: numberEnv('DAILY_COST_LIMIT_USD', '2.00'),
    monthlyCostUsd: numberEnv('MONTHLY_COST_LIMIT_USD', '50.00'),
    marketsPerRun: Math.trunc(numberEnv('MARKETS_PER_RUN', '20')),
  },
  paths: {
    db: env('DB_PATH', './data/pythia.db'),
    logDir: env('LOG_DIR', './logs'),
  },
  safety: {
    maxSignalAgeSeconds: numberEnv('SAFETY_MAX_SIGNAL_AGE_SECONDS', '30'),
    priceDriftTolerance: numberEnv('SAFETY_PRICE_DRIFT_TOLERANCE', '0.03'),
    cooldownSeconds: numberEnv('SAFETY_COOLDOWN_SECONDS', '60'),
    dedupWindowMinutes: numberEnv('SAFETY_DEDUP_WINDOW_MINUTES', '5'),
  },
} as const;

export type AppConfig = typeof CONFIG;

// Force early validation of the primary API key at module load.
requireEnv(isUsingOpenRouter ? 'OPENROUTER_API_KEY' : 'DEEPSEEK_API_KEY');
