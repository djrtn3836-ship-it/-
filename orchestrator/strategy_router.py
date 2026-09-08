# -*- coding: utf-8 -*-
"""
orchestrator/strategy_router.py - v2.1.1 (V10 전용, mypy strict 적용 - Session 42)
- 긴급 수정 이력 유지: strategy/ 폴더가 실제로 존재하지 않아 발생하던
  ModuleNotFoundError: No module named 'strategy' 를 해결한 v2.1 기반.
- domain/strategies/ (Trend, Reversal, Breakout) 만 사용 (strategy/ 폴더 의존성 완전 제거)
- 캐시 키에 tech_data 포함 (기존 기능 유지)
- Session 42: mypy strict 완결 처리 (모든 메서드 시그니처/내부 자료구조에
  명시적 제네릭 타입 추가). 로직/동작 100% 무변경.
- 주의: 로컬 StrategyResult 데이터클래스는 domain.strategies.base.StrategyResult와
  이름이 같지만 별개의 클래스입니다. 혼동 방지를 위해 domain.strategies.base에서는
  Strategy만 임포트하고 StrategyResult는 임포트하지 않습니다.
"""

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from core.logger import setup_logger

from domain.strategies.trend import TrendStrategy
from domain.strategies.reversal import ReversalStrategy
from domain.strategies.breakout import BreakoutStrategy
from domain.strategies.base import Strategy

logger = setup_logger("strategy_router")

CONFIG_PATH: Path = Path(__file__).parent.parent / "config" / "strategies.yaml"


@dataclass
class StrategyResult:
    name: str
    score: float
    action: str
    confidence: float
    reason: str
    weight: float
    details: Dict[str, Any] = field(default_factory=dict)


