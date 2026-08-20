#!/usr/bin/env python3
"""
test_telegram_events.py - v1.0
v6.2.0 이벤트 기반 Telegram 알림 템플릿 테스트
5가지 이벤트(SIGNAL_ENTRY, SL_TRAIL, ATR_SPIKE, TP_HIT, EXIT)를
각각 더미 데이터로 전송하여 템플릿과 전송 기능을 검증합니다.
사용법: python test_telegram_events.py
"""

import sys
from pathlib import Path

# 프로젝트 루트를 PYTHONPATH에 추가
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path

# 프로젝트 루트 추가
sys.path.insert(0, str(Path(__file__).parent))

from core.logger import setup_logger
from report.telegram_sender import TelegramSender

logger = setup_logger("test_telegram")


# ============================================================
# 더미 데이터 생성 함수
# ============================================================
def make_dummy_entry(ticker="005930", name="삼성전자"):
    return {
        "ticker": ticker,
        "name": name,
        "action": "SIGNAL_ENTRY",
        "price": 84500.0,
        "entry_price": 82000.0,
        "atr": 1200.0,
        "confidence": 0.87,
        "score": 0.82,
        "positives": ["20일 이평선 상향 돌파", "외국인 순매수 3일 연속", "HBM 수요 증가"],
        "negatives": ["단기 과열 우려", "환율 변동성"],
        "entry_time": (datetime.now() - timedelta(minutes=5)).isoformat(),
        "current_stop": 79600.0,
        "timestamp": datetime.now().isoformat(),
    }


def make_dummy_sl_trail(ticker="005930", name="삼성전자"):
    return {
        "ticker": ticker,
        "name": name,
        "action": "EVENT_SL_TRAIL",
        "price": 83500.0,
        "entry_price": 82000.0,
        "old_stop": 81200.0,
        "new_stop": 82300.0,
        "atr": 1200.0,
        "pnl": 1.83,
        "timestamp": datetime.now().isoformat(),
    }


def make_dummy_atr_spike(ticker="005930", name="삼성전자"):
    return {
        "ticker": ticker,
        "name": name,
        "action": "EVENT_ATR_SPIKE",
        "price": 83000.0,
        "entry_price": 82000.0,
        "old_atr": 1200.0,
        "new_atr": 1600.0,
        "old_stop": 79600.0,
        "new_stop": 78800.0,
        "atr_change_ratio": 0.333,
        "timestamp": datetime.now().isoformat(),
    }


def make_dummy_tp_hit(ticker="005930", name="삼성전자", tp_level=1):
    levels = {1: "1차 (50%)", 2: "2차 (30%)", 3: "3차 (20%)"}
    entry_price = 82000.0
    atr = 1200.0
    if tp_level == 1:
        tp_price = entry_price + atr * 3.0
        remaining = 0.5
    elif tp_level == 2:
        tp_price = entry_price + atr * 5.0
        remaining = 0.2
    else:
        tp_price = entry_price + atr * 7.0
        remaining = 0.0

    return {
        "ticker": ticker,
        "name": name,
        "action": "EVENT_TP_HIT",
        "tp_level": tp_level,
        "tp_price": tp_price,
        "price": tp_price,
        "entry_price": entry_price,
        "remaining_qty": remaining,
        "atr": atr,
        "timestamp": datetime.now().isoformat(),
    }


def make_dummy_exit(ticker="005930", name="삼성전자", pnl=6.3):
    return {
        "ticker": ticker,
        "name": name,
        "action": "EVENT_EXIT",
        "price": 87200.0,
        "entry_price": 82000.0,
        "pnl": pnl,
        "reason": "트레일링 스탑 도달 (손절)",
        "highest_price": 87500.0,
        "lowest_price": None,
        "entry_time": (datetime.now() - timedelta(hours=5, minutes=30)).isoformat(),
        "tp_hit_level": 2,
        "timestamp": datetime.now().isoformat(),
    }


# ============================================================
# 메인 테스트
# ============================================================
async def test_all_events():
    print("\n" + "=" * 60)
    print("📨 v6.2.0 Telegram 이벤트 템플릿 테스트 시작")
    print("   (실제 Telegram으로 메시지가 전송됩니다)")
    print("=" * 60)

    sender = TelegramSender()
    if not sender.bot or not sender.chat_id:
        print("❌ Telegram 봇 토큰 또는 Chat ID가 없습니다. .env를 확인하세요.")
        return

    # 테스트할 이벤트 목록 (이름, 데이터 생성 함수)
    events = [
        ("SIGNAL_ENTRY (신규 진입)", make_dummy_entry),
        ("EVENT_SL_TRAIL (손절가 상승)", make_dummy_sl_trail),
        ("EVENT_ATR_SPIKE (ATR 급변동)", make_dummy_atr_spike),
        ("EVENT_TP_HIT (부분 익절 TP1)", lambda: make_dummy_tp_hit(tp_level=1)),
        ("EVENT_TP_HIT (부분 익절 TP2)", lambda: make_dummy_tp_hit(tp_level=2)),
        ("EVENT_TP_HIT (부분 익절 TP3)", lambda: make_dummy_tp_hit(tp_level=3)),
        ("EVENT_EXIT (최종 청산)", make_dummy_exit),
    ]

    success_count = 0
    for idx, (name, data_func) in enumerate(events, 1):
        print(f"\n[{idx}/{len(events)}] 테스트: {name}")
        data = data_func()
        # ticker와 name에 (MOCK) 추가하여 구분
        data["ticker"] = data["ticker"] + " (MOCK)"
        data["name"] = data["name"] + " (MOCK)"

        try:
            result = await sender.send(data)
            if result:
                print("   ✅ 전송 성공")
                success_count += 1
            else:
                print("   ❌ 전송 실패")
        except Exception as e:
            print(f"   ❌ 예외 발생: {e}")

        # Telegram 전송 속도 제한을 피하기 위해 잠시 대기
        await asyncio.sleep(1.5)

    # 최종 요약
    print("\n" + "=" * 60)
    print(f"🏁 테스트 완료: {success_count}/{len(events)} 개 성공")
    if success_count == len(events):
        print("   🎉 모든 이벤트 템플릿이 정상 작동합니다!")
    else:
        print("   ⚠️ 일부 실패. 로그를 확인하세요.")
    print("=" * 60)


if __name__ == "__main__":
    try:
        asyncio.run(test_all_events())
    except KeyboardInterrupt:
        print("\n🛑 사용자 중단")
    except Exception as e:
        print(f"💥 오류 발생: {e}")
        import traceback

        traceback.print_exc()
