import OpenAI from 'openai';
import { z } from 'zod';
import { CONFIG } from '../config';
import type { PolymarketMarket } from '../tools/polymarket';
import {
  marketAgeHours,
  marketDaysRemaining,
  marketPriceFromYesOutcome,
} from '../tools/polymarket';

export interface DualPrediction {
  deepseek_p: number;
  deepseek_reasoning: string;
  mistral_p: number;
  mistral_reasoning: string;
  ensemble_p: number;
  disagreement: number;
  confidence: 'low' | 'medium' | 'high';
  cost_usd: number;
}

const predictionSchema = z.object({
  probability: z.coerce.number(),
  reasoning: z.string().min(1),
});

function extractJson(raw: string): string | null {
  const fenced = raw.match(/```(?:json)?\s*([\s\S]*?)```/i);
  if (fenced?.[1]) {
    return fenced[1].trim();
  }
  const firstBrace = raw.indexOf('{');
  const lastBrace = raw.lastIndexOf('}');
  if (firstBrace >= 0 && lastBrace > firstBrace) {
    return raw.slice(firstBrace, lastBrace + 1).trim();
  }
  return null;
}

function buildPredictionPrompt(market: PolymarketMarket, researchBrief: string): string {
  const marketPrice = Math.round(marketPriceFromYesOutcome(market));
  const daysRemaining = Math.max(1, Math.ceil(marketDaysRemaining(market)));
  const hoursSinceCreation = Math.max(1, Math.round(marketAgeHours(market)));

  return `You are a calibrated superforecaster. Estimate the probability that the event will occur.

QUESTION: ${market.question}
DEADLINE: in ${daysRemaining} days (${market.endDate})
MARKET AGE: ${hoursSinceCreation} hours
TRADING VOLUME: $${market.volume.toLocaleString()}

MARKET PRICE: ${marketPrice}%
Thousands of people, risking money, estimate the probability at ${marketPrice}%. This is a strong prior.
To deviate — you need concrete arguments.

RESEARCH BRIEF:
${researchBrief || 'No fresh data. Rely on general knowledge and base rates.'}

REASONING ORDER:
1. BASE RATE
2. TWO STRONGEST ARGUMENTS FOR
3. TWO STRONGEST ARGUMENTS AGAINST
4. WHAT IS THE MAIN CATALYST DRIVING PROBABILITY RIGHT NOW?
5. WHAT SIGNAL WOULD QUICKLY DISPROVE THIS ESTIMATE?
6. WHAT MIGHT THE CROWD BE MISSING?
7. CALIBRATION: if estimate is above 85% or below 15%, reconsider and shift 5% toward 50%.

ANSWER STRICTLY JSON, NO TEXT OUTSIDE JSON:
{
  "probability": number 0-100,
  "reasoning": "2-3 sentences in English"
}`;
}

async function callLLM7(
  market: PolymarketMarket,
  researchBrief: string,
): Promise<{ probability: number; reasoning: string; cost: number } | null> {
  if (!CONFIG.llm7.apiKey) {
    return null;
  }

  const client = new OpenAI({
    apiKey: CONFIG.llm7.apiKey,
    baseURL: CONFIG.llm7.baseUrl,
    timeout: 30_000,
    maxRetries: 2,
  });

  const prompt = buildPredictionPrompt(market, researchBrief);
  const completion = await client.chat.completions.create({
    model: CONFIG.llm7.forecastModel,
    temperature: 0.2,
    messages: [{ role: 'user', content: prompt }],
  });

  const raw = completion.choices[0]?.message?.content ?? '';
  const jsonText = extractJson(raw);
  if (!jsonText) {
    return null;
  }

  try {
    const parsed = predictionSchema.parse(JSON.parse(jsonText));
    // minimax-m2.7 pricing: $0.05 input, $0.13 output per 1M tokens
    const cost = (completion.usage?.total_tokens ?? 500) / 1000 * 0.00009;
    return {
      probability: Math.max(0, Math.min(100, Math.round(parsed.probability))),
      reasoning: parsed.reasoning.trim(),
      cost,
    };
  } catch {
    return null;
  }
}

async function callMistralFallback(
  market: PolymarketMarket,
  researchBrief: string,
): Promise<{ probability: number; reasoning: string; cost: number } | null> {
  if (!CONFIG.mistral.apiKey) {
    return null;
  }

  const client = new OpenAI({
    apiKey: CONFIG.mistral.apiKey,
    baseURL: CONFIG.mistral.baseUrl,
    timeout: 30_000,
    maxRetries: 2,
  });

  const prompt = buildPredictionPrompt(market, researchBrief);
  const completion = await client.chat.completions.create({
    model: CONFIG.mistral.smallModel,
    temperature: 0.2,
    messages: [{ role: 'user', content: prompt }],
  });

  const raw = completion.choices[0]?.message?.content ?? '';
  const jsonText = extractJson(raw);
  if (!jsonText) {
    return null;
  }

  try {
    const parsed = predictionSchema.parse(JSON.parse(jsonText));
    const cost = (completion.usage?.total_tokens ?? 500) / 1000 * 0.0004;
    return {
      probability: Math.max(0, Math.min(100, Math.round(parsed.probability))),
      reasoning: parsed.reasoning.trim(),
      cost,
    };
  } catch {
    return null;
  }
}

export async function predictDualLLM(
  market: PolymarketMarket,
  researchBrief: string,
): Promise<DualPrediction | null> {
  // Primary: LLM7 minimax-m2.7. Fallback: Mistral Small.
  let result = await callLLM7(market, researchBrief);
  let model = 'minimax-m2.7';

  if (!result) {
    result = await callMistralFallback(market, researchBrief);
    model = 'mistral-small';
  }

  if (!result) {
    return null;
  }

  return {
    deepseek_p: 0,
    deepseek_reasoning: '',
    mistral_p: result.probability,
    mistral_reasoning: `[${model}] ${result.reasoning}`,
    ensemble_p: result.probability,
    disagreement: 0,
    confidence: 'medium',
    cost_usd: result.cost,
  };
}
