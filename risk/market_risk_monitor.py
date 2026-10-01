# -*- coding: utf-8 -*-
"""risk/market_risk_monitor.py - 시장 리스크 서킷브레이커 배선.

배경
----
`risk/circuit_breaker.py`의 `CircuitBreakerManager`는 구현(3종 차단기 + 상태머신)이
완성돼 있었으나 **프로덕션 참조가 0건인 고아 모듈**이었다(2026-10-01 P3-1 감사).
본 모듈이 실제 시장 데이터를 공급해 차단기를 동작시킨다.

    3종 차단기                        입력 소스
    ─────────────────────────────    ──────────────────────────────────
    VolatilityBreaker                유니버스 동일가중 일간수익률의 20일 표준편차
    LiquidityBreaker                 최근 전체 거래량 / 20일 평균
    ConsecutiveLossBreaker           최근 확정된 모의(참고신호) 실현 수익률

운영
----
- **스케줄**: OHLCV 백필(16:30) 직후 16:45 (영업일만) → 당일 종가 기준으로 갱신
- **알림**: 어느 차단기든 OPEN이면 텔레그램 경고 1회
- **연동**: `cb_input_provider()`가 `OpsMonitor`에 주입되어 근본원인 분석(RCA)에
  서킷브레이커 상태가 함께 반영된다.

주의: 본 시스템은 자동매매를 하지 않으므로 "차단"은 **신호 신뢰도 경고**의 의미다.
"""

from __future__ import annotations

import statistics
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.logger import setup_logger
from core.runtime_mode import get_runtime_mode
from risk.circuit_breaker import CircuitBreakerManager

logger = setup_logger("market_risk_monitor")

WINDOW = 20          # 변동성/거래량 평균 산정 창(거래일)
MIN_SERIES = 5       # 최소 유효 종목 수

_MANAGER: Optional[CircuitBreakerManager] = None


def get_circuit_breaker_manager() -> CircuitBreakerManager:
    """프로세스 전역 서킷브레이커 매니저(싱글톤)."""
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = CircuitBreakerManager()
    return _MANAGER


async def compute_market_inputs(
    db: Any, tickers: Sequence[str], window: int = WINDOW
) -> Tuple[float, float]:
    """(일간 변동성, 거래량 비율)을 추정한다.

    Args:
        db: DatabaseManager
        tickers: 유니버스 종목 코드
        window: 산정 창(거래일)

    Returns:
        (volatility, volume_ratio). 데이터 부족 시 (0.0, 1.0) — 차단기 미발동.
    """
    from validation.momentum_backtest import load_price_series

    end = datetime.now().strftime("%Y-%m-%d")
    # window 거래일 ≈ 달력일 ×2 (주말/휴일 감안)
    start = (date.fromisoformat(end) - timedelta(days=int(window * 2.2) + 20)).isoformat()

    try:
        series, dates = await load_price_series(db, tickers, start, end, min_bars=window + 1)
    except Exception as e:
        logger.warning(f"시장 입력 계산 실패(변동성): {e}")
        return 0.0, 1.0

    if len(dates) < 3 or len(series) < MIN_SERIES:
        logger.warning(f"시장 입력 데이터 부족: 종목 {len(series)}개, 날짜 {len(dates)}일")
        return 0.0, 1.0

    # 동일가중 일간 수익률 → 표준편차(변동성)
    daily: List[float] = []
    for i in range(1, len(dates)):
        d0, d1 = dates[i - 1], dates[i]
        rets = [
            series[t][d1] / series[t][d0] - 1.0
            for t in series
            if d0 in series[t] and d1 in series[t] and series[t][d0] > 0
        ]
        if rets:
            daily.append(sum(rets) / len(rets))

    volatility = statistics.pstdev(daily[-window:]) if len(daily) >= 2 else 0.0

    # 거래량 비율 = 최근 전체 거래량 / 직전 window일 평균
    volume_ratio = 1.0
    try:
        rows = await db.get_daily_total_volume(window + 1)
        vols = [float(r["total_volume"]) for r in rows if r.get("total_volume")]
        if len(vols) >= 2:
            latest, prev = vols[0], vols[1:]
            avg = sum(prev) / len(prev)
            if avg > 0:
                volume_ratio = latest / avg
    except Exception as e:
        logger.warning(f"거래량 비율 계산 실패(무시): {e}")

    return float(volatility), float(volume_ratio)