class StrategyRouter:
    _instance: Optional["StrategyRouter"] = None

    def __new__(cls) -> "StrategyRouter":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init()
        return cls._instance

    def _init(self) -> None:
        self._strategies: List[Strategy] = []
        self._weights: Dict[str, float] = {}
        self._config_mtime: float = 0.0
        self._load_config()
        self._register_default_strategies()

        self._cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self._cache_ttl: float = 0.5

    def _load_config(self) -> None:
        default_weights: Dict[str, float] = {
            "Trend": 0.40,
            "Reversal": 0.30,
            "Breakout": 0.30,
        }
        current_mtime: float = CONFIG_PATH.stat().st_mtime if CONFIG_PATH.exists() else 0.0

        if current_mtime > self._config_mtime:
            if CONFIG_PATH.exists():
                try:
                    with open(CONFIG_PATH, encoding="utf-8") as f:
                        config: Any = yaml.safe_load(f)
                        if config and isinstance(config, dict) and "strategies" in config:
                            for name, cfg in config["strategies"].items():
                                if isinstance(cfg, dict) and "weight" in cfg:
                                    default_weights[name] = float(cfg["weight"])
                            logger.info(f"전략 가중치 재로드: {default_weights}")
                except Exception as e:
                    logger.warning(f"strategies.yaml 로드 실패: {e}, 기존값 유지")
            self._config_mtime = current_mtime

        self._weights = default_weights

    def _register_default_strategies(self) -> None:
        self._strategies = [
            TrendStrategy(),
            ReversalStrategy(),
            BreakoutStrategy(),
        ]
        total: float = sum(float(s.weight) for s in self._strategies)
        if total > 0 and abs(total - 1.0) > 0.001:
            for s in self._strategies:
                if hasattr(s, "_weight"):
                    # Strategy.weight가 읽기 전용 프로퍼티이므로 내부 저장 속성을
                    # setattr로 직접 조정 (원본 v2.1의 동작과 100% 동일, mypy strict
                    # [attr-defined] 회피를 위해 setattr 사용)
                    setattr(s, "_weight", float(s.weight) / total)
            logger.info("전략 가중치 정규화 완료 (합계 1.0)")
        logger.info(f"{len(self._strategies)}개 전략 등록 완료 (V10 domain/strategies)")

    def _get_cache_key(self, data: Dict[str, Any]) -> str:
        ticker: str = str(data.get("ticker", "unknown"))
        price: float = float(data.get("price", 0.0))
        volume: int = int(data.get("volume", 0))
        tech: Dict[str, Any] = data.get("tech_data", {})
        tech_hash: str = (
            f"{float(tech.get('rsi', 0.0)):.1f}_"
            f"{float(tech.get('ema5', 0.0)):.0f}_"
            f"{float(tech.get('ema20', 0.0)):.0f}_"
            f"{float(tech.get('volume_ratio', 1.0)):.2f}"
        )
        return f"{ticker}_{price:.0f}_{volume}_{tech_hash}"

    async def route(self, data: Dict[str, Any]) -> Dict[str, Any]:
        self._load_config()

        cache_key: str = self._get_cache_key(data)
        if cache_key in self._cache:
            timestamp, cached_result = self._cache[cache_key]
            if time.time() - timestamp < self._cache_ttl:
                cached_result["cached"] = True
                return cached_result

        tasks = [self._run_strategy_safe(s, data) for s in self._strategies]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        valid_results: List[StrategyResult] = []
        for r in results:
            if isinstance(r, Exception):
                logger.warning(f"전략 실행 예외: {r}")
                continue
            if r is not None and isinstance(r, StrategyResult):
                valid_results.append(r)

        if not valid_results:
            fallback: Dict[str, Any] = {
                "final_score": 0.5,
                "final_action": "HOLD",
                "final_confidence": 0.3,
                "strategy_results": [],
                "consensus": "모든 전략 실패",
                "action_votes": {"BUY": 0.0, "SELL": 0.0, "HOLD": 1.0},
                "cached": False,
            }
            self._cache[cache_key] = (time.time(), fallback)
            return fallback

        weighted_score: float = 0.0
        total_weight: float = 0.0
        action_votes: Dict[str, float] = {"BUY": 0.0, "SELL": 0.0, "HOLD": 0.0}

        for r in valid_results:
            weighted_score += r.score * r.weight
            total_weight += r.weight
            action_votes[r.action] = action_votes.get(r.action, 0.0) + r.weight

        final_score: float = weighted_score / total_weight if total_weight > 0 else 0.5
        final_score = max(0.0, min(1.0, final_score))

        final_action: str = max(action_votes, key=lambda k: action_votes[k])
        if final_action == "HOLD" and final_score > 0.65:
            final_action = "BUY"
        elif final_action == "HOLD" and final_score < 0.35:
            final_action = "SELL"

        max_score: float = max((r.score for r in valid_results), default=0.5)
        confidence: float = 0.5 + (final_score - 0.5) * 1.2 * max_score
        confidence = max(0.3, min(0.95, confidence))

        consensus: str = (
            f"{len([r for r in valid_results if r.action == final_action])}"
            f"/{len(valid_results)}개 전략 일치"
        )

        result: Dict[str, Any] = {
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
            now: float = time.time()
            expired: List[str] = [k for k, (ts, _) in self._cache.items() if now - ts > self._cache_ttl * 2]
            for k in expired:
                del self._cache[k]

        return result

    async def _run_strategy_safe(self, strategy: Strategy, data: Dict[str, Any]) -> Optional[StrategyResult]:
        try:
            if hasattr(strategy, "analyze") and asyncio.iscoroutinefunction(strategy.analyze):
                result: Any = await strategy.analyze(data)
            else:
                result = await asyncio.to_thread(strategy.analyze, data)

            if hasattr(result, "name") and hasattr(result, "score"):
                return StrategyResult(
                    name=str(result.name),
                    score=float(result.score),
                    action=str(result.action),
                    confidence=float(result.confidence),
                    reason=" | ".join(result.reasons) if hasattr(result, "reasons") and result.reasons else "",
                    weight=float(strategy.weight),
                    details=result.metadata if hasattr(result, "metadata") else {},
                )
            elif isinstance(result, dict):
                return StrategyResult(
                    name=str(strategy.name),
                    score=float(result.get("score", 0.5)),
                    action=str(result.get("action", "HOLD")),
                    confidence=float(result.get("confidence", 0.5)),
                    reason=str(result.get("reason", "")),
                    weight=float(strategy.weight),
                    details=result.get("details", {}),
                )
            else:
                logger.warning(f"{strategy.name} 전략 결과 형식 오류: {type(result)}")
                return None
        except Exception as e:
            logger.error(f"전략 {strategy.name} 실행 오류: {e}")
            return None

    def get_strategy_names(self) -> List[str]:
        return [str(s.name) for s in self._strategies]

    def reload_config(self) -> None:
        self._config_mtime = 0.0
        self._load_config()
        self._register_default_strategies()
        logger.info("전략 설정 수동 재로드 완료")
