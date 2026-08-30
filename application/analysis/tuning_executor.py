# -*- coding: utf-8 -*-
"""
application/analysis/tuning_executor.py - 주간 하이퍼파라미터 자동 튜닝 실행기 v1.0

DB의 최근 결정/결과를 조회해 HyperparameterTuner를 실행하고,
결과를 SignalPipeline에 즉시 반영한 뒤 Telegram으로 리포트합니다.
"""

import asyncio
from datetime import datetime, timedelta
from typing import Optional, Dict, List

from core.logger import setup_logger
from data.db_manager import DatabaseManager
from report.telegram_sender import TelegramSender
from application.analysis.hyperparameter_tuner import HyperparameterTuner, HistoricalSample
from application.analysis.signal_pipeline import SignalPipeline

logger = setup_logger("tuning_executor")

# DB의 raw action 문자열 → 튜너 시뮬레이션용 BUY/SELL/HOLD 매핑
_ACTION_MAP = {
    "SIGNAL_ENTRY": "BUY",
    "EVENT_EXIT": "SELL",
    "EVENT_TP_HIT": "SELL",
}
_MIN_SAMPLES = 20


class TuningExecutor:
    """주 1회 하이퍼파라미터 자동 튜닝 실행기."""

    def __init__(
        self,
        db_manager: DatabaseManager,
        pipeline: SignalPipeline,
        telegram: Optional[TelegramSender] = None,
        n_trials: int = 50,
    ) -> None:
        self.db = db_manager
        self.pipeline = pipeline
        self.telegram = telegram or TelegramSender()
        self.tuner = HyperparameterTuner(
            n_trials=n_trials, study_name="v10_weekly_tuning", seed=42
        )

    async def _build_samples(self, days: int) -> List[HistoricalSample]:
        """DB에서 최근 N일 결정+outcome을 조회해 HistoricalSample로 변환."""
        end = datetime.now()
        start = end - timedelta(days=days)
        decisions = await self.db.get_decisions_by_date_range(
            start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
        )

        samples: List[HistoricalSample] = []
        for d in decisions:
            dec_id = d.get("id")
            if dec_id is None:
                continue
            outcome = await self.db.get_outcome(dec_id)
            if not outcome or outcome.get("return_1d") is None:
                continue

            raw_action = d.get("action", "HOLD")
            samples.append(
                HistoricalSample(
                    action=_ACTION_MAP.get(raw_action, "HOLD"),
                    actual_return=float(outcome["return_1d"]),
                    sqi=float(d.get("sqi", 0.5)),
                    confidence=float(d.get("confidence", 0.5)),
                    score=float(d.get("score", 0.5)),
                    buy_threshold=self.pipeline.buy_threshold,
                    sell_threshold=self.pipeline.sell_threshold,
                )
            )
        return samples

    async def run(self, days: int = 30) -> Optional[Dict[str, float]]:
        """튜닝 실행 → 파이프라인 반영 → Telegram 리포트.

        Returns:
            dict: 적용된 하이퍼파라미터 (건너뛰거나 실패 시 None)
        """
        logger.info("하이퍼파라미터 튜닝 시작 (최근 %d일)", days)
        samples = await self._build_samples(days)

        if len(samples) < _MIN_SAMPLES:
            msg = (
                f"⚠️ <b>하이퍼파라미터 튜닝 건너뜀</b>\n"
                f"샘플 부족: {len(samples)}건 (최소 {_MIN_SAMPLES}건 필요)\n"
                f"다음 주 자동 재시도 예정"
            )
            await self.telegram.send_raw(msg)
            logger.warning("튜닝 샘플 부족 (%d건) — 건너뜀", len(samples))
            return None

        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(None, self.tuner.optimize, samples)
        except Exception as e:
            logger.error("튜닝 실행 실패: %s", e, exc_info=True)
            await self.telegram.send_raw(
                f"❌ <b>하이퍼파라미터 튜닝 실패</b>\n오류: {str(e)[:200]}\n다음 주 재시도 예정"
            )
            return None

        applied = self.tuner.apply_to_pipeline(self.pipeline, result)

        msg = (
            f"🔧 <b>하이퍼파라미터 튜닝 완료</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"• 샘플 수: <b>{len(samples)}건</b> (최근 {days}일)\n"
            f"• Trial 수: <b>{result.n_trials}회</b>, 소요: {result.elapsed_sec:.1f}초\n"
            f"• 목적함수 최적값: <code>{result.best_value:.4f}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"• BUY/SELL 임계값: <code>{applied['buy_threshold']:.3f}</code> / "
            f"<code>{applied['sell_threshold']:.3f}</code>\n"
            f"• 최소 신뢰도: <code>{applied['min_confidence']:.3f}</code>\n"
            f"• Trend/Reversal/Breakout 가중치: "
            f"<code>{applied.get('trend_weight', 0):.3f}</code> / "
            f"<code>{applied.get('reversal_weight', 0):.3f}</code> / "
            f"<code>{applied.get('breakout_weight', 0):.3f}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"<i>🕒 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i>"
        )
        await self.telegram.send_raw(msg)
        logger.info("튜닝 완료 및 파이프라인 반영: %s", applied)
        return applied