async def evaluate_market_risk(send_alert: bool = True) -> Dict[str, Any]:
    """시장 입력을 계산해 차단기를 갱신하고 상태를 반환한다."""
    from data.db_manager import DatabaseManager
    from infrastructure.market_data.universe_provider import get_universe

    universe = get_universe()
    db = DatabaseManager()
    volatility, volume_ratio = 0.0, 1.0
    trade_return: Optional[float] = None
    try:
        volatility, volume_ratio = await compute_market_inputs(db, list(universe.keys()))
        trade_return = await db.get_latest_momentum_paper_return()
    except Exception as e:
        logger.warning(f"시장 리스크 입력 조회 실패: {e}")
    finally:
        try:
            await db.close()
        except Exception:
            pass

    manager = get_circuit_breaker_manager()
    status = manager.update(
        trade_return=float(trade_return) if trade_return is not None else 0.0,
        current_volatility=volatility,
        volume_ratio=volume_ratio,
    )

    summary = {
        "is_open": status.is_open,
        "open_breakers": list(status.open_breakers),
        "states": dict(status.states),
        "volatility": round(volatility, 4),
        "volume_ratio": round(volume_ratio, 3),
        "trade_return": None if trade_return is None else round(trade_return, 4),
    }

    if status.is_open:
        logger.warning(
            f"[서킷브레이커 OPEN] {', '.join(status.open_breakers)} | "
            f"변동성 {volatility:.2%}, 거래량비율 {volume_ratio:.2f}"
        )
        if send_alert:
            await _send_alert(summary)
    else:
        logger.info(
            f"서킷브레이커 정상 | 변동성 {volatility:.2%}, 거래량비율 {volume_ratio:.2f}"
        )
    return summary


async def _send_alert(summary: Dict[str, Any]) -> None:
    try:
        from report.telegram_sender import TelegramSender

        text = (
            "🚨 <b>시장 리스크 경고 (서킷브레이커)</b>\n"
            f"• 발동: <b>{', '.join(summary['open_breakers'])}</b>\n"
            f"• 일간 변동성: {summary['volatility']:.2%}\n"
            f"• 거래량 비율: {summary['volume_ratio']:.2f} (20일 평균 대비)\n"
            f"• 상태: {summary['states']}\n"
            "<i>참고용 경고 — 자동매매 아님</i>"
        )
        await TelegramSender().send_raw(text)
    except Exception as e:
        logger.warning(f"서킷브레이커 알림 전송 실패: {e}")


def cb_input_provider() -> Any:
    """OpsMonitor용 서킷브레이커 상태 공급자(RCA에 반영)."""
    from observability.root_cause_analyzer import CircuitBreakerInput

    status = get_circuit_breaker_manager().status()
    return CircuitBreakerInput(
        is_open=status.is_open,
        open_count=len(status.open_breakers),
        breaker_names=list(status.open_breakers),
        status_summary=", ".join(f"{k}:{v}" for k, v in status.states.items()),
    )


async def scheduled_market_risk_check() -> None:
    """스케줄러 진입점(영업일 16:45). 비상장일에는 건너뛴다."""
    from core.holiday_utils import is_trading_day

    if not is_trading_day():
        return
    if get_runtime_mode().external_io_disabled:  # 안전모드(TEST_MODE)에서는 생략
        logger.info("🧪 [SAFE MODE] 시장 리스크 점검 생략")
        return
    try:
        await evaluate_market_risk()
    except Exception as e:
        logger.error(f"시장 리스크 점검 실패: {e}")


if __name__ == "__main__":
    import asyncio
    import sys

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    result = asyncio.run(evaluate_market_risk(send_alert=False))
    print(f"서킷브레이커 상태: {result}")
