import { afterAll, beforeEach, expect, test } from 'bun:test';
import { mkdtempSync, mkdirSync, rmSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const tempDir = mkdtempSync(path.join(os.tmpdir(), 'pythia-cost-test-'));
const dbPath = path.join(tempDir, 'pythia.db');
const logDir = path.join(tempDir, 'logs');

process.env.DEEPSEEK_API_KEY = 'test-deepseek-key';
process.env.DB_PATH = dbPath;
process.env.LOG_DIR = logDir;
process.env.DEEPSEEK_BASE_URL = 'https://api.groq.com/openai/v1';

const dbModulePromise = import('./db');
const costModulePromise = import('./cost_tracker');

beforeEach(() => {
  rmSync(dbPath, { force: true });
  rmSync(`${dbPath}-shm`, { force: true });
  rmSync(`${dbPath}-wal`, { force: true });
  mkdirSync(logDir, { recursive: true });
});

afterAll(() => {
  rmSync(tempDir, { recursive: true, force: true });
});

test('Groq-hosted models are tracked as zero-cost calls', async () => {
  const { initDb } = await dbModulePromise;
  const { CostTracker } = await costModulePromise;

  const db = initDb();
  try {
    const tracker = new CostTracker(db, 10);
    const groqCost = tracker.trackCall('llama-3.1-8b-instant', 12_000, 2_000);
    const gptOssCost = tracker.trackCall('openai/gpt-oss-120b', 12_000, 2_000);

    expect(groqCost).toBe(0);
    expect(gptOssCost).toBe(0);
    expect(tracker.getSessionCost()).toBe(0);
  } finally {
    db.close();
  }
});

test('DeepSeek pricing remains non-zero when provider is not Groq', async () => {
  process.env.DEEPSEEK_BASE_URL = 'https://api.deepseek.com/v1';

  const { initDb } = await dbModulePromise;
  const { CostTracker } = await costModulePromise;
  const db = initDb();

  try {
    const tracker = new CostTracker(db, 10);
    const cost = tracker.trackCall('deepseek-chat', 1_000_000, 1_000_000);
    expect(cost).toBeCloseTo(1.37, 6);
    expect(tracker.getSessionCost()).toBeCloseTo(1.37, 6);
  } finally {
    db.close();
    process.env.DEEPSEEK_BASE_URL = 'https://api.groq.com/openai/v1';
  }
});
