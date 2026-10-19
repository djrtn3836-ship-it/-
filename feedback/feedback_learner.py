# -*- coding: utf-8 -*-
"""
feedback/feedback_learner.py - v7.4.3 (S134: 캐치업 정산 · 결정일 고정 가격)
- 모든 메서드 반환 타입/제네릭 타입 명시, 로직 100% 무변경
"""

import asyncio
import bisect
import json
import pickle
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.holiday_utils import is_trading_day
from core.logger import setup_logger
from data.db_manager import DatabaseManager
from report.telegram_sender import TelegramSender

logger = setup_logger("feedback")

MODEL_PATH = Path(__file__).parent.parent / "config" / "xgb_model.pkl"

FEATURE_COLS: List[str] = ["momentum", "rsi", "volume_ratio", "macro_score", "sector_score", "imbalance"]

CATCHUP_DAYS = 10  # 캘린더 기준 — 앱 미실행일·연속휴장도 자동 복구 (S134)


class FeedbackLearner:
    def __init__(self, kiwoom_connector: Optional[Any] = None, db_manager: Optional[DatabaseManager] = None) -> None:
        self.db: DatabaseManager = db_manager or DatabaseManager()
        self.connector: Optional[Any] = kiwoom_connector
        self.telegram = TelegramSender()
        self._xgb_model: Optional[Any] = None
        self._model_ready: bool = False
        # 🔴 2026-10-10: 드리프트 감지 배선용(미지정 시 비활성) — bootstrap에서 주입
        self.ops_monitor: Optional[Any] = None
        try:
            import xgboost as xgb
            self._xgb: Optional[Any] = xgb
        except ImportError:
            logger.warning("⚠️ XGBoost 미설치 → ML 예측 비활성화 (pip install xgboost)")
            self._xgb = None

        self.load_model()

    def save_model(self) -> None:
        if self._xgb_model is None:
            return
        try:
            MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(MODEL_PATH, "wb") as f:
                pickle.dump(self._xgb_model, f)
            logger.info(f"✅ XGBoost 모델 저장 완료: {MODEL_PATH}")
        except Exception as e:
            logger.error(f"❌ 모델 저장 실패: {e}")

    def load_model(self) -> None:
        if not MODEL_PATH.exists():
            logger.debug("📭 저장된 ML 모델 없음 (학습 필요)")
            return
        try:
            with open(MODEL_PATH, "rb") as f:
                self._xgb_model = pickle.load(f)
            self._model_ready = True
            logger.info(f"✅ XGBoost 모델 로드 완료: {MODEL_PATH}")
        except Exception as e:
            logger.warning(f"⚠️ 모델 로드 실패: {e}, 재학습 필요")
            self._model_ready = False

    def predict_prob(self, features: Dict[str, Any]) -> float:
        if not self._model_ready or self._xgb_model is None:
            return 0.5

        try:
            import pandas as pd
            df = pd.DataFrame([{k: float(features.get(k, 0.0)) for k in FEATURE_COLS}])
            prob = self._xgb_model.predict_proba(df)[0][1]
            return float(prob)
        except Exception as e:
            logger.debug(f"⚠️ ML 예측 실패: {e}")
            return 0.5

    async def run(self) -> None:
        """미확정 신호 캐치업 정산 (S134).

        기존: '어제 하루'만 처리 → 앱이 하루 꺼지면 그날 outcomes 영구 누락(실측:
        10-14·10-16 배치 누락). 신규: 최근 CATCHUP_DAYS일 내 **미확정 전부**
        (결과 없음 / return_5d 미성숙 / 성숙 전 저장분)를 매일 재정산 → 자동 복구.
        """
        logger.info("🧠 [v7.4.3] 피드백 학습 시작 (캐치업 창 · 결정일 고정 가격)")
        now = datetime.now()
        dates: List[str] = []
        for i in range(1, CATCHUP_DAYS + 1):
            d = (now - timedelta(days=i)).date()
            if not is_trading_day(d):
                continue
            dates.append(d.strftime("%Y-%m-%d"))
        if not dates:
            logger.info("📭 최근 거래일 없음 → 학습 스킵")
            return
        start, end = min(dates), max(dates)

        decisions = await self.db.get_unsettled_decisions(start, end)
        if not decisions:
            logger.info(f"📭 미확정 신호 없음 ({start}~{end}) → 학습 스킵")
            return

        logger.info(f"📊 미확정 {len(decisions)}개 신호 성과 분석 중... ({start}~{end})")
        closes_cache: Dict[str, List[Dict[str, Any]]] = {}
        outcomes: List[Dict[str, Any]] = []
        for dec in decisions:
            outcome = await self._fetch_real_outcome(dec, closes_cache)
            if outcome and float(outcome.get("price_after_1d", 0.0) or 0.0) > 0:
                await self.db.save_outcome(outcome)
                outcomes.append(outcome)

        if not outcomes:
            logger.warning("⚠️ 유효한 결과 없음")
            return

        # 🔴 2026-10-10: 결과를 관측기에 공급 → 모델 드리프트 감지 배선
        #   (이전에는 record_outcome이 프로덕션에서 호출되지 않아 드리프트가 꺼져 있었다)
        if self.ops_monitor is not None:
            for _o in outcomes:
                try:
                    self.ops_monitor.record_outcome(
                        str(_o.get("strategy_name") or _o.get("strategy") or "ensemble"),
                        float(_o.get("confidence", 0.5) or 0.5),
                        bool(_o.get("is_correct")),
                    )
                except Exception:
                    pass

        stats = await self._generate_stats(outcomes)

        if self._xgb and len(outcomes) >= 30:
            await self._train_xgboost_model_async(outcomes)
            self.save_model()

        prev_weights = await self.db.get_weights()
        new_weights = await self._update_weights_advanced(outcomes, stats, prev_weights)

        await self._send_advanced_report(f"{start}~{end}", stats, prev_weights, new_weights)

    async def _fetch_real_outcome(
        self, decision: Dict[str, Any], closes_cache: Dict[str, List[Dict[str, Any]]]
    ) -> Optional[Dict[str, Any]]:
        """결정일 기준 고정 가격으로 성과 계산 (S134).

        - 1일차 = 결정일 봉 '다음 봉' 종가, 5일차 = 결정일 +5봉 종가 (날짜 고정).
        - 5일차가 아직 없으면 return_5d=None(미성숙) → 캐치업이 나중에 재정산.
        기존 버그: ohlcv 최신 종가를 1d·5d 모두에 사용 → 5d가 1d 복제(오염),
        그리고 캐치업 시점엔 '최신 종가'가 결정일과 무관해짐.
        """
        ticker = str(decision["ticker"])
        action = str(decision["action"])
        price_at = float(decision.get("price_at_decision", decision.get("price", 0.0)) or 0.0)
        if price_at <= 0:
            return None

        ddate = str(decision.get("created_at") or "")[:10]
        if len(ddate) != 10:
            return None

        rows = closes_cache.get(ticker)
        if rows is None:
            try:
                rows = await self.db.get_ohlcv(ticker, period=60)
            except Exception as e:
                logger.error(f"❌ 가격 조회 실패 ({ticker}): {e}")
                rows = []
            closes_cache[ticker] = rows
        if not rows:
            return None

        dates = [str(r.get("date")) for r in rows]
        closes = [float(r.get("close") or 0.0) for r in rows]
        i = bisect.bisect_right(dates, ddate) - 1  # 결정일(포함) 마지막 봉
        if i < 0 or i + 1 >= len(closes) or closes[i + 1] <= 0:
            return None  # 1일차 봉 아직 없음 → 다음 캐치업에서 재시도
        price_after_1d = closes[i + 1]
        price_after_5d = closes[i + 5] if i + 5 < len(closes) and closes[i + 5] > 0 else None

        return_1d = (price_after_1d - price_at) / price_at
        return_5d = (price_after_5d - price_at) / price_at if price_after_5d else None

        is_correct: bool
        if action == "BUY":
            is_correct = return_1d > 0
        elif action == "SELL":
            is_correct = return_1d < 0
        else:
            is_correct = abs(return_1d) < 0.02

        features: Dict[str, Any] = {}
        try:
            strategy_json = decision.get("strategy_scores")
            if strategy_json:
                parsed = json.loads(strategy_json)
                if isinstance(parsed, dict):
                    features = parsed.get("features", {})
        except Exception:
            pass

        return {
            "decision_id": int(decision["id"]),
            "ticker": ticker,
            "action": action,
            "entry_price": price_at,
            "price_after_1d": price_after_1d,
            "price_after_5d": price_after_5d,
            "return_1d": return_1d * 100,
            "return_5d": (return_5d * 100) if return_5d is not None else None,
            "is_correct": is_correct,
            "features": features,
        }

    async def _train_xgboost_model_async(self, outcomes: List[Dict[str, Any]]) -> None:
        try:
            end_date = datetime.now()
            all_decisions: List[Dict[str, Any]] = []
            for i in range(30):
                day = (end_date - timedelta(days=i)).strftime("%Y-%m-%d")
                all_decisions.extend(await self.db.get_decisions_by_date(day))

            if len(all_decisions) < 30:
                logger.info(f"📊 학습 데이터 부족 ({len(all_decisions)}개) → XGBoost 스킵")
                return

            import pandas as pd

            outcome_map = {int(o["decision_id"]): o for o in outcomes}

            X_list: List[List[float]] = []
            y_list: List[int] = []

            for dec in all_decisions:
                dec_id = dec.get("id")
                if dec_id is None:
                    continue
                outcome = outcome_map.get(int(dec_id))
                if not outcome or outcome.get("is_correct") is None:
                    continue

                features: Dict[str, Any] = {}
                try:
                    strategy_json = dec.get("strategy_scores")
                    if strategy_json:
                        parsed = json.loads(strategy_json)
                        if isinstance(parsed, dict):
                            features = parsed.get("features", {})
                except Exception:
                    pass

                row = [float(features.get(k, 0.0)) for k in FEATURE_COLS]
                X_list.append(row)
                y_list.append(1 if outcome["is_correct"] else 0)

            if len(X_list) < 20:
                logger.info(f"📊 유효 학습 샘플 부족 ({len(X_list)}개) → XGBoost 스킵")
                return

            X = pd.DataFrame(X_list, columns=FEATURE_COLS)
            y = pd.Series(y_list)

            loop = asyncio.get_running_loop()
            self._xgb_model = await loop.run_in_executor(None, self._fit_model, X, y)
            self._model_ready = True
            logger.info(f"✅ XGBoost 모델 학습 완료 (샘플: {len(X)}개, 피처: {FEATURE_COLS})")

        except Exception as e:
            logger.error(f"❌ XGBoost 학습 실패: {e}")
            self._model_ready = False

    def _fit_model(self, X: Any, y: Any) -> Any:
        if self._xgb is None:
            return None
        model = self._xgb.XGBClassifier(
            n_estimators=50,
            max_depth=3,
            learning_rate=0.1,
            use_label_encoder=False,
            eval_metric="logloss",
            random_state=42,
        )
        model.fit(X, y)
        return model

    async def _update_weights_advanced(
        self, outcomes: List[Dict[str, Any]], stats: Dict[str, Any], current_weights: Dict[str, float]
    ) -> Dict[str, float]:
        avg_return: float = float(stats.get("avg_return_5d", stats.get("avg_return_1d", 0.0)))
        accuracy: float = float(stats.get("accuracy", 0.5))
        ml_factor = 1.0 + (accuracy - 0.5) * 0.5
        updated: Dict[str, float] = {}
        for factor, current_weight in current_weights.items():
            delta = 0.03 if avg_return > 0 else -0.03
            delta = delta * ml_factor
            new_weight = max(0.1, min(3.0, current_weight + delta))
            await self.db.update_weight(factor, new_weight)
            updated[factor] = new_weight
        logger.info(f"📊 고급 가중치 최적화 완료 (5일 평균 수익률: {avg_return:.2f}%)")
        return updated

    async def _generate_stats(self, outcomes: List[Dict[str, Any]]) -> Dict[str, Any]:
        total = len(outcomes)
        correct = sum(1 for o in outcomes if o.get("is_correct"))
        accuracy = correct / total if total > 0 else 0.0
        returns_1d = [float(o["return_1d"]) for o in outcomes if o.get("return_1d") is not None]
        returns_5d = [float(o["return_5d"]) for o in outcomes if o.get("return_5d") is not None]
        mean_1d = sum(returns_1d) / len(returns_1d) if returns_1d else 0.0
        mean_5d = sum(returns_5d) / len(returns_5d) if returns_5d else mean_1d
        wins = [r for r in returns_1d if r > 0]
        losses = [abs(r) for r in returns_1d if r < 0]
        profit_factor = sum(wins) / sum(losses) if sum(losses) > 0 else (sum(wins) if sum(wins) > 0 else 1.0)
        return {
            "total": total,
            "correct": correct,
            "accuracy": accuracy,
            "avg_return_1d": mean_1d,
            "avg_return_5d": mean_5d,
            "profit_factor": profit_factor,
        }

    async def _send_advanced_report(
        self, date_str: str, stats: Dict[str, Any], prev_w: Dict[str, float], new_w: Dict[str, float]
    ) -> None:
        factor_map = {
            "momentum": "모멘텀",
            "volume": "거래량",
            "volatility": "변동성",
            "macro": "매크로",
            "sector": "섹터",
        }
        drift_lines: List[str] = []
        for f in prev_w.keys():
            old_v = prev_w.get(f, 1.0)
            new_v = new_w.get(f, 1.0)
            diff = new_v - old_v
            arrow = "🔺" if diff > 0 else ("🔻" if diff < 0 else "➖")
            label = factor_map.get(f, f)
            drift_lines.append(f"• <code>{label:<8}</code>: {old_v:.2f} ➔ <b>{new_v:.2f}</b> ({arrow} {diff:+.2f})")
        ml_status = "✅ 활성화" if self._model_ready else "⚠️ 비활성 (데이터 부족)"
        msg = (
            f"<b>🧠 [AI 퀀트] 모델 최적화 보고서 (v7.4.3 캐치업 정산)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>📈 1. 성과 지표 ({date_str})</b>\n"
            f"• 샘플: <b>{stats['total']}개</b> | 적중률: <b>{stats['accuracy']:.1%}</b>\n"
            f"• 1일 평균 수익: <code>{stats['avg_return_1d']:+.2f}%</code> | 5일: <code>{stats['avg_return_5d']:+.2f}%</code>\n"
            f"• 손익비: <code>{stats['profit_factor']:.2f}</code>\n\n"
            f"<b>⚙️ 2. AI 가중치 재조정 (ML 반영)</b>\n" + "\n".join(drift_lines) + "\n"
            f"<b>🧬 3. ML 예측 엔진</b>: {ml_status} (피처: {', '.join(FEATURE_COLS)})\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"<i>📊 XGBoost 하이브리드 | 실시간 점수 융합 (18%)</i>"
        )
        await self.telegram.send_raw(msg)
