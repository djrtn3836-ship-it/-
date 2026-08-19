"""
orchestrator/strategy_router.py - v1.1 FINAL (캐싱 + 자동 재로드 + 예외 격리)
- 전략 실행 결과 캐싱 (TTL 0.5초, 동일 종목 중복 분석 방지)
- strategies.yaml 변경 시 자동 재로드 (mtime 감지)
- 개별 전략 실패 시 나머지 전략은 계속 실행 (return_exceptions=True)
- 가중치 정규화 (합계 1.0 보장)
"""

import asyncio
import yaml
import time
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, field

from core.logger import setup_logger
from strategy.base_strategy import BaseStrategy
from strategy.trend_strategy import TrendStrategy
from strategy.reversal_strategy import ReversalStrategy
from strategy.breakout_strategy import BreakoutStrategy

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
    details: Dict = field(default_factory=dict)


class StrategyRouter:
    """멀티 전략 라우터 (v1.1 - 캐싱 + 자동 재로드)"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init()
        return cls._instance

    def _init(self):
        self._strategies: List[BaseStrategy] = []
        self._weights: Dict[str, float] = {}
        self._config_mtime: float = 0
        self._load_config()
        self._register_default_strategies()

        # 🔥 v1.1: 캐싱 (동일 종목 중복 분석 방지)
        self._cache: Dict[str, Tuple[float, Dict]] = {}  # key: ticker_price_volume, value: (timestamp, result)
        self._cache_ttl: float = 0.5  # 0.5초

    def _load_config(self):
        """strategies.yaml 로드 (mtime 체크)"""
        default_weights = {
            'Trend': 0.40,
            'Reversal': 0.30,
            'Breakout': 0.30,
        }
        current_mtime = CONFIG_PATH.stat().st_mtime if CONFIG_PATH.exists() else 0

        if current_mtime > self._config_mtime:
            if CONFIG_PATH.exists():
                try:
                    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                        config = yaml.safe_load(f)
                        if config and 'strategies' in config:
                            for name, cfg in config['strategies'].items():
                                if 'weight' in cfg:
                                    default_weights[name] = cfg['weight']
                            logger.info(f"🔄 전략 가중치 재로드: {default_weights}")
                except Exception as e:
                    logger.warning(f"⚠️ strategies.yaml 로드 실패: {e}, 기존값 유지")
            self._config_mtime = current_mtime

        self._weights = default_weights

    def _register_default_strategies(self):
        """기본 전략 등록 (가중치 적용)"""
        self._strategies = [
            TrendStrategy(weight=self._weights.get('Trend', 0.40)),
            ReversalStrategy(weight=self._weights.get('Reversal', 0.30)),
            BreakoutStrategy(weight=self._weights.get('Breakout', 0.30)),
        ]
        # 가중치 정규화 (합계 1.0)
        total = sum(s.weight for s in self._strategies)
        if total > 0 and abs(total - 1.0) > 0.001:
            for s in self._strategies:
                s._weight = s.weight / total
            logger.info(f"⚖️ 전략 가중치 정규화 완료 (합계 1.0)")
        logger.info(f"✅ {len(self._strategies)}개 전략 등록 완료")

    def _get_cache_key(self, data: Dict) -> str:
        """캐시 키 생성 (ticker + price + volume)"""
        ticker = data.get('ticker', 'unknown')
        price = data.get('price', 0.0)
        volume = data.get('volume', 0)
        return f"{ticker}_{price:.0f}_{volume}"

    async def route(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        모든 전략 병렬 실행 및 결과 집계 (캐싱 적용)

        Args:
            data: 종목 데이터 (price, tech_data, regime, atr 등)
        Returns:
            {
                'final_score': float,
                'final_action': str,
                'final_confidence': float,
                'strategy_results': List[StrategyResult],
                'consensus': str,
                'action_votes': dict,
                'cached': bool  # v1.1: 캐시 사용 여부
            }
        """
        # 0. 설정 파일 재로드 체크
        self._load_config()

        # 1. 캐시 체크
        cache_key = self._get_cache_key(data)
        if cache_key in self._cache:
            timestamp, cached_result = self._cache[cache_key]
            if time.time() - timestamp < self._cache_ttl:
                logger.debug(f"📦 캐시 사용: {data.get('ticker')} (TTL {self._cache_ttl:.1f}s)")
                cached_result['cached'] = True
                return cached_result

        # 2. 병렬 실행 (예외 격리)
        tasks = [self._run_strategy_safe(s, data) for s in self._strategies]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 3. 결과 필터링 (예외는 제외)
        valid_results: List[StrategyResult] = []
        for r in results:
            if isinstance(r, Exception):
                logger.warning(f"⚠️ 전략 실행 예외: {r}")
                continue
            if r is not None and isinstance(r, StrategyResult):
                valid_results.append(r)

        if not valid_results:
            # 모든 전략 실패 시 중립 반환
            fallback = {
                'final_score': 0.5,
                'final_action': 'HOLD',
                'final_confidence': 0.3,
                'strategy_results': [],
                'consensus': '모든 전략 실패',
                'action_votes': {'BUY': 0, 'SELL': 0, 'HOLD': 1},
                'cached': False
            }
            self._cache[cache_key] = (time.time(), fallback)
            return fallback

        # 4. 집계
        weighted_score = 0.0
        total_weight = 0.0
        action_votes = {'BUY': 0.0, 'SELL': 0.0, 'HOLD': 0.0}
        strategy_results = []

        for r in valid_results:
            strategy_results.append(r)
            weighted_score += r.score * r.weight
            total_weight += r.weight
            action_votes[r.action] += r.weight

        final_score = weighted_score / total_weight if total_weight > 0 else 0.5
        final_score = max(0.0, min(1.0, final_score))

        # 액션 결정 (가중 투표)
        final_action = max(action_votes, key=action_votes.get)
        # HOLD가 가장 높아도 점수에 따라 BUY/SELL 조정
        if final_action == 'HOLD' and final_score > 0.65:
            final_action = 'BUY'
        elif final_action == 'HOLD' and final_score < 0.35:
            final_action = 'SELL'

        # 신뢰도: 최고 점수 전략과의 차이 기반
        max_score = max(r.score for r in results if not isinstance(r, Exception)) if valid_results else 0.5
        confidence = 0.5 + (final_score - 0.5) * 1.2 * (max_score)
        confidence = max(0.3, min(0.95, confidence))

        # 합의 요약
        consensus = f"{len([r for r in valid_results if r.action == final_action])}/{len(valid_results)}개 전략 일치"

        result = {
            'final_score': final_score,
            'final_action': final_action,
            'final_confidence': confidence,
            'strategy_results': strategy_results,
            'consensus': consensus,
            'action_votes': action_votes,
            'cached': False
        }

        # 5. 캐시 저장
        self._cache[cache_key] = (time.time(), result)

        # 캐시 크기 제한 (메모리 누수 방지)
        if len(self._cache) > 1000:
            # 오래된 항목 제거 (TTL의 2배 이상)
            now = time.time()
            expired = [k for k, (ts, _) in self._cache.items() if now - ts > self._cache_ttl * 2]
            for k in expired:
                del self._cache[k]

        return result

    async def _run_strategy_safe(self, strategy: BaseStrategy, data: Dict) -> Optional[StrategyResult]:
        """개별 전략 실행 (예외를 Exception으로 반환)"""
        try:
            # 동기 함수를 스레드로 실행 (CPU 바운드)
            result = await asyncio.to_thread(strategy.analyze, data)
            return StrategyResult(
                name=strategy.name,
                score=result.get('score', 0.5),
                action=result.get('action', 'HOLD'),
                confidence=result.get('confidence', 0.5),
                reason=result.get('reason', ''),
                weight=strategy.weight,
                details=result.get('details', {}),
            )
        except Exception as e:
            logger.error(f"❌ 전략 {strategy.name} 실행 오류: {e}")
            return e  # 예외를 그대로 반환 (gather에서 처리)

    def get_strategy_names(self) -> List[str]:
        return [s.name for s in self._strategies]

    def reload_config(self):
        """수동 설정 재로드"""
        self._config_mtime = 0  # 강제 재로드 유도
        self._load_config()
        self._register_default_strategies()
        logger.info(f"🔄 전략 설정 수동 재로드 완료")