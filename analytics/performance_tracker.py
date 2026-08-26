"""
analytics/performance_tracker.py - v3.0 (StrategyBandit 실시간 피드백 연동)

v2.0: outcome 기반 일일 수익률 실제 구현
v3.0 변경사항:
    - BanditFeedbackBridge 연동 훅 추가 (_bandit_bridge)
    - _update_metrics() 완료 후 on_performance_updated() 자동 호출
    - attach_bandit_bridge() / detach_bandit_bridge() 공개 API
    - get_status()에 bandit_weights 포함
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Optional

import numpy as np

from data.db_manager import DatabaseManager
from observability.tracer import get_tracer

if TYPE_CHECKING:
    from application.analysis.bandit_feedback_bridge import BanditFeedbackBridge

logger = logging.getLogger(__name__)
trace = get_tracer(__name__)

# 연간화 계수 (252 거래일)
ANNUALIZATION = 252 ** 0.5


@dataclass
class PerformanceSnapshot:
    """성과 스냅샷 (일별 또는 실시간)"""

    timestamp: str
    total_pnl_pct: float        # 누적 총 수익률 (%)
    daily_pnl_pct: float        # 당일 수익률 (%)
    win_rate: float             # 승률 (0~1)
    sharpe_ratio: float         # 연간 Sharpe Ratio
    max_drawdown: float         # 최대 낙폭 (%)
    total_trades: int           # 총 거래 수
    open_positions: int         # 현재 오픈 포지션 수
    avg_return_pct: float = 0.0 # 평균 수익률 (%)
    equity: float = 100.0       # 기준 자본 (초기 100)
    calmar_ratio: float = 0.0   # Calmar Ratio (연수익/MDD)


@dataclass
class DailyPnL:
    """일별 손익 집계"""
    date: str
    pnl_pct: float      # 수익률
    trade_count: int    # 거래 건수
    win_count: int      # 수익 건수


class PerformanceTracker:
    """실시간 포트폴리오 성과 추적기 (싱글톤)

    v2.0 변경 사항:
    - _get_daily_returns(): 스텁 → DB outcome 기반 실측 계산
    - _get_daily_pnl_by_range(): 날짜 범위 기반 일별 PnL 집계
    - Calmar Ratio 추가 (연수익률 / MDD)
    - 평균 수익률(avg_return_pct) 필드 추가
    """

    _instance = None
    _lock = asyncio.Lock()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init()
        return cls._instance

    def _init(self):
        self.db: DatabaseManager | None = None
        self._equity_curve: list[float] = [100.0]
        self._daily_returns: list[float] = []
        self._daily_pnl_cache: list[DailyPnL] = []
        self._snapshots: list[PerformanceSnapshot] = []
        self._last_update: datetime | None = None
        self._update_interval = 300   # 5분
        self._running = False
        self._task: asyncio.Task | None = None
        self._initialized = False
        # v3.0: StrategyBandit 연동 브리지 (선택적)
        self._bandit_bridge: Optional["BanditFeedbackBridge"] = None

    def initialize(self, db_manager: DatabaseManager) -> None:
        """DB 주입 (초기화)"""
        self.db = db_manager
        self._initialized = True
        logger.info("✅ PerformanceTracker v3.0 초기화 완료")

    def attach_bandit_bridge(
        self, bridge: "BanditFeedbackBridge"
    ) -> None:
        """BanditFeedbackBridge 연결.

        _update_metrics() 완료 후 bridge.on_performance_updated()가
        자동으로 호출됩니다.

        Args:
            bridge: BanditFeedbackBridge 인스턴스
        """
        self._bandit_bridge = bridge
        logger.info("✅ BanditFeedbackBridge 연결 완료")

    def detach_bandit_bridge(self) -> None:
        """BanditFeedbackBridge 연결 해제."""
        self._bandit_bridge = None
        logger.info("🔌 BanditFeedbackBridge 연결 해제")

    @trace.traced
    async def start(self) -> None:
        """백그라운드 갱신 시작"""
        if self._running or not self._initialized:
            return
        self._running = True
        self._task = asyncio.create_task(self._update_loop())
        logger.info("✅ PerformanceTracker 백그라운드 갱신 시작 (간격: 5분)")

    @trace.traced
    async def stop(self) -> None:
        """백그라운드 갱신 중지"""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("🛑 PerformanceTracker 중지됨")

    async def _update_loop(self) -> None:
        """주기적 갱신 루프"""
        await self._update_metrics()
        while self._running:
            await asyncio.sleep(self._update_interval)
            await self._update_metrics()

    @trace.traced
    async def _update_metrics(self) -> None:
        """실제 성과 지표 계산 및 스냅샷 저장"""
        if self.db is None:
            return

        try:
            # 1. 피드백 통계 (승률, 샘플 수, 평균 수익률)
            stats = await self.db.get_feedback_stats(days=30)
            win_rate = stats.get("win_rate", 0.5)
            total_trades = stats.get("sample_count", 0)
            avg_return = stats.get("avg_return", 0.0)   # v2.0: 실측 평균 수익률

            # 2. 현재 포지션 조회
            positions = await self.db.get_positions()
            open_pos = len(positions)

            # 3. 일일 수익률 계산 ← v2.0: 실제 구현
            returns = await self._get_daily_returns()
            daily_pnl = returns[-1] if returns else 0.0

            # 4. Equity Curve 업데이트 (복리)
            if returns:
                new_equity = self._equity_curve[-1] * (1.0 + returns[-1])
            else:
                new_equity = self._equity_curve[-1]
            self._equity_curve.append(new_equity)

            # 최근 252일 (1년) 치만 유지
            if len(self._equity_curve) > 253:
                self._equity_curve = self._equity_curve[-253:]

            # _daily_returns에 신규 수익률 추가
            if returns:
                self._daily_returns.extend(returns[-1:])
            if len(self._daily_returns) > 252:
                self._daily_returns = self._daily_returns[-252:]

            # 5. Sharpe Ratio (최소 5일치 필요)
            sharpe = self._calc_sharpe(self._daily_returns)

            # 6. Max Drawdown
            max_dd = self._calculate_max_drawdown(self._equity_curve)

            # 7. 총 수익률 (기준: 초기 100)
            total_pnl = self._equity_curve[-1] - 100.0

            # 8. Calmar Ratio (연간 수익률 / MDD)
            calmar = self._calc_calmar(total_pnl, max_dd, len(self._equity_curve))

            # 9. 스냅샷 저장
            snapshot = PerformanceSnapshot(
                timestamp=datetime.now().isoformat(),
                total_pnl_pct=round(total_pnl, 4),
                daily_pnl_pct=round(daily_pnl * 100, 4),
                win_rate=round(win_rate, 4),
                sharpe_ratio=round(sharpe, 4),
                max_drawdown=round(max_dd * 100, 4),
                total_trades=total_trades,
                open_positions=open_pos,
                avg_return_pct=round(avg_return * 100, 4),
                equity=round(self._equity_curve[-1], 4),
                calmar_ratio=round(calmar, 4),
            )
            self._snapshots.append(snapshot)

            # 최근 100개만 유지
            if len(self._snapshots) > 100:
                self._snapshots = self._snapshots[-100:]

            self._last_update = datetime.now()
            logger.debug(
                "📊 성과 업데이트: PnL %.2f%%, 승률 %.1f%%, 샤프 %.2f, MDD %.1f%%, Calmar %.2f",
                total_pnl, win_rate * 100, sharpe, max_dd * 100, calmar,
            )

            # v3.0: StrategyBandit 피드백 자동 트리거
            if self._bandit_bridge is not None:
                try:
                    weights = await self._bandit_bridge.on_performance_updated()
                    logger.debug("🎰 Bandit 가중치 갱신: %s",
                                 {k: round(v, 3) for k, v in weights.items()})
                except Exception as be:
                    logger.warning("⚠️ Bandit 피드백 실패 (비치명): %s", be)

        except Exception as e:
            logger.error("❌ 성과 지표 갱신 실패: %s", e, exc_info=True)

    # ──────────────────────────────────────────────────
    # v2.0 핵심: _get_daily_returns() 실제 구현
    # ──────────────────────────────────────────────────
    @trace.traced
    async def _get_daily_returns(self) -> list[float]:
        """DB decision_outcomes에서 최근 30일 일별 수익률 계산.

        로직:
        1. get_decisions_by_date_range()로 최근 30일 결정 목록 조회
        2. 각 결정의 outcome (return_1d) 합산 → 일별 평균 수익률
        3. outcome이 없는 날은 0.0으로 처리 (미결산)

        Returns:
            float 리스트 (오래된 날 → 최근 날 순, 소수 형식 0.01 = 1%)
        """
        if self.db is None:
            return []
        try:
            end = datetime.now()
            start = end - timedelta(days=30)
            start_str = start.strftime("%Y-%m-%d")
            end_str = end.strftime("%Y-%m-%d")

            # DB에서 30일 결정 + outcome 조회
            decisions = await self.db.get_decisions_by_date_range(start_str, end_str)
            if not decisions:
                return []

            # 날짜별로 집계
            daily: dict[str, list[float]] = {}
            for d in decisions:
                date_key = d.get("created_at", "")[:10]
                if not date_key:
                    continue

                # outcome 조회 (get_outcome은 decision_id 필요)
                dec_id = d.get("id")
                if dec_id is not None:
                    outcome = await self.db.get_outcome(dec_id)
                    if outcome and outcome.get("return_1d") is not None:
                        ret = float(outcome["return_1d"])
                        daily.setdefault(date_key, []).append(ret)

            # 날짜 정렬 후 일별 평균 수익률 리스트 생성
            sorted_dates = sorted(daily.keys())
            returns = [
                sum(daily[d]) / len(daily[d])
                for d in sorted_dates
                if daily[d]
            ]
            return returns

        except Exception as e:
            logger.debug("일일 수익률 조회 실패: %s", e)
            return []

    # ──────────────────────────────────────────────────
    # 지표 계산 헬퍼
    # ──────────────────────────────────────────────────
    @staticmethod
    def _calc_sharpe(returns: list[float], risk_free: float = 0.0) -> float:
        """연간 Sharpe Ratio.
        
        Args:
            returns: 일별 수익률 리스트 (최소 5개 필요)
            risk_free: 무위험 수익률 (기본 0)
        """
        if len(returns) < 5:
            return 0.0
        arr = np.array(returns, dtype=float)
        mean_r = float(np.mean(arr)) - risk_free / ANNUALIZATION
        std_r = float(np.std(arr, ddof=1))
        return (mean_r / std_r * ANNUALIZATION) if std_r > 1e-9 else 0.0

    @staticmethod
    def _calc_calmar(total_pnl_pct: float, max_dd: float, days: int) -> float:
        """Calmar Ratio = 연간 수익률 / MDD.

        Args:
            total_pnl_pct: 누적 수익률 % (e.g. 15.0 → 15%)
            max_dd: 최대 낙폭 (0~1)
            days: 운용 기간 (일)
        """
        if max_dd < 1e-6 or days < 1:
            return 0.0
        annual_return = (total_pnl_pct / 100) * (252 / max(days, 1))
        return annual_return / max_dd

    @staticmethod
    def _calculate_max_drawdown(equity_curve: list[float]) -> float:
        """최대 낙폭 계산 (0~1 범위).

        Args:
            equity_curve: 자본 곡선 (기준 100)

        Returns:
            최대 낙폭 비율 (0.0 ~ 1.0)
        """
        if not equity_curve:
            return 0.0
        peak = equity_curve[0]
        max_dd = 0.0
        for value in equity_curve:
            if value > peak:
                peak = value
            dd = (peak - value) / peak if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd
        return max_dd

    # ──────────────────────────────────────────────────
    # 공개 인터페이스
    # ──────────────────────────────────────────────────
    def get_latest_snapshot(self) -> PerformanceSnapshot | None:
        """최신 성과 스냅샷 반환"""
        return self._snapshots[-1] if self._snapshots else None

    def get_telegram_summary(self) -> str:
        """텔레그램 성과 요약 메시지 (HTML 형식)"""
        snap = self.get_latest_snapshot()
        if not snap:
            return "📊 성과 데이터 없음 (수집 중...)"

        pnl_sign = "📈" if snap.total_pnl_pct >= 0 else "📉"
        return (
            f"{pnl_sign} <b>실시간 포트폴리오 성과</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"• 총 손익: <code>{snap.total_pnl_pct:+.2f}%</code>\n"
            f"• 오늘 수익: <code>{snap.daily_pnl_pct:+.2f}%</code>\n"
            f"• 평균 수익률: <code>{snap.avg_return_pct:+.2f}%</code>\n"
            f"• 승률: <b>{snap.win_rate:.1%}</b>\n"
            f"• Sharpe Ratio: <code>{snap.sharpe_ratio:.2f}</code>\n"
            f"• Calmar Ratio: <code>{snap.calmar_ratio:.2f}</code>\n"
            f"• MDD: <code>{snap.max_drawdown:.1f}%</code>\n"
            f"• 거래 수: {snap.total_trades}건\n"
            f"• 오픈 포지션: {snap.open_positions}개\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"<i>🕒 {snap.timestamp[:16]}</i>"
        )

    def get_equity_curve(self) -> list[float]:
        """자본 곡선 반환 (백테스트/차트용)"""
        return self._equity_curve.copy()

    def get_status(self) -> dict[str, Any]:
        """헬스체크용 상태 반환 (v3.0: Bandit 가중치 포함)"""
        snap = self.get_latest_snapshot()
        status: dict[str, Any] = {
            "last_update": self._last_update.isoformat() if self._last_update else None,
            "snapshot_count": len(self._snapshots),
            "equity_points": len(self._equity_curve),
            "daily_return_points": len(self._daily_returns),
            "latest_total_pnl": snap.total_pnl_pct if snap else 0.0,
            "latest_sharpe": snap.sharpe_ratio if snap else 0.0,
            "latest_mdd": snap.max_drawdown if snap else 0.0,
            "is_running": self._running,
        }
        if self._bandit_bridge is not None:
            status["bandit_weights"] = self._bandit_bridge.get_current_weights()
            status["bandit_next_feedback_sec"] = self._bandit_bridge.next_feedback_in_seconds()
        return status


# 전역 인스턴스
performance_tracker = PerformanceTracker()
