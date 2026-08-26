# -*- coding: utf-8 -*-
"""
application/analysis/strategy_bandit.py - Multi-Armed Bandit 전략 선택기 v1.0

Thompson Sampling 기반 전략 가중치 자동 갱신.

목적:
    여러 전략(TrendStrategy, ReversalStrategy, BreakoutStrategy) 중
    수익률 기반으로 선택 확률을 동적으로 갱신합니다.
    "탐색(Exploration) vs 활용(Exploitation)"을 Thompson Sampling으로 균형 잡습니다.

알고리즘:
    - 각 전략을 Bandit Arm으로 모델링
    - 승리(win) / 패배(loss)를 Beta 분포의 alpha / beta 파라미터로 추적
    - 샘플링 시 Beta(alpha, beta)에서 난수 추출 → 최고 샘플이 선택됨
    - DB outcome 기반으로 실시간 갱신

특징:
    - 순수 Python (numpy 불필요 - Beta 분포 근사 구현)
    - 스레드 안전한 asyncio.Lock 보호
    - 상태 직렬화 (to_dict / from_dict)
    - 망각 인자(decay) 지원 → 오래된 결과는 영향력 감소
"""

import asyncio
import math
import random
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


# ─── Beta 분포 근사 헬퍼 ────────────────────────────────────────────
# numpy 없이 Beta(α, β) 분포에서 샘플링 (감마 분포 합 방법)
# Knuth의 알고리즘을 사용한 감마 분포 샘플링

def _gamma_sample(alpha: float) -> float:
    """감마 분포 Gamma(alpha, 1) 샘플링 (Marsaglia-Tsang 방법).

    Args:
        alpha: 형상 파라미터 (> 0)

    Returns:
        float: 감마 분포 샘플
    """
    if alpha < 1.0:
        return _gamma_sample(1.0 + alpha) * (random.random() ** (1.0 / alpha))

    d = alpha - 1.0 / 3.0
    c = 1.0 / math.sqrt(9.0 * d)
    while True:
        x = random.gauss(0, 1)
        v = (1.0 + c * x) ** 3
        if v > 0:
            u = random.random()
            if u < 1.0 - 0.0331 * (x ** 4):
                return d * v
            if math.log(u) < 0.5 * x ** 2 + d * (1.0 - v + math.log(v)):
                return d * v


def _beta_sample(alpha: float, beta: float) -> float:
    """Beta(alpha, beta) 분포에서 샘플링.

    Args:
        alpha: 성공 파라미터 (≥ 1.0)
        beta: 실패 파라미터 (≥ 1.0)

    Returns:
        float: [0, 1] 범위의 샘플
    """
    alpha = max(1.0, alpha)
    beta = max(1.0, beta)
    g1 = _gamma_sample(alpha)
    g2 = _gamma_sample(beta)
    total = g1 + g2
    if total <= 0:
        return 0.5
    return g1 / total


# ═══════════════════════════════════════════════════════════════════

