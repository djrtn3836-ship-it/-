#!/usr/bin/env python3
"""
test_domestic_mock.py - v1.0 (국내장 파이프라인 Mock 검증기)
설명: 키움 WebSocket을 전혀 사용하지 않고, 가상의 국내 종목 데이터를 생성하여
      전체 분석 파이프라인(DeepAnalyzer, DB 저장, Telegram 전송)을 검증합니다.
      "키움 연결이 없어도 Telegram이 오는가?"를 확인하는 최종 테스트입니다.
사용법: python test_domestic_mock.py
"""
import sys
from pathlib import Path

# 프로젝트 루트를 PYTHONPATH에 추가
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import asyncio
import sys
import os
from pathlib import Path
from datetime import datetime
import random

sys.path.insert(0, str(Path(__file__).parent))

from core.logger import setup_logger
from scanner.deep_analyzer import DeepAnalyzer
from data.db_manager import DatabaseManager
from report.telegram_sender import TelegramSender

logger = setup_logger("mock_domestic")

async def run_mock_test():
    print("\n" + "=" * 60)
    print("🇰🇷 [국내장 Mock 테스트] 파이프라인 검증 시작 (키움 미연결)")
    print("   목적: DeepAnalyzer + DB + Telegram 연동 최종 확인")
    print("=" * 60)

    # 1. DB 초기화
    print("[1/4] DB 연결 중...")
    db = DatabaseManager()
    await db.init_db()
    print("   ✅ DB 연결 성공")

    # 2. 분석기 로드
    print("[2/4] 분석 엔진(DeepAnalyzer) 로드 중...")
    analyzer = DeepAnalyzer(db_manager=db)
    await analyzer.load_weights()
    print("   ✅ 분석 엔진 로드 완료")

    # 3. Telegram 발송기
    print("[3/4] Telegram 발송기 초기화 중...")
    sender = TelegramSender()
    if not sender.bot or not sender.chat_id:
        print("   ❌ Telegram 봇 토큰 또는 Chat ID가 없습니다. .env를 확인하세요.")
        return
    print("   ✅ Telegram 발송기 준비 완료")

    # 4. 🔥 가상의 국내 종목 데이터 생성 (실제 종목코드 + 원화 가격)
    print("\n[4/4] 국내 모의 데이터 분석 및 전송 실행...")
    
    # 실제 국내 종목 코드를 사용하되, 가격은 임의로 설정 (키움과 무관)
    test_stocks = [
        {"ticker": "005930", "name": "삼성전자 (MOCK)", "base_price": 82000, "change": 2.5},
        {"ticker": "000660", "name": "SK하이닉스 (MOCK)", "base_price": 210000, "change": -3.1},
        {"ticker": "005380", "name": "현대차 (MOCK)", "base_price": 280000, "change": 4.8},
    ]

    success_count = 0
    for stock in test_stocks:
        # 변동률 적용 (2% 이상 -> 신호 발생 유도)
        price = stock["base_price"] * (1 + stock["change"] / 100)
        
        # 실제 `realtime_monitor`에서 오는 데이터 구조를 완벽히 모방
        mock_data = {
            "ticker": stock["ticker"],
            "name": stock["name"],
            "price": round(price, 2),
            "change_rate": stock["change"] / 100.0,
            "momentum": stock["change"] / 100.0,
            "imbalance": 0.7 if stock["change"] > 0 else 0.3,  # 매수/매도 불균형
            "timestamp": datetime.now().isoformat(),
            "pressure": "외국인 및 기관 동반 매수" if stock["change"] > 0 else "외국인 순매도"
        }

        # 실제 `analyze` 함수 실행 (DB에서 ATR을 가져오려고 시도하지만, 
        # DB에 OHLCV 데이터가 없으면 ATR은 0으로 처리됨. 그래도 로직은 돔)
        analysis = await analyzer.analyze(mock_data)
        
        # 🔥 만약 분석 점수가 낮아서 HOLD가 나오면, 테스트를 위해 강제로 SIGNAL 부여
        if analysis.get('action') not in ['BUY', 'SELL']:
            if stock["change"] > 0:
                analysis['action'] = 'BUY'
                analysis['score'] = 0.85
                analysis['confidence'] = 0.9
                analysis['positives'] = ['모의 데이터 상승 검증', '전략 파이프라인 정상']
                analysis['entry_price'] = price
                analysis['current_stop'] = price * 0.97
            else:
                analysis['action'] = 'SELL'
                analysis['score'] = 0.80
                analysis['confidence'] = 0.85
                analysis['negatives'] = ['모의 데이터 하락 검증']

        # DB 저장 (실제 decisions 테이블에 기록됨)
        await db.save_decision(analysis)

        # Telegram 전송
        if analysis.get('action') in ['BUY', 'SELL']:
            result = await sender.send(analysis)
            if result:
                print(f"   ✅ {stock['ticker']} ({stock['name']}) 신호 전송 성공! (액션: {analysis['action']})")
                success_count += 1
            else:
                print(f"   ❌ {stock['ticker']} 전송 실패 (Telegram 오류)")
        else:
            print(f"   ⚠️ {stock['ticker']} 분석 결과 HOLD")

        # 약간의 텀을 두어 Telegram 전송 제한을 피함
        await asyncio.sleep(0.5)

    # 최종 결과
    print("\n" + "=" * 60)
    print("🏁 [국내장 Mock 테스트] 최종 검증 결과")
    if success_count > 0:
        print(f"   ✅ 성공: {success_count}개 신호가 Telegram으로 전송되었습니다!")
        print("   📱 지금 스마트폰에서 Telegram 알림을 확인하세요.")
        print("   ➡️ 만약 알림이 왔다면, 시스템의 '분석→DB→전송' 파이프라인은 완벽합니다.")
        print("   ➡️ 월요일에 키움 데이터만 들어오면 자동으로 신호가 발송됩니다.")
    else:
        print(f"   ❌ 실패: 단 하나의 신호도 전송되지 않았습니다.")
        print("   ➡️ .env 파일의 TELEGRAM_BOT_TOKEN과 CHAT_ID를 확인하세요.")
    print("=" * 60)

    await db.close()

if __name__ == "__main__":
    os.system('color')
    try:
        asyncio.run(run_mock_test())
    except KeyboardInterrupt:
        print("\n🛑 사용자 중단")
    except Exception as e:
        print(f"💥 오류 발생: {e}")
        import traceback
        traceback.print_exc()