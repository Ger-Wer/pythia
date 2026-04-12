/**
 * Telegram notification helper for SafetyPipeline
 */

import { CONFIG } from '../config';

export async function sendTelegramAlert(message: string): Promise<void> {
  if (!CONFIG.telegram.botToken || !CONFIG.telegram.chatId) {
    return;
  }

  const url = `https://api.telegram.org/bot${CONFIG.telegram.botToken}/sendMessage`;
  await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      chat_id: CONFIG.telegram.chatId,
      text: message,
      parse_mode: 'Markdown',
    }),
  }).catch((error) => console.error('Telegram alert failed:', error));
}

export function formatSafetyRejectionAlert(
  marketId: string,
  reason: string,
  signal: {
    market_price: number | null;
    best_probability: number | null;
    recommended_action: string;
  }
): string {
  const edge = signal.best_probability && signal.market_price
    ? (signal.best_probability - signal.market_price).toFixed(1)
    : 'N/A';

  return [
    '*Pythia: сделка отклонена SafetyPipeline*',
    '',
    `Market: \`${marketId}\``,
    `Action: ${signal.recommended_action}`,
    `Edge: ${edge}%`,
    `Price: ${signal.market_price?.toFixed(1) ?? 'N/A'}%`,
    '',
    `⚠️ Причина: ${reason}`,
  ].join('\n');
}

export function formatKillSwitchAlert(reason: string): string {
  return [
    '*🚨 Pythia: KILL SWITCH ACTIVATED*',
    '',
    `Причина: ${reason}`,
    '',
    'Все сделки остановлены до деактивации.',
  ].join('\n');
}
