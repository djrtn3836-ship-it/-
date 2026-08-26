# -*- coding: utf-8 -*-
"""
orchestrator/strategy_router.py - v2.1 (V10 전용, strategy/ 레거시 완전 제거)
- 긴급 수정: strategy/ 폴더가 실제로 존재하지 않아 발생하던
  ModuleNotFoundError: No module named 'strategy' 를 해결.
  이 오류로 인해 scanner/deep_analyzer.py -> orchestrator/strategy_router.py
  임포트 체인 전체가 실패하여, app/main.py를 포함한 시스템 전체가
  부팅조차 되지 않는 상태였음.
- domain/strategies/ (Trend, Reversal, Breakout) 만 사용하도록 완전 교체
- 캐시 키에 tech_data 포함 (기존 기능 유지)
"""

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from core.logger import setup_logger

# V10: domain/strategies/ 만 사용 (strategy/ 폴더 의존성 완전 제거)
from domain.strategies.trend import TrendStrategy
from domain.strategies.reversal import ReversalStrategy
from domain.strategies.breakout import BreakoutStrategy

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
        self._strategies: list = []
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
                            logger.info(f"전략 가중치 재로드: {default_weights}")
                except Exception as e:
                    logger.warning(f"strategies.yaml 로드 실패: {e}, 기존값 유지")
            self._config_mtime = current_mtime

        self._weights = default_weights

    def _register_default_strategies(self):
        self._strategies = [
            TrendStrategy(),
            ReversalStrategy(),
            BreakoutStrategy(),
        ]
        total = sum(s.weight for s in self._strategies)
        if total > 0 and abs(total - 1.0) > 0.001:
            for s in self._strategies:
                if hasattr(s, "_weight"):
                    s._weight = s.weight / total
            logger.info("전략 가중치 정규화 완료 (합계 1.0)")
        logger.info(f"{len(self._strategies)}개 전략 등록 완료 (V10 domain/strategies)")

    def _get_cache_key(self, data: dict) -> str:
        ticker = data.get("ticker", "unknown")
        price = data.get("price", 0.0)
        volume = data.get("volume", 0)
        tech = data.get("tech_data", {})
        tech_hash = (
            f"{tech.get('rsi', 0):.1f}_"
            f"{tech.get('ema5', 0):.0f}_"
            f"{tech.get('ema20', 0):.0f}_"
            f"{tech.get('volume_ratio', 1.0):.2f}"
        )
        return f"{ticker}_{price:.0f}_{volume}_{tech_hash}"

    async def route(self, data: dict[str, Any]) -> dict[str, Any]:
        self._load_config()

        cache_key = self._get_cache_key(data)
        if cache_key in self._cache:
            timestamp, cached_result = self._cache[cache_key]
            if time.time() - timestamp < self._cache_ttl:
                cached_result["cached"] = True
                return cached_result

        tasks = [self._run_strategy_safe(s, data) for s in self._strategies]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        valid_results: list[StrategyResult] = []
        for r in results:
            if isinstance(r, Exception):
                logger.warning(f"전략 실행 예외: {r}")
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

        for r in valid_results:
            weighted_score += r.score * r.weight
            total_weight += r.weight
            action_votes[r.action] = action_votes.get(r.action, 0.0) + r.weight

        final_score = weighted_score / total_weight if total_weight > 0 else 0.5
        final_score = max(0.0, min(1.0, final_score))

        final_action = max(action_votes, key=action_votes.get)
        if final_action == "HOLD" and final_score > 0.65:
            final_action = "BUY"
        elif final_action == "HOLD" and final_score < 0.35:
            final_action = "SELL"

        max_score = max((r.score for r in valid_results), default=0.5)
        confidence = 0.5 + (final_score - 0.5) * 1.2 * max_score
        confidence = max(0.3, min(0.95, confidence))

        consensus = (
            f"{len([r for r in valid_results if r.action == final_action])}"
            f"/{len(valid_results)}개 전략 일치"
        )

        result = {
            "final_score": final_score,
            "final_action": final_action,
            "final_confidence": confidence,
            "strategy_results": valid_results,
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

    async def _run_strategy_safe(self, strategy, data: dict) -> StrategyResult | None:
        try:
            if hasattr(strategy, "analyze") and asyncio.iscoroutinefunction(strategy.analyze):
                result = await strategy.analyze(data)
            else:
                result = await asyncio.to_thread(strategy.analyze, data)

            if hasattr(result, "name") and hasattr(result, "score"):
                return StrategyResult(
                    name=result.name,
                    score=result.score,
                    action=result.action,
                    confidence=result.confidence,
                    reason=" | ".join(result.reasons) if hasattr(result, "reasons") and result.reasons else "",
                    weight=strategy.weight,
                    details=result.metadata if hasattr(result, "metadata") else {},
                )
            elif isinstance(result, dict):
                return StrategyResult(
                    name=strategy.name,
                    score=result.get("score", 0.5),
                    action=result.get("action", "HOLD"),
                    confidence=result.get("confidence", 0.5),
                    reason=result.get("reason", ""),
                    weight=strategy.weight,
                    details=result.get("details", {}),
                )
            else:
                logger.warning(f"{strategy.name} 전략 결과 형식 오류: {type(result)}")
                return None
        except Exception as e:
            logger.error(f"전략 {strategy.name} 실행 오류: {e}")
            return None

    def get_strategy_names(self) -> list[str]:
        return [s.name for s in self._strategies]

    def reload_config(self):
        self._config_mtime = 0
        self._load_config()
        self._register_default_strategies()
        logger.info("전략 설정 수동 재로드 완료")