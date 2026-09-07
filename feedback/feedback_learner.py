# -*- coding: utf-8 -*-
"""
feedback/feedback_learner.py - v7.4.2 (Session 38: mypy strict 적용)
- 모든 메서드 반환 타입/제네릭 타입 명시, 로직 100% 무변경
"""

import asyncio
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


class FeedbackLearner:
    def __init__(self, kiwoom_connector: Optional[Any] = None, db_manager: Optional[DatabaseManager] = None) -> None:
        self.db: DatabaseManager = db_manager or DatabaseManager()
        self.connector: Optional[Any] = kiwoom_connector
        self.telegram = TelegramSender()
        self._xgb_model: Optional[Any] = None
        self._model_ready: bool = False
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
        logger.info("🧠 [v7.4.2] 피드백 학습 시작 (ML 피처 복원, mypy strict)")
        yesterday = datetime.now() - timedelta(days=1)
        if not is_trading_day(yesterday):
            logger.info(f"📭 {yesterday.strftime('%Y-%m-%d')} 비거래일 → 학습 스킵")
            return

        yesterday_str = yesterday.strftime("%Y-%m-%d")
        decisions = await self.db.get_decisions_by_date(yesterday_str)
        if not decisions:
            logger.info("📭 학습할 데이터 없음")
            return

        logger.info(f"📊 {len(decisions)}개 신호 성과 분석 중...")
        outcomes: List[Dict[str, Any]] = []
        for dec in decisions:
            outcome = await self._fetch_real_outcome(dec)
            if outcome and float(outcome.get("price_after_1d", 0.0)) > 0:
                await self.db.save_outcome(outcome)
                outcomes.append(outcome)

        if not outcomes:
            logger.warning("⚠️ 유효한 결과 없음")
            return

        stats = await self._generate_stats(outcomes)

        if self._xgb and len(outcomes) >= 30:
            await self._train_xgboost_model_async(outcomes)
            self.save_model()

        prev_weights = await self.db.get_weights()
        new_weights = await self._update_weights_advanced(outcomes, stats, prev_weights)

        await self._send_advanced_report(yesterday_str, stats, prev_weights, new_weights)

    async def _fetch_real_outcome(self, decision: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        ticker = str(decision["ticker"])
        action = str(decision["action"])
        price_at = float(decision.get("price_at_decision", decision.get("price", 0.0)))
        if price_at <= 0:
            return None

        features: Dict[str, Any] = {}
        try:
            strategy_json = decision.get("strategy_scores")
            if strategy_json:
                parsed = json.loads(strategy_json)
                if isinstance(parsed, dict):
                    features = parsed.get("features", {})
        except Exception:
            pass

        price_after_1d: float = 0.0
        price_after_5d: float = 0.0
        try:
            ohlcv_1d = await self.db.get_ohlcv(ticker, period=2)
            price_after_1d = float(ohlcv_1d[-1].get("close", 0.0)) if ohlcv_1d and len(ohlcv_1d) >= 2 else 0.0
            ohlcv_5d = await self.db.get_ohlcv(ticker, period=6)
            price_after_5d = float(ohlcv_5d[-1].get("close", 0.0)) if ohlcv_5d and len(ohlcv_5d) >= 6 else 0.0
            if price_after_1d <= 0 or price_after_5d <= 0:
                if self.connector:
                    resp = await self.connector.request_tr(ticker, "일봉")
                    if resp and "close" in resp:
                        price_after_1d = float(resp.get("close", 0.0))
                        price_after_5d = float(resp.get("close", 0.0))
        except Exception as e:
            logger.error(f"❌ 가격 조회 실패 ({ticker}): {e}")
            return None

        if price_after_1d <= 0:
            return None

        return_1d = (price_after_1d - price_at) / price_at
        return_5d = (price_after_5d - price_at) / price_at if price_after_5d > 0 else return_1d

        is_correct: bool
        if action == "BUY":
            is_correct = return_1d > 0
        elif action == "SELL":
            is_correct = return_1d < 0
        else:
            is_correct = abs(return_1d) < 0.02

        return {
            "decision_id": int(decision["id"]),
            "ticker": ticker,
            "action": action,
            "entry_price": price_at,
            "price_after_1d": price_after_1d,
            "price_after_5d": price_after_5d,
            "return_1d": return_1d * 100,
            "return_5d": return_5d * 100,
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
        returns_1d = [float(o["return_1d"]) for o in outcomes if "return_1d" in o]
        returns_5d = [float(o.get("return_5d", o.get("return_1d", 0.0))) for o in outcomes]
        mean_1d = sum(returns_1d) / total if total > 0 else 0.0
        mean_5d = sum(returns_5d) / total if total > 0 else 0.0
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
            f"<b>🧠 [AI 퀀트] 모델 최적화 보고서 (v7.4.2 ML 피처 복원)</b>\n"
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
