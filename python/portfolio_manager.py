#!/usr/bin/env python3
"""
Pythia Capital — Virtual Portfolio Manager
Tracks virtual positions with Kelly stake sizing, computes ROI, updates pinned message.
"""

import argparse
import math
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Any, Optional, List

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "data" / "pythia.db"))
LOG_DIR = os.getenv("LOG_DIR", str(BASE_DIR / "logs"))

ORACLE_BOT_TOKEN = os.getenv("ORACLE_TELEGRAM_BOT_TOKEN", "")
ORACLE_CHANNEL_ID = os.getenv("ORACLE_TELEGRAM_CHANNEL_ID", "")

STARTING_AUM = float(os.getenv("PORTFOLIO_STARTING_AUM", "1000"))
KELLY_FRACTION = float(os.getenv("PORTFOLIO_KELLY_FRACTION", "0.25"))
MAX_POSITION_PCT = float(os.getenv("PORTFOLIO_MAX_POSITION_PCT", "0.05"))

GAMMA_API = "https://gamma-api.polymarket.com"


def log_message(message: str, level: str = "INFO"):
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] [{level}] {message}"
    print(log_entry)
    log_file = Path(LOG_DIR) / "portfolio_manager.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with open(log_file, "a") as f:
        f.write(log_entry + "\n")


def kelly_fraction(entry_price: float, pythia_prob: float, direction: str) -> float:
    """
    Kelly criterion for prediction markets.
    YES at price c: f = (p - c) / (1 - c)
    NO at price c (buying NO at 1-c): f = (c - p) / c
    Returns raw Kelly fraction (before scaling).
    """
    p = pythia_prob
    if direction == 'YES':
        c = entry_price
        if c >= 1:
            return 0.0
        return (p - c) / (1 - c)
    else:
        c = 1 - entry_price
        if c >= 1:
            return 0.0
        return (c - (1 - p)) / c


