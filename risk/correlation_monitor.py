# -*- coding: utf-8 -*-
"""risk/correlation_monitor.py - 상관행렬 기반 집중도 리스크 모니터 (P8-1).

배경
----
`risk/correlation_matrix.py`(388줄, RollingCorrelation/DiversificationReport)는
작성되어 있었으나 **프로덕션 참조 0건의 고아 모듈**이었다.

P5/P6 검증 결론(가격 팩터에 알파 없음 → 시스템 가치는 감시·리스크 알림)에 따라,
5년치 OHLCV(23만행)를 이용해 **종목 간 동조화(집중도) 리스크**를 정기 점검한다.

동작
----
1. 구독 유니버스 상위 N종목(기본 50)의 최근 window+1 거래일 종가를 읽어 일간 수익률 산출
2. `RollingCorrelation`에 투입 → 분산화 점수 + 고상관(|ρ| ≥ 0.8) 종목쌍 탐지
3. 고상관 쌍이 임계 이상이면 텔레그램 경고(쿨다운), 결과는 대시보드에도 노출

사용:
    python -m risk.correlation_monitor
"""

from __future__ import annotations

import argparse
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence

from core.logger import setup_logger
from risk.correlation_matrix import RollingCorrelation

logger = setup_logger("corr_monitor")

DEFAULT_WINDOW = 60          # 거래일
DEFAULT_MAX_TICKERS = 50     # 한 번에 평가할 종목 수(연산량 O(n²))
DEFAULT_ALERT_PAIRS = 5      # 고상관 쌍이 이 개수 이상이면 경고
ALERT_COOLDOWN_SEC = 6 * 3600

_last_alert_at: float = 0.0
_last_report: Optional[Dict[str, Any]] = None


def get_last_report() -> Dict[str, Any]:
    """마지막 점검 결과(대시보드 등에서 재사용). 없으면 빈 dict."""
    return dict(_last_report) if _last_report else {}


async def load_universe_tickers(db: Any, limit: int) -> List[str]:
    """평가 대상 종목을 고른다 — 최근 거래량 합계 상위(유동성 기준)."""
    try:
        rows = await db._execute_read(
            """SELECT ticker, SUM(volume) AS vol
               FROM ohlcv
               WHERE date >= date('now', '-30 day')
               GROUP BY ticker
               ORDER BY vol DESC
               LIMIT ?""",
            (int(limit),),
        )
        tickers = [str(r["ticker"]) for r in rows if r.get("ticker")]
        if tickers:
            return tickers
    except Exception as e:
        logger.warning(f"유동성 기준 종목 선정 실패({e}) → 유니버스 순서 사용")

    from infrastructure.market_data.universe_provider import get_universe

    return list(get_universe().keys())[:limit]


async def compute_correlation_report(
    db: Any,
    window: int = DEFAULT_WINDOW,
    max_tickers: int = DEFAULT_MAX_TICKERS,
    reference_date: Optional[str] = None,
) -> Dict[str, Any]:
    """상관행렬·분산화 점수·고상관 쌍을 계산해 반환한다."""
    tickers = await load_universe_tickers(db, max_tickers)
    if len(tickers) < 2:
        return {"status": "insufficient_tickers", "tickers": tickers}

    end = reference_date or datetime.now().strftime("%Y-%m-%d")
    # window+5: 거래일 기준 여유(주말/휴일 제외)
    start = (datetime.fromisoformat(end) - timedelta(days=int(window * 1.6) + 10)).strftime("%Y-%m-%d")

    rolling = RollingCorrelation(window=window, max_tickers=max_tickers)
    series: Dict[str, Dict[str, float]] = {}
    for code in tickers:
        try:
            bars = await db.get_ohlcv_range(code, start, end)
        except Exception as e:
            logger.debug(f"[{code}] 시세 조회 실패(무시): {e}")
            continue
        prices = {
            str(b["date"]): float(b["close"])
            for b in bars
            if b.get("close") and float(b["close"]) > 0
        }
        if len(prices) >= 20:
            series[code] = prices

    if len(series) < 2:
        return {"status": "insufficient_data", "loaded": len(series)}

    # 🔴 위치 기반 정렬 금지: 결측일이 있는 종목이 섞이면 시계열이 어긋나
    #    가짜 상관(예: ρ≈0.95)이 만들어진다. → 공통 거래일(교집합)만 사용한다.
    common_dates = sorted(set.intersection(*(set(p) for p in series.values())))
    if len(common_dates) < 20:
        logger.warning(
            f"공통 거래일 부족({len(common_dates)}일) — 종목 수를 줄이거나 기간을 늘려야 함"
        )
        return {"status": "insufficient_data", "common_dates": len(common_dates)}

    # 공통 거래일 기준으로 날짜별 수익률을 만들어 투입(날짜 정렬 보장)
    for prev_d, cur_d in zip(common_dates, common_dates[1:]):
        batch: Dict[str, float] = {}
        for code, prices in series.items():
            prev_p, cur_p = prices.get(prev_d), prices.get(cur_d)
            if prev_p and cur_p:
                batch[code] = (cur_p - prev_p) / prev_p
        if batch:
            rolling.add_returns_batch(batch)

    loaded = len(series)

    report = rolling.diversification_score(tickers)
    pairs = sorted(
        (p.to_dict() for p in report.high_corr_pairs),
        key=lambda d: abs(float(d.get("correlation", 0.0))),
        reverse=True,
    )
    result = {
        "status": "ok",
        "as_of": end,
        "window": window,
        "common_dates": len(common_dates),
        "evaluated_tickers": loaded,
        "score": round(float(report.score), 4),
        "avg_abs_correlation": round(float(report.avg_abs_correlation), 4),
        "high_corr_pair_count": len(pairs),
        "top_pairs": pairs[:10],
        "recommendation": report.recommendation,
    }
    return result


