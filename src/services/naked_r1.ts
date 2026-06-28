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
  // Forecast naked predict: Mistral only (free tier). DeepSeek V4 Pro reserved for oracle prophecy.
  if (!CONFIG.mistral.apiKey) {
    return null;
  }

  const client = new OpenAI({
    apiKey: CONFIG.mistral.apiKey,
    baseURL: CONFIG.mistral.baseUrl,
    timeout: 30_000,
    maxRetries: 2,
  });

  const marketPrice = market.outcomePrices[0] * 100;
  const daysRemaining = Math.max(
    1,
    Math.ceil((new Date(market.endDate).getTime() - Date.now()) / 86_400_000),
  );
  const prompt = `Оцени вероятность, что событие произойдёт.

ВОПРОС: ${market.question}
ДЕДЛАЙН: через ${daysRemaining} дней (${market.endDate})
ЦЕНА РЫНКА: ${marketPrice}% (коллективная оценка)

СВЕЖИЕ ДАННЫЕ:
${newsSnippets.join('\n') || 'Свежих новостей не найдено.'}

Рассуждения:
1. Базовая частота подобных событий
2. Один аргумент ЗА, один ПРОТИВ
3. Есть ли причины думать, что толпа ошибается?

Ответ JSON:
{
  "probability": число 0-100,
  "reasoning": "1-2 предложения на русском"
}`;

  const completion = await client.chat.completions.create({
    model: CONFIG.mistral.smallModel,
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
      CONFIG.mistral.smallModel,
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
