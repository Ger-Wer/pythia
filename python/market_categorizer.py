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
    
    # Sports keywords
    sports_keywords = [
        'nba', 'nfl', 'premier league', 'champions league', 'world cup',
        'olympics', 'championship', 'finals', 'playoff', 'season',
        'tournament', 'match', 'game', 'team', 'player', 'coach',
        'score', 'win', 'lose', 'trophy', 'medal', 'super bowl',
        'fifa', 'uefa', 'mlb', 'nhl', 'formula 1', 'f1', 'tennis',
        'golf', 'boxing', 'mma', 'ufc'
    ]
    
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
        'sports': count_matches(sports_keywords),
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
    Проверяет нужно ли исключить категорию из публикации.
    
    Args:
        category: категория рынка
        exclude_list: comma-separated список исключений (из env var)
    
    Returns:
        True если категорию нужно исключить
    """
    if not exclude_list:
        return False
    
    excluded = [cat.strip().lower() for cat in exclude_list.split(',')]
    return category.lower() in excluded


def get_category_emoji(category: str) -> str:
    """Возвращает emoji для категории."""
    emoji_map = {
        'politics': '🗳️',
        'crypto': '₿',
        'sports': '⚽',
        'pop_culture': '🎬',
        'business': '💼',
        'other': '📊'
    }
    return emoji_map.get(category, '📊')


if __name__ == '__main__':
    # Test cases
    test_cases = [
        "Will Bitcoin exceed $120k by July 31?",
        "Will Trump win the 2024 presidential election?",
        "Will Manchester City win the Premier League?",
        "Will Taylor Swift release a new album in 2024?",
        "Will Tesla stock hit $300?",
        "Will it rain tomorrow in New York?"
    ]
    
    print("Market Categorization Test:\n")
    for question in test_cases:
        category = categorize_market(question)
        emoji = get_category_emoji(category)
        print(f"{emoji} {category:12s} | {question}")
