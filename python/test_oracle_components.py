#!/usr/bin/env python3
"""
Oracle Components Test - базовые тесты для oracle модулей
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from python.market_categorizer import categorize_market, get_category_emoji, should_exclude_category


def test_market_categorizer():
    """Test market categorization."""
    print("\n=== Testing Market Categorizer ===\n")
    
    test_cases = [
        ("Will Bitcoin exceed $120k by July 31?", "crypto", "₿"),
        ("Will Trump win the 2024 presidential election?", "politics", "🗳️"),
        ("Will Manchester City win the Premier League?", "sports", "⚽"),
        ("Will Taylor Swift release a new album in 2024?", "pop_culture", "🎬"),
        ("Will Tesla stock hit $300?", "business", "💼"),
        ("Will it rain tomorrow in New York?", "other", "📊"),
    ]
    
    passed = 0
    failed = 0
    
    for question, expected_category, expected_emoji in test_cases:
        category = categorize_market(question)
        emoji = get_category_emoji(category)
        
        if category == expected_category and emoji == expected_emoji:
            print(f"✅ PASS: {category:12s} {emoji} | {question[:50]}")
            passed += 1
        else:
            print(f"❌ FAIL: Expected {expected_category} {expected_emoji}, got {category} {emoji}")
            print(f"         Question: {question[:50]}")
            failed += 1
    
    print(f"\nResults: {passed} passed, {failed} failed")
    return failed == 0


def test_category_exclusion():
    """Test category exclusion logic."""
    print("\n=== Testing Category Exclusion ===\n")
    
    test_cases = [
        ("sports", "sports", True),
        ("sports", "sports,crypto", True),
        ("crypto", "sports", False),
        ("politics", "", False),
        ("sports", "Sports", True),  # case-insensitive
    ]
    
    passed = 0
    failed = 0
    
    for category, exclude_list, expected in test_cases:
        result = should_exclude_category(category, exclude_list)
        
        if result == expected:
            status = "excluded" if result else "included"
            print(f"✅ PASS: '{category}' {status} when exclude_list='{exclude_list}'")
            passed += 1
        else:
            print(f"❌ FAIL: Expected {expected}, got {result}")
            print(f"         Category: {category}, Exclude: {exclude_list}")
            failed += 1
    
    print(f"\nResults: {passed} passed, {failed} failed")
    return failed == 0


def test_imports():
    """Test that all oracle modules can be imported."""
    print("\n=== Testing Module Imports ===\n")
    
    modules = [
        "python.oracle_publisher",
        "python.oracle_score_updater",
        "python.oracle_scorecard",
        "python.oracle_backfill",
    ]
    
    passed = 0
    failed = 0
    
    for module_name in modules:
        try:
            __import__(module_name)
            print(f"✅ PASS: {module_name}")
            passed += 1
        except Exception as e:
            print(f"❌ FAIL: {module_name} - {e}")
            failed += 1
    
    print(f"\nResults: {passed} passed, {failed} failed")
    return failed == 0


def main():
    """Run all tests."""
    print("=" * 60)
    print("ORACLE COMPONENTS TEST SUITE")
    print("=" * 60)
    
    all_passed = True
    
    all_passed &= test_market_categorizer()
    all_passed &= test_category_exclusion()
    all_passed &= test_imports()
    
    print("\n" + "=" * 60)
    if all_passed:
        print("✅ ALL TESTS PASSED")
        print("=" * 60)
        return 0
    else:
        print("❌ SOME TESTS FAILED")
        print("=" * 60)
        return 1


if __name__ == '__main__':
    sys.exit(main())
