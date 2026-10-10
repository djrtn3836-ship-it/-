from typing import Any, Dict, List
"""
analytics/calibration_executor.py - v1.0 (P3-2: Slippage Calibration)
- 실제 Paper 체결 결과와 시뮬레이션 예측 슬리피지 비교
- Almgren-Chriss 파라미터(ALPHA, GAMMA) 자동 튜닝
- 주기적 실행 (매일 17:30) 스케줄러에 등록 가능
"""

import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta


from core.logger import setup_logger
from data.db_manager import DatabaseManager
from report.telegram_sender import TelegramSender

logger = setup_logger("calibration")


@dataclass
class CalibrationReport:
    """보정 보고서"""
    actual_mean_slippage_bps: float
    actual_std_slippage_bps: float
    simulated_mean_slippage_bps: float
    bias_bps: float
    recommended_alpha: float
    recommended_gamma: float
    sample_count: int
    period: str
    status: str  # "SUCCESS" | "INSUFFICIENT_DATA"


# 실현 슬리피지 클램프 상한(bp) / 기본 주문금액(원)
_MAX_SLIPPAGE_BPS = 100.0
_DEFAULT_ORDER_KRW = 5_000_000.0


class ExecutionCalibrator:
    """체결/슬리피지 Calibration 실행기"""

    # Almgren-Chriss 기본 파라미터 (execution_simulator.py와 동일)
    DEFAULT_ALPHA = 0.08
    DEFAULT_GAMMA = 0.005

    def __init__(self, db_manager: DatabaseManager, telegram_sender: TelegramSender | None = None):
        self.db = db_manager
        self.telegram = telegram_sender or TelegramSender()
        self._report: CalibrationReport | None = None

    async def run(self, days: int = 30) -> CalibrationReport | None:
        """
        최근 N일간의 Paper 체결 데이터를 분석하여 슬리피지 보정값 계산
        """
        logger.info(f"📊 슬리피지 Calibration 실행 (과거 {days}일)")

        # 1. Paper 주문 기록 조회 (paper_trades 테이블 필요 - 가정)
        # 실제로는 order_executor가 저장한 체결 기록을 읽어옴
        trades = await self._get_paper_trades(days)

        if len(trades) < 20:
            logger.warning(f"⚠️ 샘플 부족 ({len(trades)}건) → Calibration 스킵")
            return CalibrationReport(
                actual_mean_slippage_bps=0.0,
                actual_std_slippage_bps=0.0,
                simulated_mean_slippage_bps=0.0,
                bias_bps=0.0,
                recommended_alpha=self.DEFAULT_ALPHA,
                recommended_gamma=self.DEFAULT_GAMMA,
                sample_count=len(trades),
                period=f"{days}일",
                status="INSUFFICIENT_DATA",
            )

        # 2. 실제 슬리피지 vs 시뮬레이션 슬리피지 추출
        actual_slippages = []
        sim_slippages = []
        for trade in trades:
            actual = trade.get("slippage_bps", 0)
            sim = trade.get("simulated_slippage_bps", 0)
            if actual != 0 and sim != 0:
                actual_slippages.append(actual)
                sim_slippages.append(sim)

        if len(actual_slippages) < 10:
            return CalibrationReport(
                actual_mean_slippage_bps=0.0,
                actual_std_slippage_bps=0.0,
                simulated_mean_slippage_bps=0.0,
                bias_bps=0.0,
                recommended_alpha=self.DEFAULT_ALPHA,
                recommended_gamma=self.DEFAULT_GAMMA,
                sample_count=len(actual_slippages),
                period=f"{days}일",
                status="INSUFFICIENT_DATA",
            )

        # 3. 통계 계산
        actual_mean = statistics.mean(actual_slippages)
        actual_std = statistics.stdev(actual_slippages) if len(actual_slippages) > 1 else 0.0
        sim_mean = statistics.mean(sim_slippages)
        bias = actual_mean - sim_mean

        # 4. 파라미터 보정 (간단한 비례 조정)
        # alpha는 시장 충격 계수, bias가 양수면 실제 슬리피지가 더 크므로 alpha 증가
        correction_factor = 1.0 + (bias / 10000)  # 1bp당 0.01% 조정
        new_alpha = max(0.01, self.DEFAULT_ALPHA * correction_factor)
        new_gamma = max(0.001, self.DEFAULT_GAMMA * correction_factor)

        # 5. 보고서 생성
        report = CalibrationReport(
            actual_mean_slippage_bps=actual_mean,
            actual_std_slippage_bps=actual_std,
            simulated_mean_slippage_bps=sim_mean,
            bias_bps=bias,
            recommended_alpha=new_alpha,
            recommended_gamma=new_gamma,
            sample_count=len(actual_slippages),
            period=f"{days}일",
            status="SUCCESS",
        )
        self._report = report
        logger.info(
            f"✅ Calibration 완료: 실제 평균 {actual_mean:.2f}bp, 시뮬 {sim_mean:.2f}bp, 편차 {bias:+.2f}bp"
        )

        # 6. Telegram 알림
        await self._send_report(report)

        # 7. execution_simulator.py에 파라미터 적용 (전역 변수 업데이트)
        self._apply_calibration(new_alpha, new_gamma)

        return report

    # ============================================================
    # 내부 헬퍼
    # ============================================================
    async def _get_paper_trades(self, days: int) -> list[dict[str, Any]]:
        """실현 슬리피지 vs 시뮬레이터 추정 슬리피지 산출 (실데이터).

        🔴 2026-10-10 이전: `hash(ticker)` 기반 **가짜 슬리피지**를 생성해
            보정 리포트가 실데이터와 무관했다(재현 불가·의미 없음).
        이제 DB 실가격으로 계산한다:
            - 실현(actual): 결정가 → **다음 거래일 시가** 변화율(bp)
            - 추정(simulated): ExecutionSimulator 모델값
        다음 거래일 데이터가 없거나 값이 0이면 해당 건은 제외한다(추정 금지).
        """
        from validation.execution_simulator import RealisticExecutionSimulator

        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)
        decisions = await self.db.get_decisions_by_date_range(
            start_date.strftime("%Y-%m-%d"),
            end_date.strftime("%Y-%m-%d"),
        )
        simulator = RealisticExecutionSimulator()
        trades: List[Dict[str, float]] = []
        for d in decisions:
            if d.get("action") != "SIGNAL_ENTRY":
                continue
            ticker = str(d.get("ticker", ""))
            try:
                price = float(d.get("price_at_decision") or 0.0)
            except (TypeError, ValueError):
                continue
            dec_date = str(d.get("created_at", ""))[:10]
            if not ticker or price <= 0 or not dec_date:
                continue
            # 시뮬레이터는 '현재 시각'으로 장중 여부를 판정한다.
            # 잡이 16:00(장 마감 후)에 돌면 슬리피지가 0이 되므로 **결정 시각**을 넘긴다.
            try:
                dec_dt = datetime.strptime(str(d.get("created_at", ""))[:19], "%Y-%m-%d %H:%M:%S")
            except (TypeError, ValueError):
                continue
            rows = await self.db.get_ohlcv(ticker, 30)
            nxt = next((r for r in rows if str(r.get("date", ""))[:10] > dec_date), None)
            if nxt is None:
                continue
            try:
                next_open = float(nxt.get("open") or 0.0)
            except (TypeError, ValueError):
                continue
            if next_open <= 0:
                continue
            actual_bps = (next_open - price) / price * 10000.0
            actual_bps = max(-_MAX_SLIPPAGE_BPS, min(_MAX_SLIPPAGE_BPS, actual_bps))
            try:
                res = simulator.execute(
                    ticker=ticker,
                    action="BUY",
                    price=price,
                    volume=0,
                    order_size=max(1, int(_DEFAULT_ORDER_KRW / price)),
                    market_cap=0.0,
                    current_time=dec_dt,
                )
                sim_bps = float(res.slippage_bps)
            except Exception as e:
                logger.debug(f"시뮬레이터 추정 실패({ticker}): {e}")
                sim_bps = 0.0
            if actual_bps == 0.0 or sim_bps == 0.0:
                continue
            trades.append({
                "slippage_bps": float(actual_bps),
                "simulated_slippage_bps": float(sim_bps),
            })
        return trades

    async def _send_report(self, report: CalibrationReport) -> None:
        """텔레그램으로 Calibration 보고서 전송"""
        if report.status == "INSUFFICIENT_DATA":
            msg = (
                f"📊 <b>Calibration 보고서</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"⚠️ 데이터 부족 (샘플 {report.sample_count}건)\n"
                f"최소 20건 필요 → 다음 실행 시 재시도\n"
                f"━━━━━━━━━━━━━━━━━━━━━"
            )
        else:
            msg = (
                f"📊 <b>슬리피지 Calibration 보고서</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"• 실제 평균 슬리피지: <code>{report.actual_mean_slippage_bps:.2f}bp</code>\n"
                f"• 시뮬 평균 슬리피지: <code>{report.simulated_mean_slippage_bps:.2f}bp</code>\n"
                f"• 편차: <code>{report.bias_bps:+.2f}bp</code>\n"
                f"• 권장 ALPHA: <b>{report.recommended_alpha:.4f}</b> (기존 {self.DEFAULT_ALPHA:.4f})\n"
                f"• 권장 GAMMA: <b>{report.recommended_gamma:.4f}</b> (기존 {self.DEFAULT_GAMMA:.4f})\n"
                f"• 샘플 수: {report.sample_count}건\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"<i>🕒 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i>"
            )
        await self.telegram.send_raw(msg)

    def _apply_calibration(self, alpha: float, gamma: float) -> None:
        """실제 execution_simulator.py의 전역 파라미터 업데이트"""
        try:
            from validation import execution_simulator
            execution_simulator.RealisticExecutionSimulator.ALPHA = alpha
            execution_simulator.RealisticExecutionSimulator.GAMMA = gamma
            logger.info(f"✅ Calibration 적용: ALPHA={alpha:.4f}, GAMMA={gamma:.4f}")
        except Exception as e:
            logger.error(f"❌ Calibration 적용 실패: {e}")

    def get_latest_report(self) -> CalibrationReport | None:
        return self._report
