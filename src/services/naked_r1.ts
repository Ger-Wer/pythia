import OpenAI from 'openai';
import { z } from 'zod';
import { CONFIG } from '../config';
import type { PolymarketMarket } from '../tools/polymarket';
import { CostTracker } from './cost_tracker';

export interface NakedPrediction {
  probability: number;
  reasoning: string;
  cost_usd: number;
}

const responseSchema = z.object({
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

export async function nakedR1Predict(
  market: PolymarketMarket,
  newsSnippets: string[],
  costTracker: CostTracker,
): Promise<NakedPrediction | null> {
  const client = new OpenAI({
    apiKey: CONFIG.deepseek.apiKey,
    baseURL: CONFIG.deepseek.baseUrl,
    timeout: 30_000,
    maxRetries: 2,
  });

  const marketPrice = market.outcomePrices[0] * 100;
  const daysRemaining = Math.max(
    1,
    Math.ceil((new Date(market.endDate).getTime() - Date.now()) / 86_400_000),
  );
  const prompt = `You are a calibrated forecaster. Estimate probability this resolves YES.

MARKET: ${market.question}
DEADLINE: ${market.endDate} (${daysRemaining} days)
MARKET PRICE: ${marketPrice}% (crowd probability)
VOLUME: $${market.volume}

TOP NEWS:
${newsSnippets.join('\n') || 'No recent news found.'}

RULES:
- Output JSON only: {"probability": <0-100>, "reasoning": "<2 sentences>"}
- Probabilities above 90% or below 10% require explicit justification
- If uncertain, move 5% toward 50%`;

  const completion = await client.chat.completions.create({
    model: CONFIG.deepseek.modelSynthesis,
    temperature: 0.2,
    messages: [
      {
        role: 'user',
        content: prompt,
      },
    ],
  });

  const raw = completion.choices[0]?.message?.content ?? '';
  const jsonText = extractJson(raw);

  if (!jsonText) {
    console.error(`Failed to extract Naked R1 JSON for market ${market.id}`);
    return null;
  }

  try {
    const parsed = responseSchema.parse(JSON.parse(jsonText));
    const cost = costTracker.trackCall(
      CONFIG.deepseek.modelSynthesis,
      completion.usage?.prompt_tokens ?? 0,
      completion.usage?.completion_tokens ?? 0,
    );

    return {
      probability: Math.max(0, Math.min(100, Math.round(parsed.probability))),
      reasoning: parsed.reasoning.trim(),
      cost_usd: cost,
    };
  } catch (error) {
    console.error(`Failed to parse Naked R1 response for market ${market.id}:`, error);
    return null;
  }
}
