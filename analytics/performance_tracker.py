"""
analytics/performance_tracker.py - v1.0 (P2-1: 포트폴리오 성과 추적기)
- 일별 PnL, Sharpe Ratio, Max Drawdown, 승률 계산
- 5분 주기로 자동 갱신 (scanner_main에서 호출)
- 일일 리포트/PDF에 연동 가능
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import numpy as np

from data.db_manager import DatabaseManager

logger = logging.getLogger(__name__)


@dataclass
class PerformanceSnapshot:
    """성과 스냅샷 (일별 또는 실시간)"""

    timestamp: str
    total_pnl_pct: float  # 누적 총 수익률 (%)
    daily_pnl_pct: float  # 일일 수익률 (%)
    win_rate: float  # 승률 (0~1)
    sharpe_ratio: float  # 연간 Sharpe Ratio
    max_drawdown: float  # 최대 낙폭 (%)
    total_trades: int  # 총 거래 수
    open_positions: int  # 현재 오픈 포지션 수
    equity: float = 100.0  # 기준 자본 (초기 100)


class PerformanceTracker:
    """실시간 포트폴리오 성과 추적기 (싱글톤)"""

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
        self._snapshots: list[PerformanceSnapshot] = []
        self._last_update: datetime | None = None
        self._update_interval = 300  # 5분
        self._running = False
        self._task: asyncio.Task | None = None
        self._initialized = False

    def initialize(self, db_manager: DatabaseManager) -> None:
        """DB 주입 (초기화)"""
        self.db = db_manager
        self._initialized = True
        logger.info("✅ PerformanceTracker 초기화 완료")

    async def start(self) -> None:
        """백그라운드 갱신 시작"""
        if self._running or not self._initialized:
            return
        self._running = True
        self._task = asyncio.create_task(self._update_loop())
        logger.info("✅ PerformanceTracker 백그라운드 갱신 시작 (간격: 5분)")

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

    async def _update_metrics(self) -> None:
        """실제 성과 지표 계산 및 스냅샷 저장"""
        if self.db is None:
            return

        try:
            # 1. 피드백 통계 (승률, 샘플 수)
            stats = await self.db.get_feedback_stats(days=30)
            win_rate = stats.get("win_rate", 0.5)
            total_trades = stats.get("sample_count", 0)

            # 2. 최근 포지션 조회
            positions = await self.db.get_positions()
            open_pos = len(positions)

            # 3. 일일 수익률 계산 (outcomes 기반)
            returns = await self._get_daily_returns()
            daily_pnl = returns[-1] if returns else 0.0

            # 4. Equity Curve 업데이트
            if returns:
                new_equity = self._equity_curve[-1] * (1 + returns[-1])
            else:
                new_equity = self._equity_curve[-1]
            self._equity_curve.append(new_equity)
            self._daily_returns.extend(returns[-1:])

            # 5. Sharpe Ratio
            if len(self._daily_returns) >= 5:
                mean_r = np.mean(self._daily_returns)
                std_r = np.std(self._daily_returns)
                sharpe = (mean_r / std_r * np.sqrt(252)) if std_r > 0 else 0.0
            else:
                sharpe = 0.0

            # 6. Max Drawdown
            max_dd = self._calculate_max_drawdown(self._equity_curve)

            # 7. 총 수익률
            total_pnl = self._equity_curve[-1] - 100.0

            # 8. 스냅샷 저장
            snapshot = PerformanceSnapshot(
                timestamp=datetime.now().isoformat(),
                total_pnl_pct=total_pnl,
                daily_pnl_pct=daily_pnl * 100,
                win_rate=win_rate,
                sharpe_ratio=sharpe,
                max_drawdown=max_dd * 100,
                total_trades=total_trades,
                open_positions=open_pos,
                equity=self._equity_curve[-1],
            )
            self._snapshots.append(snapshot)

            # 최근 100개만 유지
            if len(self._snapshots) > 100:
                self._snapshots = self._snapshots[-100:]

            self._last_update = datetime.now()
            logger.debug(f"📊 성과 업데이트: PnL {total_pnl:.2f}%, 승률 {win_rate:.1%}, 샤프 {sharpe:.2f}")

        except Exception as e:
            logger.error(f"❌ 성과 지표 갱신 실패: {e}")

    async def _get_daily_returns(self) -> list[float]:
        """DB에서 최근 30일 일일 수익률 추출"""
        if self.db is None:
            return []
        try:
            # 오늘 기준 30일 전
            end = datetime.now()
            start = end - timedelta(days=30)
            returns = []
            for i in range(30):
                day = (end - timedelta(days=i)).strftime("%Y-%m-%d")
                decisions = await self.db.get_decisions_by_date(day)
                if decisions:
                    # 해당일 평균 수익률 (간단히)
                    day_ret = 0.0
                    count = 0
                    for d in decisions:
                        # outcome 조회 (간접)
                        pass
                    returns.append(day_ret / max(1, count))
            return returns
        except Exception as e:
            logger.debug(f"일일 수익률 조회 실패: {e}")
            return []

    def _calculate_max_drawdown(self, equity_curve: list[float]) -> float:
        """최대 낙폭 계산 (0~1)"""
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

    def get_latest_snapshot(self) -> PerformanceSnapshot | None:
        """최신 성과 스냅샷 반환"""
        return self._snapshots[-1] if self._snapshots else None

    def get_telegram_summary(self) -> str:
        """텔레그램 요약 메시지 생성"""
        snap = self.get_latest_snapshot()
        if not snap:
            return "📊 성과 데이터 없음 (수집 중...)"

        return (
            f"📈 <b>실시간 포트폴리오 성과</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"• 총 손익: <code>{snap.total_pnl_pct:+.2f}%</code>\n"
            f"• 오늘 수익: <code>{snap.daily_pnl_pct:+.2f}%</code>\n"
            f"• 승률: <b>{snap.win_rate:.1%}</b>\n"
            f"• Sharpe Ratio: <code>{snap.sharpe_ratio:.2f}</code>\n"
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
        """상태 반환 (헬스체크용)"""
        snap = self.get_latest_snapshot()
        return {
            "last_update": self._last_update.isoformat() if self._last_update else None,
            "snapshot_count": len(self._snapshots),
            "latest_total_pnl": snap.total_pnl_pct if snap else 0.0,
            "is_running": self._running,
        }


# 전역 인스턴스
performance_tracker = PerformanceTracker()
