"""
tests/test_stock_filter.py - stock_filter.py ZeroDivision 방어 검증
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from filters.stock_filter import StockFilter

def test_stock_filter_zero_division():
    sf = StockFilter()
    
    test_cases = [
        # (입력 데이터, 예상 ma 필드 문자열 포함)
        ({'ticker': '005930'}, '데이터 부족'),
        ({'ticker': '005930', 'price': 0}, '데이터 부족'),
        ({'ticker': '005930', 'price': 80000, 'ma_20': 0}, '데이터 부족'),
        ({'ticker': '005930', 'price': 80000, 'ma_20': 78000}, '상회'),
        ({'ticker': '005930', 'price': 80000, 'ma_20': 82000}, '하회'),
        ({'ticker': '005930', 'price': -100, 'ma_20': 78000}, '데이터 부족'),  # 음수 방어
    ]
    
    for i, (data, expected) in enumerate(test_cases):
        try:
            result = sf.check(data)
            ma_detail = result['details'].get('ma', '')
            assert expected in ma_detail, f"Case {i}: expected '{expected}' in '{ma_detail}'"
            print(f"✅ Case {i}: PASS (ma='{ma_detail}')")
        except Exception as e:
            print(f"❌ Case {i}: FAIL - {e}")
            raise

if __name__ == "__main__":
    test_stock_filter_zero_division()
    print("\n🎉 모든 단위 테스트 통과!")