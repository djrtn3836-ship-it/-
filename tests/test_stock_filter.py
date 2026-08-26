#!/usr/bin/env python3
"""
test_stock_filter.py - v1.1 (UTF-8 강제)
"""

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from filters.stock_filter import StockFilter


def test_stock_filter_zero_division():
    sf = StockFilter()
    test_cases = [
        ({"ticker": "005930"}, "데이터 부족"),
        ({"ticker": "005930", "price": 0}, "데이터 부족"),
        ({"ticker": "005930", "price": 80000, "ma_20": 78000}, "상회"),
        ({"ticker": "005930", "price": 80000, "ma_20": 82000}, "하회"),
    ]
    for i, (data, expected) in enumerate(test_cases):
        try:
            result = sf.check(data)
            ma_detail = result["details"].get("ma", "")
            assert expected in ma_detail, f"Case {i}: expected '{expected}' in '{ma_detail}'"
            print(f"[PASS] Case {i}: PASS (ma='{ma_detail}')")
        except Exception as e:
            print(f"[FAIL] Case {i}: FAIL - {e}")
            raise


if __name__ == "__main__":
    test_stock_filter_zero_division()
    print("\n[SUCCESS] 모든 단위 테스트 통과!")
