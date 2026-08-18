"""
orchestrator/strategy_router.py - v1.0 FINAL (멀티 전략 라우터)
- 등록된 모든 전략을 병렬 실행하고 결과를 집계
- 가중치 기반 최종 점수 및 액션 결정
- DeepAnalyzer에서 호출됨
"""

import asyncio
import yaml
from pathlib import Path
from typing import Dict, List, Any, Optional
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
    """멀티 전략 라우터 (싱글톤)"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init()
        return cls._instance

    def _init(self):
        self._strategies: List[BaseStrategy] = []
        self._weights: Dict[str, float] = {}
        self._load_config()
        self._register_default_strategies()

    def _load_config(self):
        """strategies.yaml에서 가중치 로드"""
        default_weights = {
            'Trend': 0.40,
            'Reversal': 0.30,
            'Breakout': 0.30,
        }
        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                    config = yaml.safe_load(f)
                    if config and 'strategies' in config:
                        for name, cfg in config['strategies'].items():
                            if 'weight' in cfg:
                                default_weights[name] = cfg['weight']
                        logger.info(f"✅ 전략 가중치 로드: {default_weights}")
            except Exception as e:
                logger.warning(f"⚠️ strategies.yaml 로드 실패: {e}, 기본값 사용")
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
        if total > 0:
            for s in self._strategies:
                s._weight = s.weight / total
        logger.info(f"✅ {len(self._strategies)}개 전략 등록 완료")

    async def route(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        모든 전략 병렬 실행 및 결과 집계
        Args:
            data: 종목 데이터 (price, tech_data, regime, atr 등)
        Returns:
            {
                'final_score': float,
                'final_action': str,
                'final_confidence': float,
                'strategy_results': List[StrategyResult],
                'consensus': str
            }
        """
        # 병렬 실행
        tasks = [self._run_strategy(s, data) for s in self._strategies]
        results = await asyncio.gather(*tasks)

        # 집계
        weighted_score = 0.0
        total_weight = 0.0
        action_votes = {'BUY': 0, 'SELL': 0, 'HOLD': 0}
        strategy_results = []

        for r in results:
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
        max_score = max(r.score for r in results)
        confidence = 0.5 + (final_score - 0.5) * 1.2 * (max_score)
        confidence = max(0.3, min(0.95, confidence))

        # 합의 요약
        consensus = f"{len([r for r in results if r.action == final_action])}/{len(results)}개 전략 일치"

        logger.debug(
            f"📊 전략 집계: 최종 {final_action} (점수 {final_score:.3f}, 신뢰도 {confidence:.2f}) "
            f"| BUY:{action_votes['BUY']:.2f} SELL:{action_votes['SELL']:.2f} HOLD:{action_votes['HOLD']:.2f}"
        )

        return {
            'final_score': final_score,
            'final_action': final_action,
            'final_confidence': confidence,
            'strategy_results': strategy_results,
            'consensus': consensus,
            'action_votes': action_votes,
        }

    async def _run_strategy(self, strategy: BaseStrategy, data: Dict) -> StrategyResult:
        """개별 전략 실행"""
        try:
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
            return StrategyResult(
                name=strategy.name,
                score=0.5,
                action='HOLD',
                confidence=0.3,
                reason=f"오류: {str(e)[:30]}",
                weight=strategy.weight,
            )

    def get_strategy_names(self) -> List[str]:
        return [s.name for s in self._strategies]

    def reload_config(self):
        """설정 재로드 (동적 가중치 변경)"""
        self._load_config()
        # 기존 전략 객체의 가중치 업데이트
        for s in self._strategies:
            if s.name in self._weights:
                s._weight = self._weights[s.name]
        total = sum(s.weight for s in self._strategies)
        if total > 0:
            for s in self._strategies:
                s._weight = s.weight / total
        logger.info(f"🔄 전략 가중치 재조정 완료")