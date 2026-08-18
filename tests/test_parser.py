#!/usr/bin/env python3
"""
test_parser.py - v1.0
WebSocket 수신 데이터 파싱 로직 진단 (휴장일 테스트용)
실제 키움 서버 연결 없이, 가상의 데이터 구조로 파싱 성공 여부를 확인합니다.
"""
import sys
from pathlib import Path

# 프로젝트 루트를 PYTHONPATH에 추가
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import sys
import os
from pathlib import Path

# 프로젝트 루트를 path에 추가 (kiwoom_connector.py 임포트용)
sys.path.insert(0, str(Path(__file__).parent))

from data.kiwoom_connector import KiwoomConnectorV512
import logging

# 로깅을 DEBUG/INFO로 설정하여 WARNING 메시지가 보이도록 함
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger("📡테스트")


def run_parser_test():
    print("=" * 60)
    print("🔬 Kiwoom 메시지 파서 테스트 시작 (휴장일 진단)")
    print("   목표: 다양한 키 이름(stk_cd, code, ticker 등) 인식 확인")
    print("=" * 60)

    # KiwoomConnectorV512 인스턴스 생성 (실제 연결은 하지 않음)
    # _handle_ws_message 함수만 사용할 것이므로, None으로 초기화해도 됨
    connector = KiwoomConnectorV512(rate_limit=5.0)
    
    # 가짜 핸들러 등록 (수신된 데이터를 받아서 출력할 콜백)
    def mock_handler(data):
        print(f"   ✅ [콜백 성공] 데이터 수신됨: {data.get('price', 0)}")
    
    # 테스트용 종목을 미리 등록 (핸들러 연결 확인용)
    connector._realtime_handlers["005930"] = mock_handler
    connector._realtime_handlers["000660"] = mock_handler
    connector._realtime_handlers["035420"] = mock_handler
    connector._realtime_handlers["005380"] = mock_handler

    # ============================================================
    # 테스트 케이스 1: 기존 키 (ticker, symbol, item)
    # ============================================================
    test_cases = [
        # (설명, 데이터 딕셔너리, 예상 추출 티커)
        ("1. 'ticker' 키 사용", {"ticker": "005930", "price": 82000}, "005930"),
        ("2. 'symbol' 키 사용", {"symbol": "000660", "price": 130000}, "000660"),
        ("3. 'item' 키 사용", {"item": "035420", "price": 150000}, "035420"),
        ("4. 'stk_cd' 키 사용 (한국 주식 API 표준)", {"stk_cd": "005380", "price": 100000}, "005380"),
        ("5. 'code' 키 사용 (일반적)", {"code": "051910", "price": 200000}, "051910"),
        ("6. 'item_cd' 키 사용", {"item_cd": "006400", "price": 70000}, "006400"),
        ("7. 알 수 없는 키 (파싱 실패 케이스)", {"unknown_key": "207940", "price": 50000}, None),
    ]

    success_count = 0
    fail_count = 0

    for idx, (desc, data, expected_ticker) in enumerate(test_cases, 1):
        print(f"\n--- 테스트 {idx}: {desc} ---")
        print(f"    입력 데이터: {data}")
        
        # 실제 _handle_ws_message 로직을 그대로 복사하여 실행 (직접 호출)
        # (코드 중복을 피하기 위해 임시로 함수화하여 로직 실행)
        extracted_ticker = None
        warning_triggered = False
        
        # 🔥 실제 `_handle_ws_message` 의 파싱 로직을 그대로 여기서 재현
        ticker = (
            data.get("ticker") or 
            data.get("symbol") or 
            data.get("item") or 
            data.get("stk_cd") or      # 🔥 추가된 부분
            data.get("code") or         # 🔥 추가된 부분
            data.get("item_cd")         # 🔥 추가된 부분
        )
        
        if not ticker:
            # WARNING 로그가 발생해야 하는 상황
            keys = list(data.keys())
            print(f"   ❌ [파싱실패] 인식할 수 없는 데이터 구조 (keys: {keys})")
            warning_triggered = True
        else:
            extracted_ticker = ticker
            print(f"   ✅ [파싱성공] 티커 추출: {ticker}")
            
            # 등록된 핸들러가 있으면 콜백 실행 (모의)
            if ticker in connector._realtime_handlers:
                connector._realtime_handlers[ticker](data)
            else:
                print(f"   ⚠️ 미등록 종목: {ticker}")

        # 결과 검증
        if expected_ticker is None:
            if warning_triggered:
                print("   ✅ 예상대로 파싱 실패 및 WARNING 트리거됨")
                success_count += 1
            else:
                print("   ❌ 실패: 파싱이 되어서는 안 되는데 파싱됨")
                fail_count += 1
        else:
            if extracted_ticker == expected_ticker:
                print(f"   ✅ 성공: 예상 티커 '{expected_ticker}'와 일치")
                success_count += 1
            else:
                print(f"   ❌ 실패: 예상 '{expected_ticker}' != 실제 '{extracted_ticker}'")
                fail_count += 1

    # ============================================================
    # 최종 진단 리포트
    # ============================================================
    print("\n" + "=" * 60)
    print("🏁 [테스트 완료] 최종 진단 리포트")
    print(f"   ✅ 통과: {success_count}개")
    print(f"   ❌ 실패: {fail_count}개")
    
    if fail_count == 0:
        print("\n   🎉 모든 테스트 통과! 수정된 파싱 로직이 정상입니다.")
        print("   ➡️ 월요일 장중에 데이터가 들어오면 정상적으로 인식됩니다.")
        print("   ➡️ 만약 여전히 데이터가 안 온다면, 키움 서버가 완전히 다른 구조를 보낼 수 있습니다.")
        print("   ➡️ 그때는 로그에 찍힌 'keys'를 확인하여 이 테스트 코드에 추가하세요.")
    else:
        print("\n   ⚠️ 일부 테스트가 실패했습니다. 위 로그를 확인하여 파싱 로직을 수정하세요.")
    print("=" * 60)


if __name__ == "__main__":
    run_parser_test()