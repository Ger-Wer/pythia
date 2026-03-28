#!/bin/bash
# Pythia Day 0 — Market Availability Check
# Запуск: bash scripts/day0_check.sh

echo "=== Pythia Day 0: Market Availability Check ==="
echo "Fetching active markets from Polymarket Gamma API..."

RESPONSE=$(curl -s "https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=200")

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
    and 1 < days_to_resolution(m) <= 14
]

def vol(m):
    try:
        return float(m.get('volume', 0) or 0)
    except Exception:
        return 0.0

tier1 = [m for m in active_binary if 5000 <= vol(m) <= 50000]
tier2 = [m for m in active_binary if 5000 <= vol(m) <= 100000]
tier3 = [m for m in active_binary if vol(m) >= 5000]

def hours_old(m):
    try:
        created = datetime.fromisoformat(m.get('createdAt', '').replace('Z', '+00:00'))
        return (now - created).total_seconds() / 3600
    except Exception:
        return 9999.0

new_48h = [m for m in tier1 if hours_old(m) < 48]

print('')
print(f'Total active markets: {len(markets)}')
print(f'Binary only (2-14d window): {len(active_binary)}')
print('')
print('=== VOLUME TIERS ===')
print(f'$5K-$50K (primary):   {len(tier1)}')
print(f'$5K-$100K (expanded): {len(tier2)}')
print(f'$5K+ (all):           {len(tier3)}')
print('')
print(f'New (<48h, $5K-$50K): {len(new_48h)}')
print('')

if len(tier1) >= 10:
    print(f'PASS - {len(tier1)} markets in primary tier. Proceed to Week 1.')
elif len(tier2) >= 10:
    print(f'MARGINAL - expand volume filter to $100K. {len(tier2)} markets.')
else:
    print(f'FAIL - only {len(tier2)} markets. Consider waiting or different approach.')

print('')
print('=== SAMPLE MARKETS (primary tier) ===')
for m in sorted(tier1, key=lambda x: hours_old(x))[:10]:
    age = hours_old(m)
    prices = json.loads(m.get('outcomePrices', '[]'))
    price = float(prices[0]) * 100 if prices else 0
    marker = 'NEW' if age < 48 else '   '
    print(f'  {marker} {m["question"][:70]:70s}  vol=${vol(m):,.0f}  price={price:.0f}%  age={age:.0f}h')
PY

rm -f "$TMP_JSON"

echo ""
echo "=== Check complete ==="
