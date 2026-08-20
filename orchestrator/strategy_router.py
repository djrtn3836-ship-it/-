"""
orchestrator/strategy_router.py - v1.1.1 (캐시 키 확장)
- _get_cache_key()에 tech_data의 주요 값(RSI, EMA5, EMA20, volume_ratio) 포함
- 동일 가격/거래량이라도 기술 지표가 다르면 캐시 미스 처리
"""

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from core.logger import setup_logger
from strategy.base_strategy import BaseStrategy
from strategy.breakout_strategy import BreakoutStrategy
from strategy.reversal_strategy import ReversalStrategy
from strategy.trend_strategy import TrendStrategy

logger = setup_logger("strategy_router")

CONFIG_PATH = Path(__file__).parent.parent / "config" / "strategies.yaml"


@dataclass
class StrategyResult:
    name: str
    score: float
    action: str
    confidence: float
    reason: str
    weight: float
    details: dict = field(default_factory=dict)


class StrategyRouter:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init()
        return cls._instance

    def _init(self):
        self._strategies: list[BaseStrategy] = []
        self._weights: dict[str, float] = {}
        self._config_mtime: float = 0
        self._load_config()
        self._register_default_strategies()

        self._cache: dict[str, tuple[float, dict]] = {}
        self._cache_ttl: float = 0.5

    def _load_config(self):
        default_weights = {
            "Trend": 0.40,
            "Reversal": 0.30,
            "Breakout": 0.30,
        }
        current_mtime = CONFIG_PATH.stat().st_mtime if CONFIG_PATH.exists() else 0

        if current_mtime > self._config_mtime:
            if CONFIG_PATH.exists():
                try:
                    with open(CONFIG_PATH, encoding="utf-8") as f:
                        config = yaml.safe_load(f)
                        if config and "strategies" in config:
                            for name, cfg in config["strategies"].items():
                                if "weight" in cfg:
                                    default_weights[name] = cfg["weight"]
                            logger.info(f"🔄 전략 가중치 재로드: {default_weights}")
                except Exception as e:
                    logger.warning(f"⚠️ strategies.yaml 로드 실패: {e}, 기존값 유지")
            self._config_mtime = current_mtime

        self._weights = default_weights

    def _register_default_strategies(self):
        self._strategies = [
            TrendStrategy(weight=self._weights.get("Trend", 0.40)),
            ReversalStrategy(weight=self._weights.get("Reversal", 0.30)),
            BreakoutStrategy(weight=self._weights.get("Breakout", 0.30)),
        ]
        total = sum(s.weight for s in self._strategies)
        if total > 0 and abs(total - 1.0) > 0.001:
            for s in self._strategies:
                s._weight = s.weight / total
            logger.info("⚖️ 전략 가중치 정규화 완료 (합계 1.0)")
        logger.info(f"✅ {len(self._strategies)}개 전략 등록 완료")

    # 🔥 P1-9: 캐시 키에 tech_data 포함
    def _get_cache_key(self, data: dict) -> str:
        ticker = data.get("ticker", "unknown")
        price = data.get("price", 0.0)
        volume = data.get("volume", 0)
        tech = data.get("tech_data", {})
        # tech_data의 주요 값 추출 (RSI, EMA5, EMA20, volume_ratio)
        tech_hash = f"{tech.get('rsi', 0):.1f}_{tech.get('ema5', 0):.0f}_{tech.get('ema20', 0):.0f}_{tech.get('volume_ratio', 1.0):.2f}"
        return f"{ticker}_{price:.0f}_{volume}_{tech_hash}"

    async def route(self, data: dict[str, Any]) -> dict[str, Any]:
        self._load_config()

        cache_key = self._get_cache_key(data)
        if cache_key in self._cache:
            timestamp, cached_result = self._cache[cache_key]
            if time.time() - timestamp < self._cache_ttl:
                logger.debug(f"📦 캐시 사용: {data.get('ticker')} (TTL {self._cache_ttl:.1f}s)")
                cached_result["cached"] = True
                return cached_result

        tasks = [self._run_strategy_safe(s, data) for s in self._strategies]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        valid_results: list[StrategyResult] = []
        for r in results:
            if isinstance(r, Exception):
                logger.warning(f"⚠️ 전략 실행 예외: {r}")
                continue
            if r is not None and isinstance(r, StrategyResult):
                valid_results.append(r)

        if not valid_results:
            fallback = {
                "final_score": 0.5,
                "final_action": "HOLD",
                "final_confidence": 0.3,
                "strategy_results": [],
                "consensus": "모든 전략 실패",
                "action_votes": {"BUY": 0, "SELL": 0, "HOLD": 1},
                "cached": False,
            }
            self._cache[cache_key] = (time.time(), fallback)
            return fallback

        weighted_score = 0.0
        total_weight = 0.0
        action_votes = {"BUY": 0.0, "SELL": 0.0, "HOLD": 0.0}
        strategy_results = []

        for r in valid_results:
            strategy_results.append(r)
            weighted_score += r.score * r.weight
            total_weight += r.weight
            action_votes[r.action] += r.weight

        final_score = weighted_score / total_weight if total_weight > 0 else 0.5
        final_score = max(0.0, min(1.0, final_score))

        final_action = max(action_votes, key=action_votes.get)
        if final_action == "HOLD" and final_score > 0.65:
            final_action = "BUY"
        elif final_action == "HOLD" and final_score < 0.35:
            final_action = "SELL"

        max_score = max(r.score for r in results if not isinstance(r, Exception)) if valid_results else 0.5
        confidence = 0.5 + (final_score - 0.5) * 1.2 * (max_score)
        confidence = max(0.3, min(0.95, confidence))

        consensus = f"{len([r for r in valid_results if r.action == final_action])}/{len(valid_results)}개 전략 일치"

        result = {
            "final_score": final_score,
            "final_action": final_action,
            "final_confidence": confidence,
            "strategy_results": strategy_results,
            "consensus": consensus,
            "action_votes": action_votes,
            "cached": False,
        }

        self._cache[cache_key] = (time.time(), result)

        if len(self._cache) > 1000:
            now = time.time()
            expired = [k for k, (ts, _) in self._cache.items() if now - ts > self._cache_ttl * 2]
            for k in expired:
                del self._cache[k]

        return result

    async def _run_strategy_safe(self, strategy: BaseStrategy, data: dict) -> StrategyResult | None:
        try:
            result = await asyncio.to_thread(strategy.analyze, data)
            return StrategyResult(
                name=strategy.name,
                score=result.get("score", 0.5),
                action=result.get("action", "HOLD"),
                confidence=result.get("confidence", 0.5),
                reason=result.get("reason", ""),
                weight=strategy.weight,
                details=result.get("details", {}),
            )
        except Exception as e:
            logger.error(f"❌ 전략 {strategy.name} 실행 오류: {e}")
            return e

    def get_strategy_names(self) -> list[str]:
        return [s.name for s in self._strategies]

    def reload_config(self):
        self._config_mtime = 0
        self._load_config()
        self._register_default_strategies()
        logger.info("🔄 전략 설정 수동 재로드 완료")