class PortfolioManager:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path

    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def get_state(self) -> Dict[str, Any]:
        conn = self._conn()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM portfolio_state WHERE id = 1")
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else {
            'starting_aum': STARTING_AUM,
            'current_aum': STARTING_AUM,
            'cash': STARTING_AUM,
            'positions_value': 0.0,
            'total_realized_pnl': 0.0,
            'total_trades': 0,
            'winning_trades': 0,
        }

    def open_position(self, prophecy_id: int, entry_price: float, direction: str,
                      pythia_prob: float) -> Optional[int]:
        """Open a virtual position with Kelly stake sizing."""
        raw_kelly = kelly_fraction(entry_price, pythia_prob, direction)
        if raw_kelly <= 0:
            log_message(f"Kelly <= 0 for prophecy {prophecy_id}, skipping position")
            return None

        stake_fraction = KELLY_FRACTION * raw_kelly
        state = self.get_state()
        stake_usd = stake_fraction * state['current_aum']
        stake_usd = min(stake_usd, MAX_POSITION_PCT * state['current_aum'])

        if stake_usd < 0.50:
            log_message(f"Stake too small (${stake_usd:.2f}), skipping")
            return None

        now = datetime.now(timezone.utc).isoformat() + 'Z'
        conn = self._conn()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO portfolio_positions
                (prophecy_id, entry_price, stake_fraction, stake_usd, direction, status, opened_at)
            VALUES (?, ?, ?, ?, ?, 'open', ?)
        ''', (prophecy_id, entry_price, stake_fraction, stake_usd, direction, now))
        position_id = cursor.lastrowid

        cursor.execute('''
            UPDATE portfolio_state
            SET cash = cash - ?,
                current_aum = current_aum,
                updated_at = datetime('now')
            WHERE id = 1
        ''', (stake_usd,))
        conn.commit()
        conn.close()

        log_message(f"Opened position {position_id}: {direction} ${stake_usd:.2f} @ {entry_price:.2f}")
        return position_id

    def close_position(self, position_id: int, exit_price: float, outcome: int):
        """Close position on market resolution. outcome: 1=YES won, 0=NO won."""
        conn = self._conn()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM portfolio_positions WHERE id = ?", (position_id,))
        pos = cursor.fetchone()
        if not pos:
            conn.close()
            return

        pos = dict(pos)
        if pos['status'] != 'open':
            conn.close()
            return

        direction = pos['direction']
        entry = pos['entry_price']
        stake = pos['stake_usd']

        # Calculate realized PnL
        if direction == 'YES':
            # Bought YES shares at 'entry', each pays $1 if YES wins
            shares = stake / entry if entry > 0 else 0
            if outcome == 1:
                realized = shares - stake  # won
            else:
                realized = -stake  # lost all
        else:
            # Bought NO shares at (1-entry), each pays $1 if NO wins
            no_price = 1 - entry
            shares = stake / no_price if no_price > 0 else 0
            if outcome == 0:
                realized = shares - stake  # won
            else:
                realized = -stake  # lost all

        now = datetime.now(timezone.utc).isoformat() + 'Z'
        won = 1 if realized > 0 else 0
        cursor.execute('''
            UPDATE portfolio_positions
            SET status = 'closed', exit_price = ?, realized_pnl = ?, closed_at = ?
            WHERE id = ?
        ''', (exit_price, realized, now, position_id))

        cursor.execute('''
            UPDATE portfolio_state
            SET cash = cash + ? + ?,
                total_realized_pnl = total_realized_pnl + ?,
                total_trades = total_trades + 1,
                winning_trades = winning_trades + ?,
                current_aum = cash + ? + ? + positions_value,
                updated_at = datetime('now')
            WHERE id = 1
        ''', (stake, realized, realized, won, stake, realized, stake))

        conn.commit()
        conn.close()
        log_message(f"Closed position {position_id}: PnL ${realized:+.2f} ({'WIN' if won else 'LOSS'})")

    def mark_to_market(self):
        """Update current prices for all open positions via Polymarket API."""
        conn = self._conn()
        cursor = conn.cursor()
        cursor.execute("SELECT id, prophecy_id FROM portfolio_positions WHERE status = 'open'")
        positions = cursor.fetchall()
        conn.close()

        if not positions:
            return

        log_message(f"Marking {len(positions)} open positions to market")
        # Get current prices for all open position markets
        prophecies = []
        for pos in positions:
            cursor2 = self._conn().cursor()
            cursor2.execute("SELECT market_id FROM oracle_prophecies WHERE id = ?", (pos['prophecy_id'],))
            row = cursor2.fetchone()
            if row:
                prophecies.append({'position_id': pos['id'], 'market_id': row[0]})

        # Fetch current prices (could batch, but keep simple)
        updated = 0
        for p in prophecies:
            current_price = self._fetch_current_price(p['market_id'])
            if current_price is not None:
                # Update positions_value in portfolio_state
                conn = self._conn()
                cursor = conn.cursor()
                cursor.execute("SELECT entry_price, stake_usd, direction FROM portfolio_positions WHERE id = ?",
                               (p['position_id'],))
                pos = cursor.fetchone()
                if pos:
                    entry, stake, direction = pos['entry'], pos['stake_usd'], pos['direction']
                    if direction == 'YES':
                        shares = stake / entry if entry > 0 else 0
                        unrealized = shares * current_price - stake
                    else:
                        no_price = 1 - entry
                        shares = stake / no_price if no_price > 0 else 0
                        unrealized = shares * (1 - current_price) - stake
                    updated += 1
                conn.close()

        # Recalculate current_aum = cash + sum(unrealized)
        conn = self._conn()
        cursor = conn.cursor()
        cursor.execute("SELECT cash FROM portfolio_state WHERE id = 1")
        cash = cursor.fetchone()[0]
        # positions_value would need to be summed; simplified for now
        cursor.execute("SELECT current_aum FROM portfolio_state WHERE id = 1")
        conn.close()
        log_message(f"Marked {updated} positions")

    def _fetch_current_price(self, market_id: str) -> Optional[float]:
        """Fetch current market price from Polymarket CLOB API."""
        try:
            resp = requests.get(f"https://clob.polymarket.com/markets/{market_id}", timeout=10)
            if resp.ok:
                data = resp.json()
                price = float(data.get('price', 0))
                return price
        except Exception as e:
            log_message(f"Failed to fetch price for {market_id}: {e}", "WARN")
        return None

    def get_portfolio_summary(self) -> Dict[str, Any]:
        """Get portfolio summary for pinned message."""
        state = self.get_state()
        conn = self._conn()
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM portfolio_positions WHERE status = 'open'")
        open_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM portfolio_positions WHERE status = 'closed'")
        closed_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM portfolio_positions WHERE status = 'closed' AND realized_pnl > 0")
        wins = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM portfolio_positions WHERE status = 'closed' AND realized_pnl <= 0")
        losses = cursor.fetchone()[0]

        cursor.execute("SELECT AVG(ABS(realized_pnl)) FROM portfolio_positions WHERE status = 'closed'")
        avg_pnl = cursor.fetchone()[0] or 0

        # Best and worst calls
        cursor.execute('''
            SELECT p.market_question, pp.realized_pnl
            FROM portfolio_positions pp
            JOIN oracle_prophecies p ON pp.prophecy_id = p.id
            WHERE pp.status = 'closed' AND pp.realized_pnl > 0
            ORDER BY pp.realized_pnl DESC LIMIT 1
        ''')
        best = cursor.fetchone()

        cursor.execute('''
            SELECT p.market_question, pp.realized_pnl
            FROM portfolio_positions pp
            JOIN oracle_prophecies p ON pp.prophecy_id = p.id
            WHERE pp.status = 'closed' AND pp.realized_pnl < 0
            ORDER BY pp.realized_pnl ASC LIMIT 1
        ''')
        worst = cursor.fetchone()

        conn.close()

        total_return = ((state['current_aum'] - state['starting_aum']) / state['starting_aum'] * 100) if state['starting_aum'] > 0 else 0
        win_rate = (wins / closed_count * 100) if closed_count > 0 else 0

        return {
            'current_aum': state['current_aum'],
            'starting_aum': state['starting_aum'],
            'total_return': total_return,
            'open_positions': open_count,
            'closed_trades': closed_count,
            'wins': wins,
            'losses': losses,
            'win_rate': win_rate,
            'best_call': best[0][:40] if best else 'N/A',
            'best_pnl': best[1] if best else 0,
            'worst_call': worst[0][:40] if worst else 'N/A',
            'worst_pnl': worst[1] if worst else 0,
        }

    def format_portfolio_pinned(self) -> str:
        """Format markdown for pinned message."""
        s = self.get_portfolio_summary()
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M") + " UTC"

        best_text = f"{s['best_call']} (+${s['best_pnl']:.2f})" if s['best_pnl'] > 0 else "N/A"
        worst_text = f"{s['worst_call']} (-${abs(s['worst_pnl']):.2f})" if s['worst_pnl'] < 0 else "N/A"

        return f"""📊 PYTHIA CAPITAL — PORTFOLIO

AUM: ${s['current_aum']:.2f}
Return: {s['total_return']:+.1f}% (started ${s['starting_aum']:.0f})
Open positions: {s['open_positions']} | Closed: {s['closed_trades']} ({s['wins']}W / {s['losses']}L)
Win rate: {s['win_rate']:.0f}%

Best call: {best_text}
Worst call: {worst_text}

Last updated: {now}
⚠️ Virtual portfolio. Not financial advice.

#polymarket #pythiacapital"""

    def update_pinned_message(self):
        """Update or create pinned message in Telegram channel."""
        if not ORACLE_BOT_TOKEN or not ORACLE_CHANNEL_ID:
            log_message("Telegram not configured, printing pinned message:")
            print(self.format_portfolio_pinned())
            return

        text = self.format_portfolio_pinned()
        url = f"https://api.telegram.org/bot{ORACLE_BOT_TOKEN}/sendMessage"

        payload = {
            "chat_id": ORACLE_CHANNEL_ID,
            "text": text,
            "parse_mode": "Markdown",
            "disable_notification": True
        }

        try:
            response = requests.post(url, json=payload, timeout=20)
            response.raise_for_status()
            data = response.json()
            if data.get('ok'):
                msg_id = data['result']['message_id']
                # Pin the message
                pin_url = f"https://api.telegram.org/bot{ORACLE_BOT_TOKEN}/pinChatMessage"
                pin_payload = {
                    "chat_id": ORACLE_CHANNEL_ID,
                    "message_id": msg_id,
                    "disable_notification": True
                }
                requests.post(pin_url, json=pin_payload, timeout=10)
                log_message(f"Pinned portfolio message: {msg_id}")
        except Exception as e:
            log_message(f"Failed to update pinned message: {e}", "ERROR")


def main():
    parser = argparse.ArgumentParser(description='Pythia Capital Portfolio Manager')
    parser.add_argument('--update-pinned', action='store_true', help='Update pinned message in channel')
    parser.add_argument('--mark-to-market', action='store_true', help='Update unrealized PnL')
    parser.add_argument('--summary', action='store_true', help='Print portfolio summary')
    args = parser.parse_args()

    pm = PortfolioManager(DB_PATH)

    if args.update_pinned:
        pm.mark_to_market()
        pm.update_pinned_message()
    elif args.mark_to_market:
        pm.mark_to_market()
    elif args.summary:
        print(pm.format_portfolio_pinned())
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
