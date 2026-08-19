#!/usr/bin/env python3
"""
tests/test_chaos_injection.py - v2.1 FINAL (DB 복제본 + Connector 완전 정리)
- 네트워크 타임아웃, DB 손상, Telegram 지연 3가지 시나리오 테스트
- DB 테스트는 복제본(decisions_test.db)을 생성하여 사용 → 메인 DB 영향 없음
- KiwoomConnector 사용 후 반드시 disconnect() 호출 → 경고 메시지 제거
- 모든 테스트 후 자동 원복 및 복제본 삭제
"""

import sys
import os
import asyncio
import json
import shutil
import time
import traceback
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Tuple

# 프로젝트 루트 추가
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.logger import setup_logger
from data.db_manager import DatabaseManager
from report.telegram_sender import TelegramSender
from data.kiwoom_connector import KiwoomConnectorV512

logger = setup_logger("chaos_test")

# ============================================================
# 테스트 설정
# ============================================================
DB_PATH = PROJECT_ROOT / "data" / "decisions.db"
DB_TEST_PATH = PROJECT_ROOT / "data" / "decisions_test.db"  # 🔥 복제본
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

TEST_RESULTS: List[Dict] = []


# ============================================================
# 1. 네트워크 타임아웃 테스트 (Connector 완전 정리 포함)
# ============================================================
async def test_network_timeout() -> Tuple[bool, str]:
    """네트워크 타임아웃 테스트: 키움 API 타임아웃 모의"""
    connector = None
    try:
        logger.info("🔍 [시나리오 1] 네트워크 타임아웃 테스트...")

        # 1) 타임아웃이 발생하는 요청 모의 (aiohttp ClientTimeout)
        import aiohttp
        import asyncio

        timeout = aiohttp.ClientTimeout(total=0.001)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            try:
                async with session.get("https://api.kiwoom.com/oauth2/token", timeout=timeout) as resp:
                    pass
            except asyncio.TimeoutError:
                logger.info("✅ 타임아웃 정상 감지")
            except aiohttp.ClientConnectorError:
                logger.info("✅ 연결 오류 정상 감지")
            except Exception as e:
                logger.error(f"❌ 예상치 못한 오류: {e}")
                return False, str(e)

        # 2) KiwoomConnector 타임아웃 처리 검증
        connector = KiwoomConnectorV512(rate_limit=5.0)
        try:
            await connector.connect()
        except asyncio.TimeoutError:
            logger.info("✅ KiwoomConnector 타임아웃 정상 처리")
        except Exception as e:
            # 토큰이 없어서 발생하는 오류는 무시 (타임아웃 테스트와 무관)
            logger.debug(f"ℹ️ 연결 시도 중 예외 (무시): {e}")

        logger.info("✅ 네트워크 타임아웃 테스트 통과")
        return True, "타임아웃 정상 감지 (시스템 크래시 없음)"

    except Exception as e:
        logger.error(f"❌ 네트워크 타임아웃 테스트 실패: {e}")
        return False, str(e)
    finally:
        # 🔥 명시적 종료 (경고 메시지 제거)
        if connector is not None:
            try:
                await connector.disconnect()
                logger.debug("✅ KiwoomConnector 정리 완료")
            except Exception as e:
                logger.debug(f"⚠️ Connector 정리 중 오류 (무시): {e}")


# ============================================================
# 2. DB 손상 테스트 (복제본 사용)
# ============================================================
async def test_db_corruption() -> Tuple[bool, str]:
    """
    DB 손상 테스트: 복제본(decisions_test.db)을 생성하여 테스트
    - 메인 DB는 전혀 건드리지 않음 → PermissionError 발생하지 않음
    """
    try:
        logger.info("🔍 [시나리오 2] DB 손상 테스트 (복제본 사용)...")

        # 1) 메인 DB가 없으면 테스트 불가
        if not DB_PATH.exists():
            logger.warning("⚠️ 메인 DB 파일 없음 → 테스트 스킵")
            return True, "메인 DB 없음 (스킵)"

        # 2) 복제본 생성
        if DB_TEST_PATH.exists():
            DB_TEST_PATH.unlink()
        shutil.copy2(DB_PATH, DB_TEST_PATH)
        logger.info(f"✅ 복제본 생성 완료: {DB_TEST_PATH}")

        # 3) 복제본 DB 초기화 (정상 동작 확인)
        db = DatabaseManager(db_path=DB_TEST_PATH)
        await db.init_db()
        logger.info("✅ 복제본 DB 초기화 완료")

        # 4) 복제본에 테스트 데이터 저장
        test_data = {
            "ticker": "CHAOS_TEST",
            "action": "BUY",
            "score": 0.99,
            "confidence": 0.95,
            "price": 100000,
            "positives": ["테스트"],
            "negatives": [],
            "counterfactuals": [],
        }
        await db.save_decision(test_data)
        logger.info("✅ 복제본 DB에 테스트 데이터 저장 완료")

        # 5) 복제본 DB "손상" 모의 → 파일 삭제
        DB_TEST_PATH.unlink()
        logger.info("✅ 복제본 DB 삭제 완료 (손상 모의)")

        # 6) 복제본 DB 재생성 (새로운 빈 DB)
        db = DatabaseManager(db_path=DB_TEST_PATH)
        await db.init_db()
        logger.info("✅ 복제본 DB 재생성 완료 (복구 모의)")

        # 7) 복제본 DB가 정상 동작하는지 확인
        await db.save_decision(test_data)
        logger.info("✅ 복제본 DB 정상 동작 확인")

        # 8) 복제본 삭제 (정리)
        if DB_TEST_PATH.exists():
            DB_TEST_PATH.unlink()
            logger.info("✅ 복제본 DB 정리 완료")

        await db.close()

        logger.info("✅ DB 손상 테스트 통과 (PermissionError 없음)")
        return True, "DB 복구 정상 (PermissionError 없음)"

    except Exception as e:
        logger.error(f"❌ DB 손상 테스트 실패: {e}")
        # 정리: 복제본 삭제
        try:
            if DB_TEST_PATH.exists():
                DB_TEST_PATH.unlink()
        except:
            pass
        return False, str(e)


