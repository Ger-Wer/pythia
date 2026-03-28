import { z } from 'zod';

export interface SynthesisResult {
  probability: number;
  confidence: 'low' | 'medium' | 'high';
  vs_market: number;
  reasoning: string;
  key_risk: string;
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
  return `You are a calibrated superforecaster. Estimate the probability
this prediction market resolves YES.

MARKET: ${params.question}
RESOLUTION CRITERIA: ${params.resolutionSource}
DEADLINE: ${params.endDate} (${params.daysRemaining} days from now)
MARKET AGE: ${params.hoursSinceCreation} hours old
CURRENT MARKET PRICE: ${params.marketPrice}% (crowd implied probability)
LIQUIDITY: $${params.volume} total volume

RESEARCH BRIEF:
${params.researchBrief || 'NO RESEARCH — market too new, use base rates and reasoning only.'}

REASONING PROTOCOL:
1. State the relevant base rate for this type of event
2. List 2 strongest arguments FOR (YES)
3. List 2 strongest arguments AGAINST (YES)
4. If market < 24h old: weight base rates more heavily than current news
5. If your estimate differs from market price by >15%: explain why you believe the crowd is wrong

CALIBRATION RULES:
- Probabilities above 90% or below 10% require EXPLICIT justification
- If uncertain, move 5% toward 50%
- Market price is a strong prior — justify deviations explicitly

OUTPUT (JSON only, no text outside the JSON block):
{
  "probability": <integer 0-100>,
  "confidence": "<low|medium|high>",
  "vs_market": <integer, your_prob minus market_price>,
  "reasoning": "<2-3 sentences max>",
  "key_risk": "<what would make this estimate wrong>"
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
  confidence: z.enum(['low', 'medium', 'high']),
  vs_market: z.coerce.number(),
  reasoning: z.string().min(1),
  key_risk: z.string().min(1),
});

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
      confidence: parsed.confidence,
      vs_market: Math.round(parsed.vs_market),
      reasoning: parsed.reasoning.trim(),
      key_risk: parsed.key_risk.trim(),
    };
  } catch (error) {
    console.error('Failed to parse synthesis response:', error);
    return null;
  }
}
