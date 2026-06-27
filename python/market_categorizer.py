#!/usr/bin/env python3
"""
Market Categorizer - автоматическая категоризация рынков по question text
"""

import re
from typing import Optional


def categorize_market(question: str) -> str:
    """
    Определяет категорию рынка на основе question text.
    
    Returns:
        'politics', 'crypto', 'sports', 'pop_culture', 'business', 'other'
    """
    q_lower = question.lower()
    
    # Politics keywords
    politics_keywords = [
        'president', 'election', 'vote', 'poll', 'congress', 'senate',
        'governor', 'parliament', 'prime minister', 'democrat', 'republican',
        'referendum', 'ballot', 'campaign', 'political', 'legislation',
        'impeach', 'nominee', 'cabinet', 'minister', 'mayor', 'electoral'
    ]
    
    # Crypto keywords
    crypto_keywords = [
        'bitcoin', 'btc', 'ethereum', 'eth', 'crypto', 'blockchain',
        'defi', 'nft', 'solana', 'cardano', 'dogecoin', 'shib',
        'binance', 'coinbase', 'web3', 'dao', 'token', 'coin',
        'satoshi', 'mining', 'hash rate', 'halving'
    ]
    
    # Sports keywords (with subcategory detection)
    sports_nba = ['nba', 'lakers', 'celtics', 'warriors', 'knicks', 'bulls', 'heat',
                  'thunder', 'nuggets', 'clippers', 'playoff', 'finals mvp']
    sports_nfl = ['nfl', 'super bowl', 'chiefs', 'eagles', 'cowboys', 'patriots',
                  'ravens', '49ers', 'bills', 'quarterback', 'touchdown']
    sports_soccer = ['premier league', 'champions league', 'world cup', 'la liga',
                     'serie a', 'bundesliga', 'manchester', 'real madrid', 'barcelona',
                     'liverpool', 'arsenal', 'chelsea', 'bayern', 'psg', 'fifa', 'uefa',
                     'efa cup', 'euro 2026', 'copa america']
    sports_tennis = ['tennis', 'wimbledon', 'us open', 'french open', 'australian open',
                     'atp', 'wta', 'djokovic', 'alcaraz', 'sinner', 'swiatek']
    sports_mma = ['ufc', 'mma', 'boxing', 'fight', 'knockout', 'title fight']
    sports_cricket = ['cricket', 'ipl', 'test match', 't20', 'world cup cricket']
    sports_esports = ['esports', 'league of legends', 'dota', 'csgo', 'valorant',
                      'counter-strike', 'major tournament']
    
    sports_all = (sports_nba + sports_nfl + sports_soccer + sports_tennis +
                  sports_mma + sports_cricket + sports_esports)
    
    # Pop culture keywords
    pop_culture_keywords = [
        'oscar', 'grammy', 'emmy', 'golden globe', 'movie', 'film',
        'album', 'song', 'artist', 'actor', 'actress', 'director',
        'box office', 'billboard', 'spotify', 'netflix', 'disney',
        'marvel', 'star wars', 'taylor swift', 'beyonce', 'drake',
        'release', 'premiere', 'concert', 'tour', 'award', 'nominee'
    ]
    
    # Business/Tech keywords
    business_keywords = [
        'stock', 'market cap', 'ipo', 'merger', 'acquisition', 'ceo',
        'earnings', 'revenue', 'apple', 'google', 'amazon', 'microsoft',
        'tesla', 'meta', 'twitter', 'openai', 'nvidia', 'startup',
        'valuation', 'funding', 'bankrupt', 'layoff'
    ]
    
    # Count matches for each category
    def count_matches(keywords):
        return sum(1 for kw in keywords if kw in q_lower)
    
    scores = {
        'politics': count_matches(politics_keywords),
        'crypto': count_matches(crypto_keywords),
        'sports_nba': count_matches(sports_nba),
        'sports_nfl': count_matches(sports_nfl),
        'sports_soccer': count_matches(sports_soccer),
        'sports_tennis': count_matches(sports_tennis),
        'sports_mma': count_matches(sports_mma),
        'sports_cricket': count_matches(sports_cricket),
        'sports_esports': count_matches(sports_esports),
        'sports': count_matches(sports_all) - count_matches(sports_nba) - count_matches(sports_nfl) - count_matches(sports_soccer) - count_matches(sports_tennis) - count_matches(sports_mma) - count_matches(sports_cricket) - count_matches(sports_esports),
        'pop_culture': count_matches(pop_culture_keywords),
        'business': count_matches(business_keywords)
    }
    
    # Return category with highest score (minimum 1 match required)
    max_score = max(scores.values())
    if max_score == 0:
        return 'other'
    
    for category, score in scores.items():
        if score == max_score:
            return category
    
    return 'other'


def should_exclude_category(category: str, exclude_list: str = "") -> bool:
    """
    Check if a category should be excluded from publishing.
    Supports sports subcategories: excluding 'sports' also excludes 'sports_nba', etc.
    """
    if not exclude_list:
        return False
    
    excluded = [cat.strip().lower() for cat in exclude_list.split(',')]
    category_lower = category.lower()
    
    # Direct match
    if category_lower in excluded:
        return True
    
    # If 'sports' is excluded, exclude all sports_* subcategories
    if 'sports' in excluded and category_lower.startswith('sports_'):
        return True
    
    return False


def get_category_emoji(category: str) -> str:
    """Returns emoji for category (with sports subcategories)."""
    emoji_map = {
        'politics': '🗳️',
        'crypto': '₿',
        'sports': '⚽',
        'sports_nba': '🏀',
        'sports_nfl': '🏈',
        'sports_soccer': '⚽',
        'sports_tennis': '🎾',
        'sports_mma': '🥊',
        'sports_cricket': '🏏',
        'sports_esports': '🎮',
        'pop_culture': '🎬',
        'business': '💼',
        'other': '📊'
    }
    return emoji_map.get(category, '📊')


if __name__ == '__main__':
    # Test cases with sports subcategories
    test_cases = [
        "Will Bitcoin exceed $120k by July 31?",
        "Will Trump win the 2024 presidential election?",
        "Will Manchester City win the Premier League?",
        "Will the Lakers win the NBA Finals?",
        "Will the Chiefs win the Super Bowl?",
        "Will Djokovic win Wimbledon?",
        "Will Taylor Swift release a new album in 2024?",
        "Will Tesla stock hit $300?",
        "Will Israel Adesanya win the UFC title fight?",
        "Will Mumbai Indians win the IPL?",
    ]
    
    print("Market Categorization Test:\n")
    for question in test_cases:
        category = categorize_market(question)
        emoji = get_category_emoji(category)
        print(f"{emoji} {category:16s} | {question}")