@dataclass
class BanditArm:
    """Bandit의 단일 팔(전략) 상태.

    Beta(alpha, beta) 분포를 통해 전략의 "수익률 잠재력"을 표현합니다.

    Attributes:
        name: 전략 이름 (예: "Trend", "Reversal", "Breakout")
        alpha: 성공 횟수 + 1 (초기값 1.0 - uniform prior)
        beta: 실패 횟수 + 1 (초기값 1.0 - uniform prior)
        total_plays: 총 사용 횟수
        total_reward: 누적 보상 (수익률 합)
        decay: 망각 인자 (0~1, 오래된 결과 영향력 감소)
    """

    name: str
    alpha: float = 1.0
    beta: float = 1.0
    total_plays: int = 0
    total_reward: float = 0.0
    decay: float = 0.99

    @property
    def mean_reward(self) -> float:
        """현재 기대 보상 (alpha / (alpha + beta))."""
        total = self.alpha + self.beta
        return self.alpha / total if total > 0 else 0.5

    @property
    def uncertainty(self) -> float:
        """불확실도 - play 횟수가 적을수록 높음."""
        total = self.alpha + self.beta
        variance = (self.alpha * self.beta) / (total ** 2 * (total + 1))
        return math.sqrt(variance)

    def sample(self) -> float:
        """Thompson Sampling: Beta 분포에서 샘플.

        Returns:
            float: [0, 1] 범위의 샘플 (높을수록 선택 가능성 높음)
        """
        return _beta_sample(self.alpha, self.beta)

    def update(self, reward: float) -> None:
        """결과 반영하여 파라미터 갱신.

        Args:
            reward: 실현된 보상 (양수=성공, 음수=실패)
                    수익률 기반: 양수 수익 → reward > 0
        """
        # 망각 인자 적용 (오래된 정보 서서히 소멸)
        self.alpha = max(1.0, self.alpha * self.decay)
        self.beta = max(1.0, self.beta * self.decay)

        if reward > 0:
            self.alpha += reward
        else:
            self.beta += abs(reward)

        self.total_plays += 1
        self.total_reward += reward

    def to_dict(self) -> dict:
        """직렬화."""
        return {
            "name": self.name,
            "alpha": self.alpha,
            "beta": self.beta,
            "total_plays": self.total_plays,
            "total_reward": self.total_reward,
            "decay": self.decay,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "BanditArm":
        """역직렬화."""
        arm = cls(name=d["name"])
        arm.alpha = d.get("alpha", 1.0)
        arm.beta = d.get("beta", 1.0)
        arm.total_plays = d.get("total_plays", 0)
        arm.total_reward = d.get("total_reward", 0.0)
        arm.decay = d.get("decay", 0.99)
        return arm


class StrategyBandit:
    """Multi-Armed Bandit 전략 선택기 (Thompson Sampling).

    전략들의 과거 성과를 베이지안 업데이트로 추적하고,
    Thompson Sampling으로 탐색/활용을 균형 있게 수행합니다.

    사용 예::

        bandit = StrategyBandit(["Trend", "Reversal", "Breakout"])

        # 전략 선택 (확률적)
        selected = bandit.select()

        # 결과 반영 (수익률 기반)
        await bandit.update("Trend", reward=0.023)   # 2.3% 수익
        await bandit.update("Reversal", reward=-0.01) # -1.0% 손실

        # 현재 가중치 조회
        weights = bandit.get_weights()
    """

    def __init__(
        self,
        strategy_names: List[str],
        decay: float = 0.99,
        seed: Optional[int] = None,
    ) -> None:
        """초기화.

        Args:
            strategy_names: 전략 이름 목록
            decay: 망각 인자 (기본 0.99, 1.0이면 망각 없음)
            seed: 재현 가능한 랜덤 시드 (테스트용)
        """
        if seed is not None:
            random.seed(seed)

        self._arms: Dict[str, BanditArm] = {
            name: BanditArm(name=name, decay=decay)
            for name in strategy_names
        }
        self._lock = asyncio.Lock()
        self._total_selections: int = 0

    @property
    def arm_names(self) -> List[str]:
        """등록된 전략 이름 목록."""
        return list(self._arms.keys())

    def select(self) -> str:
        """Thompson Sampling으로 전략 선택.

        각 전략의 Beta 분포에서 샘플을 추출하고
        가장 높은 샘플을 반환한 전략을 선택합니다.

        Returns:
            str: 선택된 전략 이름
        """
        if not self._arms:
            return ""

        samples = {name: arm.sample() for name, arm in self._arms.items()}
        selected = max(samples, key=lambda k: samples[k])
        self._total_selections += 1
        return selected

    def select_top_k(self, k: int) -> List[str]:
        """상위 k개 전략 선택 (앙상블용).

        Args:
            k: 선택할 전략 수

        Returns:
            List[str]: 선택된 전략 이름 목록 (샘플 높은 순)
        """
        if not self._arms:
            return []
        k = min(k, len(self._arms))
        samples = {name: arm.sample() for name, arm in self._arms.items()}
        return sorted(samples, key=lambda x: samples[x], reverse=True)[:k]

    async def update(self, strategy_name: str, reward: float) -> None:
        """전략 결과를 반영해 파라미터 갱신.

        Args:
            strategy_name: 업데이트할 전략 이름
            reward: 보상 (수익률, 양수=성공, 음수=실패)
        """
        async with self._lock:
            if strategy_name in self._arms:
                self._arms[strategy_name].update(reward)

    async def bulk_update(self, outcomes: Dict[str, float]) -> None:
        """여러 전략 결과를 한 번에 반영.

        Args:
            outcomes: {전략이름: 보상} 딕셔너리
        """
        async with self._lock:
            for name, reward in outcomes.items():
                if name in self._arms:
                    self._arms[name].update(reward)

    def get_weights(self) -> Dict[str, float]:
        """현재 전략별 기대 보상을 정규화한 가중치 반환.

        Returns:
            Dict[str, float]: {전략이름: 정규화 가중치} (합계 ≈ 1.0)
        """
        means = {name: arm.mean_reward for name, arm in self._arms.items()}
        total = sum(means.values())
        if total <= 0:
            n = len(self._arms)
            return {name: 1.0 / n for name in self._arms}
        return {name: v / total for name, v in means.items()}

    def get_stats(self) -> List[Dict]:
        """전략별 상세 통계 반환.

        Returns:
            List[Dict]: 각 전략의 alpha, beta, mean_reward, uncertainty 등
        """
        result = []
        for arm in self._arms.values():
            result.append({
                "name": arm.name,
                "alpha": round(arm.alpha, 4),
                "beta": round(arm.beta, 4),
                "mean_reward": round(arm.mean_reward, 4),
                "uncertainty": round(arm.uncertainty, 4),
                "total_plays": arm.total_plays,
                "avg_reward": (
                    round(arm.total_reward / arm.total_plays, 4)
                    if arm.total_plays > 0 else 0.0
                ),
            })
        return sorted(result, key=lambda x: x["mean_reward"], reverse=True)

    def get_recommended_strategy(self) -> Tuple[str, float]:
        """현재 가장 높은 기대 보상 전략 반환 (탐욕적 선택, 탐색 없음).

        Returns:
            Tuple[str, float]: (전략이름, 기대보상)
        """
        if not self._arms:
            return "", 0.0
        best = max(self._arms.values(), key=lambda a: a.mean_reward)
        return best.name, best.mean_reward

    def to_dict(self) -> dict:
        """전체 상태 직렬화 (DB 저장용).

        Returns:
            dict: 직렬화된 상태
        """
        return {
            "total_selections": self._total_selections,
            "arms": {name: arm.to_dict() for name, arm in self._arms.items()},
        }

    @classmethod
    def from_dict(cls, d: dict, decay: float = 0.99) -> "StrategyBandit":
        """DB에서 복구.

        Args:
            d: to_dict()로 직렬화된 딕셔너리
            decay: 복구 시 망각 인자

        Returns:
            StrategyBandit: 복구된 인스턴스
        """
        arms_data = d.get("arms", {})
        bandit = cls(strategy_names=[], decay=decay)
        bandit._arms = {
            name: BanditArm.from_dict(arm_d)
            for name, arm_d in arms_data.items()
        }
        bandit._total_selections = d.get("total_selections", 0)
        return bandit