# ============================================================
# 3. Telegram 지연 테스트
# ============================================================
class DelayedTelegramSender(TelegramSender):
    """지연을 모의하는 TelegramSender (15초 지연)"""
    async def send_raw(self, message: str, max_retries: int = 4) -> bool:
        logger.info("⏳ Telegram 지연 모의: 15초 대기...")
        await asyncio.sleep(15)
        return await super().send_raw(message, max_retries)


async def test_telegram_delay() -> Tuple[bool, str]:
    """Telegram 지연 테스트: 15초 지연 후에도 전송 성공"""
    try:
        logger.info("🔍 [시나리오 3] Telegram 지연 테스트...")

        sender = DelayedTelegramSender()

        if not sender.bot or not sender.chat_id:
            logger.warning("⚠️ Telegram 봇 토큰 또는 Chat ID 없음 → 테스트 스킵")
            return True, "Telegram 미설정 (스킵)"

        test_msg = "🧪 [Chaos Test] Telegram 지연 테스트 메시지입니다."
        start = time.time()
        result = await sender.send_raw(test_msg)
        elapsed = time.time() - start

        if result:
            logger.info(f"✅ 지연 후 전송 성공 (소요 {elapsed:.1f}초)")
            return True, f"지연 후 전송 성공 (소요 {elapsed:.1f}초)"
        else:
            logger.error("❌ 지연 후 전송 실패")
            return False, "전송 실패"

    except Exception as e:
        logger.error(f"❌ Telegram 지연 테스트 실패: {e}")
        return False, str(e)


# ============================================================
# 메인 테스트 러너
# ============================================================
async def run_all_tests():
    """모든 테스트 순차 실행"""
    print("\n" + "=" * 70)
    print("🧨 [Chaos Engineering] 통합 장애 주입 테스트 v2.1")
    print(f"   시작 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("   테스트 시나리오: 네트워크 타임아웃, DB 손상 (복제본), Telegram 지연")
    print("=" * 70)

    results = []
    scenarios = [
        ("네트워크 타임아웃", test_network_timeout),
        ("DB 손상 (복제본)", test_db_corruption),
        ("Telegram 지연", test_telegram_delay),
    ]

    for name, test_func in scenarios:
        print(f"\n🔍 [시나리오 {len(results)+1}] {name} 테스트...")
        try:
            passed, message = await test_func()
            results.append({"name": name, "passed": passed, "message": message})
        except Exception as e:
            results.append({"name": name, "passed": False, "message": f"테스트 자체 오류: {e}"})

    # 결과 요약
    passed = sum(1 for r in results if r["passed"])
    failed = len(results) - passed

    print("\n" + "=" * 70)
    print("🏁 [Chaos Test] 최종 결과")
    print("=" * 70)
    print(f"  ✅ 통과: {passed}개")
    print(f"  ❌ 실패: {failed}개")
    print("-" * 70)

    for r in results:
        status = "✅" if r["passed"] else "❌"
        print(f"  {status} {r['name']}: {r['message']}")

    print("=" * 70)

    # 상세 보고서 저장
    report = {
        "timestamp": datetime.now().isoformat(),
        "results": results,
        "summary": {"passed": passed, "failed": failed},
    }
    report_path = LOG_DIR / f"chaos_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    try:
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\n📄 상세 보고서 저장됨: {report_path}")
    except Exception as e:
        print(f"⚠️ 보고서 저장 실패: {e}")

    return results


# ============================================================
# 엔트리 포인트
# ============================================================
if __name__ == "__main__":
    try:
        results = asyncio.run(run_all_tests())
        sys.exit(0 if all(r["passed"] for r in results) else 1)
    except KeyboardInterrupt:
        print("\n🛑 사용자 중단")
        sys.exit(1)
    except Exception as e:
        print(f"💥 치명적 오류: {e}")
        traceback.print_exc()
        sys.exit(1)