async def _notify(result: Dict[str, Any]) -> None:
    global _last_alert_at
    now = time.time()
    if now - _last_alert_at < ALERT_COOLDOWN_SEC:
        logger.info("상관 경고 쿨다운 중 — 알림 생략")
        return
    _last_alert_at = now

    lines = [
        "📉 <b>집중도 리스크 경고</b>",
        f"• 분산화 점수: {result['score']:.2f} (낮을수록 동조화)",
        f"• 평균 |상관|: {result['avg_abs_correlation']:.2f}",
        f"• 고상관 쌍(|ρ|≥0.8): {result['high_corr_pair_count']}개 / {result['evaluated_tickers']}종목",
    ]
    for p in result.get("top_pairs", [])[:5]:
        a = p.get("ticker_a", p.get("ticker1", "?"))
        b = p.get("ticker_b", p.get("ticker2", "?"))
        c = float(p.get("correlation", 0.0))
        lines.append(f"   - {a} ↔ {b}: ρ {c:+.2f}")
    lines.append("<i>동일 방향 급락 위험 — 분산 부족</i>")

    try:
        from report.telegram_sender import TelegramSender

        await TelegramSender().send_raw("\n".join(lines))
    except Exception as e:
        logger.warning(f"집중도 경고 전송 실패: {e}")


async def scheduled_correlation_check() -> Dict[str, Any]:
    """스케줄러 진입점(평일 장 마감 후 1회)."""
    global _last_report
    from data.db_manager import DatabaseManager

    db = DatabaseManager()
    try:
        result = await compute_correlation_report(db)
    except Exception as e:
        logger.error(f"상관행렬 점검 실패: {e}")
        return {"status": "error", "error": str(e)}
    finally:
        try:
            await db.close()
        except Exception:
            pass

    _last_report = result
    if result.get("status") != "ok":
        logger.warning(f"상관행렬 점검 결과: {result.get('status')}")
        return result

    logger.info(
        f"📉 분산화 점수 {result['score']:.2f} | 평균|ρ| {result['avg_abs_correlation']:.2f} | "
        f"고상관 쌍 {result['high_corr_pair_count']}개 ({result['evaluated_tickers']}종목)"
    )
    if result["high_corr_pair_count"] >= DEFAULT_ALERT_PAIRS or result["score"] < 0.4:
        await _notify(result)
    return result


async def _main(argv: Optional[Sequence[str]] = None) -> int:
    import sys

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    parser = argparse.ArgumentParser(description="상관행렬 기반 집중도 리스크 점검")
    parser.add_argument("--window", type=int, default=DEFAULT_WINDOW)
    parser.add_argument("--tickers", type=int, default=DEFAULT_MAX_TICKERS)
    args = parser.parse_args(list(argv) if argv is not None else None)

    from data.db_manager import DatabaseManager

    db = DatabaseManager()
    try:
        r = await compute_correlation_report(db, window=args.window, max_tickers=args.tickers)
    finally:
        await db.close()

    if r.get("status") != "ok":
        print(f"점검 불가: {r.get('status')} ({r})")
        return 1
    print(f"분산화 점수: {r['score']:.3f} | 평균|ρ|: {r['avg_abs_correlation']:.3f}")
    print(f"평가 {r['evaluated_tickers']}종목 | 고상관 쌍 {r['high_corr_pair_count']}개 (기준 |ρ|≥0.8)")
    for p in r["top_pairs"][:8]:
        a, b = p.get("ticker_a", "?"), p.get("ticker_b", "?")
        print(f"  {a} ↔ {b}: ρ {float(p.get('correlation', 0)):+.3f}")
    print(f"권고: {r['recommendation']}")
    return 0


if __name__ == "__main__":
    import asyncio

    raise SystemExit(asyncio.run(_main()))
