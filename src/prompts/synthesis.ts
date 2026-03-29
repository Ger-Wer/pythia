import { z } from 'zod';

export interface SynthesisResult {
  probability: number;
  confidence: 'low' | 'medium' | 'high';
  vs_market: number;
  reasoning: string;
  key_risk: string;
  base_rate: string;
}

export function buildSynthesisPrompt(params: {
  question: string;
  resolutionSource: string;
  endDate: string;
  daysRemaining: number;
  hoursSinceCreation: number;
  marketPrice: number;
  volume: number;
  researchBrief: string;
}): string {
  return `Ты — калиброванный суперпрогнозист. Оцени вероятность, что событие произойдёт.

ВОПРОС: ${params.question}
КРИТЕРИЙ РЕЗОЛВА: ${params.resolutionSource}
ДЕДЛАЙН: через ${params.daysRemaining} дней (${params.endDate})
ВОЗРАСТ РЫНКА: ${params.hoursSinceCreation} часов
ОБЪЁМ ТОРГОВ: $${params.volume.toLocaleString()}

ЦЕНА РЫНКА: ${params.marketPrice}%
Тысячи людей, рискуя деньгами, оценивают вероятность в ${params.marketPrice}%. Это сильный ориентир.
Чтобы отклониться — нужны конкретные аргументы.

ИССЛЕДОВАТЕЛЬСКАЯ СПРАВКА:
${params.researchBrief || 'Свежих данных нет. Опирайся на общие знания и базовые частоты.'}

ПОРЯДОК РАССУЖДЕНИЯ:
1. БАЗОВАЯ ЧАСТОТА
2. ДВА СИЛЬНЕЙШИХ АРГУМЕНТА ЗА
3. ДВА СИЛЬНЕЙШИХ АРГУМЕНТА ПРОТИВ
4. ЧТО МОЖЕТ УПУСКАТЬ ТОЛПА?
5. КАЛИБРОВКА: если оценка выше 85% или ниже 15%, пересмотри её и сдвинь на 5% к 50%.

ОТВЕТЬ СТРОГО JSON, БЕЗ ТЕКСТА ВНЕ JSON:
{
  "probability": число 0-100,
  "confidence": "низкая" | "средняя" | "высокая",
  "reasoning": "2-3 предложения на русском",
  "vs_market": число,
  "key_risk": "что может сделать оценку неверной",
  "base_rate": "историческая частота подобных событий"
}`;
}

function extractJson(raw: string): string | null {
  const fencedMatch = raw.match(/```(?:json)?\s*([\s\S]*?)```/i);
  if (fencedMatch?.[1]) {
    return fencedMatch[1].trim();
  }

  const firstBrace = raw.indexOf('{');
  const lastBrace = raw.lastIndexOf('}');
  if (firstBrace >= 0 && lastBrace > firstBrace) {
    return raw.slice(firstBrace, lastBrace + 1).trim();
  }

  return null;
}

const synthesisSchema = z.object({
  probability: z.coerce.number(),
  confidence: z.string().min(1),
  vs_market: z.coerce.number(),
  reasoning: z.string().min(1),
  key_risk: z.string().min(1),
  base_rate: z.string().min(1).default('Не указано.'),
});

function normalizeConfidence(value: string): 'low' | 'medium' | 'high' {
  const normalized = value.trim().toLowerCase();

  if (['low', 'низкая', 'низкий'].includes(normalized)) {
    return 'low';
  }

  if (['high', 'высокая', 'высокий'].includes(normalized)) {
    return 'high';
  }

  return 'medium';
}

export function parseSynthesisResponse(raw: string): SynthesisResult | null {
  const jsonText = extractJson(raw);

  if (!jsonText) {
    console.error('Failed to extract JSON from synthesis response.');
    return null;
  }

  try {
    const parsed = synthesisSchema.parse(JSON.parse(jsonText));
    return {
      probability: Math.max(0, Math.min(100, Math.round(parsed.probability))),
      confidence: normalizeConfidence(parsed.confidence),
      vs_market: Math.round(parsed.vs_market),
      reasoning: parsed.reasoning.trim(),
      key_risk: parsed.key_risk.trim(),
      base_rate: parsed.base_rate.trim(),
    };
  } catch (error) {
    console.error('Failed to parse synthesis response:', error);
    return null;
  }
}
