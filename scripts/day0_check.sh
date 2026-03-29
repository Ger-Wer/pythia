#!/bin/bash
# Pythia Day 0 — Market Availability Check
# Запуск: bash scripts/day0_check.sh

echo "=== Pythia Day 0: Market Availability Check ==="
echo "Fetching active markets from Polymarket Gamma API..."

RESPONSE=$(curl -s "https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=500")

if [ -z "$RESPONSE" ]; then
    echo "ERROR: Gamma API returned empty response"
    exit 1
fi

TMP_JSON=$(mktemp)
printf '%s' "$RESPONSE" > "$TMP_JSON"

python3 - "$TMP_JSON" <<'PY'
import json
import sys
from datetime import datetime, timezone

with open(sys.argv[1], 'r', encoding='utf-8') as handle:
    markets = json.load(handle)
now = datetime.now(timezone.utc)

binary = [m for m in markets if len(json.loads(m.get('outcomes', '[]'))) == 2]

def days_to_resolution(m):
    try:
        end = datetime.fromisoformat(m.get('endDate', '').replace('Z', '+00:00'))
        return (end - now).total_seconds() / 86400
    except Exception:
        return -9999.0

active_binary = [
    m for m in binary
    if m.get('active')
    and not m.get('closed')
]

primary_window = [m for m in active_binary if 1 < days_to_resolution(m) <= 21]
expanded_window = [m for m in active_binary if 1 < days_to_resolution(m) <= 30]

def vol(m):
    try:
        return float(m.get('volume', 0) or 0)
    except Exception:
        return 0.0

tier1 = [m for m in primary_window if 1000 <= vol(m) <= 100000]
tier2 = [m for m in expanded_window if 500 <= vol(m) <= 250000]
tier3 = [m for m in expanded_window if vol(m) >= 500]

def hours_old(m):
    try:
        created = datetime.fromisoformat(m.get('createdAt', '').replace('Z', '+00:00'))
        return (now - created).total_seconds() / 3600
    except Exception:
        return 9999.0

new_72h = [m for m in tier1 if hours_old(m) < 72]

print('')
print(f'Total active markets: {len(markets)}')
print(f'Binary only (1-21d primary window): {len(primary_window)}')
print(f'Binary only (1-30d fallback window): {len(expanded_window)}')
print('')
print('=== VOLUME TIERS ===')
print(f'$1K-$100K (primary):   {len(tier1)}')
print(f'$500-$250K (fallback): {len(tier2)}')
print(f'$500+ (all fallback):  {len(tier3)}')
print('')
print(f'New (<72h, $1K-$100K): {len(new_72h)}')
print('')

if len(tier1) >= 10:
    print(f'PASS - {len(tier1)} markets in primary tier. Proceed to Week 1.')
elif len(tier2) >= 10:
    print(f'MARGINAL - fallback tier is viable with {len(tier2)} markets.')
else:
    print(f'FAIL - only {len(tier2)} markets. Consider waiting or different approach.')

print('')
print('=== SAMPLE MARKETS (primary tier) ===')
for m in sorted(tier1, key=lambda x: hours_old(x))[:10]:
    age = hours_old(m)
    prices = json.loads(m.get('outcomePrices', '[]'))
    price = float(prices[0]) * 100 if prices else 0
    marker = 'NEW' if age < 72 else '   '
    print(f'  {marker} {m["question"][:70]:70s}  vol=${vol(m):,.0f}  price={price:.0f}%  age={age:.0f}h')
PY

rm -f "$TMP_JSON"

echo ""
echo "=== Check complete ==="
