"""
observability/explainer_v2.py - v2.0 (Session 11)

SHAP-style Feature Attribution + Local/Global Explanation + Counterfactual
- Shapley 값 순수 Python 근사 (퍼뮤테이션 샘플링, 로컬 random.Random 인스턴스 사용
  → 전역 random 모듈 상태를 변경하지 않음)
- LocalExplanation: 개별 거래 결정의 기여 요인 분해
- GlobalExplanation: 최근 N건 평균 기준 피처 중요도 집계
- Counterfactual: "X가 delta만큼 증가/감소했다면 다른 결정이 됐을 것" 자동 탐색
- 자연어 설명 생성 (generate_narrative)
"""

import random
from collections import deque
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from core.logger import setup_logger

logger = setup_logger("explainer_v2")

# ─── 상수 ──────────────────────────────────────────────────────────
_MAX_SHAPLEY_SAMPLES = 50    # 퍼뮤테이션 샘플링 최대 횟수
_TOP_CONTRIBUTORS = 5        # LocalExplanation 상위 기여 요인 수
_DECISION_BOUNDARY = 0.5     # 결정 경계 (BUY/SELL 분기점)


# ═══════════════════════════════════════════════════════════════════
#  DTO
# ═══════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class FeatureContribution:
    """개별 피처의 기여도 DTO"""
    feature_name: str
    contribution_value: float
    direction: str          # "+" (긍정) 또는 "-" (부정)
    magnitude: float        # abs(contribution_value)

    def to_dict(self) -> dict:
        return {
            "feature_name": self.feature_name,
            "contribution_value": round(self.contribution_value, 6),
            "direction": self.direction,
            "magnitude": round(self.magnitude, 6),
        }


@dataclass(frozen=True)
class LocalExplanation:
    """단일 거래 결정에 대한 로컬 설명 DTO"""
    decision_id: str
    action: str                                     # "BUY" / "SELL" / "HOLD"
    final_score: float
    top_contributors: List[FeatureContribution]     # 상위 _TOP_CONTRIBUTORS개
    counterfactual: Optional[str]                   # "X가 Y였다면 Z 결정"
    confidence_gap: float                           # 결정 경계까지의 거리

    def to_dict(self) -> dict:
        return {
            "decision_id": self.decision_id,
            "action": self.action,
            "final_score": round(self.final_score, 4),
            "top_contributors": [c.to_dict() for c in self.top_contributors],
            "counterfactual": self.counterfactual,
            "confidence_gap": round(self.confidence_gap, 4),
        }


# ═══════════════════════════════════════════════════════════════════
#  순수 Python Shapley 근사 헬퍼
# ═══════════════════════════════════════════════════════════════════

def _permutation_shapley(
    features: Dict[str, float],
    score_fn: Callable[[Dict[str, float]], float],
    baseline: Optional[Dict[str, float]] = None,
    max_samples: int = _MAX_SHAPLEY_SAMPLES,
    seed: Optional[int] = None,
) -> Dict[str, float]:
    """
    퍼뮤테이션 샘플링 기반 Shapley 값 근사 (순수 Python).

    알고리즘:
        1. 피처 이름 목록을 무작위 순서로 섞은 퍼뮤테이션을 max_samples번 생성
        2. 각 퍼뮤테이션에서 피처 i를 추가하기 전/후의 스코어 차이를
           해당 피처의 한계 기여도로 계산
        3. 모든 퍼뮤테이션에 걸쳐 평균 → 근사 Shapley 값

    전역 random 모듈 상태를 변경하지 않기 위해 인스턴스 단위의
    random.Random(seed)를 사용합니다. seed가 None이면 매 호출마다
    OS 엔트로피로 초기화되어 실제 무작위 동작을 유지합니다.

    Args:
        features:    피처 이름 → 현재 값 딕셔너리
        score_fn:    피처 딕셔너리를 받아 스코어(float)를 반환하는 함수
        baseline:    기준 피처 값 (None이면 모든 피처를 0.0으로 대체)
        max_samples: 퍼뮤테이션 샘플 수
        seed:        재현성 시드 (None이면 무작위)

    Returns:
        Dict[str, float]: 피처 이름 → Shapley 값
    """
    if not features:
        return {}

    rng = random.Random(seed)

    feature_names = list(features.keys())
    base = baseline if baseline is not None else {k: 0.0 for k in feature_names}
    shapley: Dict[str, float] = {k: 0.0 for k in feature_names}

    for _ in range(max_samples):
        perm = feature_names[:]
        rng.shuffle(perm)

        current: Dict[str, float] = dict(base)
        prev_score = score_fn(current)

        for feat in perm:
            current[feat] = features[feat]
            new_score = score_fn(current)
            shapley[feat] += new_score - prev_score
            prev_score = new_score

    for k in shapley:
        shapley[k] /= max_samples

    return shapley


def _build_contributions(
    shapley_values: Dict[str, float],
    top_k: int = _TOP_CONTRIBUTORS,
) -> List[FeatureContribution]:
    """
    Shapley 값 딕셔너리 → FeatureContribution 리스트 변환.

    magnitude(절댓값) 내림차순으로 정렬 후 상위 top_k개 반환.
    """
    contributions = []
    for name, val in shapley_values.items():
        contributions.append(FeatureContribution(
            feature_name=name,
            contribution_value=round(val, 6),
            direction="+" if val >= 0 else "-",
            magnitude=round(abs(val), 6),
        ))
    contributions.sort(key=lambda c: c.magnitude, reverse=True)
    return contributions[:top_k]


def _compute_counterfactual(
    features: Dict[str, float],
    score_fn: Callable[[Dict[str, float]], float],
    final_score: float,
    action: str,
    delta: float = 0.05,
) -> Optional[str]:
    """
    가장 영향력 있는 피처를 delta만큼 변경했을 때 결정이 바뀌는지 확인.

    Args:
        features:    현재 피처 값
        score_fn:    스코어 함수
        final_score: 현재 최종 스코어
        action:      현재 결정 ("BUY"/"SELL"/"HOLD")
        delta:       반사실 변화량 (기본 0.05)

    Returns:
        반사실 설명 문자열 또는 None (반사실 없음)
    """
    if not features:
        return None

    best_feature: Optional[str] = None
    best_counterfactual_score: float = final_score
    best_direction: str = "+"

    for feat_name, feat_val in features.items():
        for sign in (+1, -1):
            modified = dict(features)
            modified[feat_name] = feat_val + sign * delta
            new_score = score_fn(modified)

            current_above = final_score >= _DECISION_BOUNDARY
            new_above = new_score >= _DECISION_BOUNDARY
            if current_above != new_above:
                change = abs(new_score - final_score)
                best_change = abs(best_counterfactual_score - final_score)
                if best_feature is None or change < best_change:
                    best_feature = feat_name
                    best_counterfactual_score = new_score
                    best_direction = "+" if sign > 0 else "-"

    if best_feature is None:
        return None

    direction_str = "증가" if best_direction == "+" else "감소"
    new_action = "BUY" if best_counterfactual_score >= _DECISION_BOUNDARY else "SELL/HOLD"
    return (
        f"{best_feature}가 {delta:.0%} {direction_str}했다면 "
        f"스코어 {final_score:.3f} → {best_counterfactual_score:.3f}로 변화하여 "
        f"{action} 대신 {new_action} 결정이 됐을 것입니다."
    )


# ═══════════════════════════════════════════════════════════════════
#  ExplainerV2
# ═══════════════════════════════════════════════════════════════════

class ExplainerV2:
    """
    SHAP-style 피처 기여도 설명기 v2.0.

    사용 예::

        def my_score_fn(features):
            return features.get("rsi", 50) / 100

        explainer = ExplainerV2()
        explanation = explainer.explain_local(
            features={"rsi": 72.0, "volume_ratio": 1.5, "macd_hist": 0.3},
            score_fn=my_score_fn,
            decision_id="DEC-001",
            action="BUY",
            final_score=0.72,
        )
        print(explainer.generate_narrative(explanation))
    """

    def __init__(
        self,
        max_shapley_samples: int = _MAX_SHAPLEY_SAMPLES,
        max_history: int = 500,
        seed: Optional[int] = None,
    ) -> None:
        """
        Args:
            max_shapley_samples: 퍼뮤테이션 샘플 수 (정확도 vs 속도 트레이드오프)
            max_history:         글로벌 설명 집계용 이력 보관 수
            seed:                재현성 시드
        """
        self._max_samples = max_shapley_samples
        self._seed = seed
        self._global_history: deque = deque(maxlen=max_history)

    def explain_local(
        self,
        features: Dict[str, float],
        score_fn: Callable[[Dict[str, float]], float],
        decision_id: str = "unknown",
        action: str = "HOLD",
        final_score: Optional[float] = None,
        baseline: Optional[Dict[str, float]] = None,
        counterfactual_delta: float = 0.05,
    ) -> LocalExplanation:
        """
        단일 결정에 대한 로컬 설명 생성.

        Args:
            features:             피처 이름 → 현재 값
            score_fn:              피처 딕셔너리 → 스코어 함수
            decision_id:           결정 식별자 (DB ID 등)
            action:                최종 결정 문자열
            final_score:           최종 스코어 (None이면 score_fn(features)로 계산)
            baseline:              기준 피처 값 (None이면 0.0)
            counterfactual_delta:  반사실 변화량

        Returns:
            LocalExplanation (예외 발생 시에도 안전한 fallback 반환)
        """
        try:
            if not features:
                return LocalExplanation(
                    decision_id=decision_id, action=action,
                    final_score=0.0, top_contributors=[],
                    counterfactual=None, confidence_gap=0.0,
                )

            score = final_score if final_score is not None else score_fn(features)
            score = max(0.0, min(1.0, float(score)))

            shapley = _permutation_shapley(
                features, score_fn, baseline,
                max_samples=self._max_samples, seed=self._seed,
            )
            contributors = _build_contributions(shapley, top_k=_TOP_CONTRIBUTORS)

            counterfactual = _compute_counterfactual(
                features, score_fn, score, action, delta=counterfactual_delta
            )

            confidence_gap = abs(score - _DECISION_BOUNDARY)

            self._global_history.append({
                "decision_id": decision_id,
                "shapley": shapley,
            })

            return LocalExplanation(
                decision_id=decision_id,
                action=action,
                final_score=round(score, 4),
                top_contributors=contributors,
                counterfactual=counterfactual,
                confidence_gap=round(confidence_gap, 4),
            )

        except Exception as e:
            logger.warning(f"explain_local 실패 [{decision_id}]: {e}")
            return LocalExplanation(
                decision_id=decision_id, action=action,
                final_score=0.0, top_contributors=[],
                counterfactual=None, confidence_gap=0.0,
            )

    def explain_global(self, n_recent: int = 100) -> Dict[str, float]:
        """
        최근 n_recent건의 로컬 설명을 집계하여 글로벌 피처 중요도 반환.

        Returns:
            Dict[str, float]: 피처 이름 → 평균 |Shapley| 값 (내림차순)
        """
        records = list(self._global_history)[-n_recent:]
        if not records:
            return {}

        aggregated: Dict[str, List[float]] = {}
        for rec in records:
            for feat, val in rec.get("shapley", {}).items():
                aggregated.setdefault(feat, []).append(abs(val))

        result = {feat: sum(vals) / len(vals) for feat, vals in aggregated.items()}
        return dict(sorted(result.items(), key=lambda x: x[1], reverse=True))

    def generate_narrative(self, explanation: LocalExplanation) -> str:
        """
        LocalExplanation을 사람이 읽기 쉬운 자연어 문장으로 변환.
        """
        if not explanation.top_contributors:
            return (
                f"결정: {explanation.action} (스코어 {explanation.final_score:.3f}) — "
                f"설명 가능한 피처 없음."
            )

        lines = [
            f"📊 결정 설명 [{explanation.decision_id}]",
            f"  결정: {explanation.action}  스코어: {explanation.final_score:.3f}  "
            f"경계까지 거리: {explanation.confidence_gap:.3f}",
            "  주요 기여 요인:",
        ]

        for i, c in enumerate(explanation.top_contributors, 1):
            bar_len = max(1, int(c.magnitude * 20))
            bar = "█" * bar_len
            lines.append(
                f"    {i}. {c.direction}{bar} {c.feature_name}: "
                f"{c.contribution_value:+.4f}"
            )

        if explanation.counterfactual:
            lines.append(f"  💡 반사실: {explanation.counterfactual}")

        return "\n".join(lines)

    def clear_history(self) -> None:
        """글로벌 집계 이력 초기화."""
        self._global_history.clear()

    @property
    def history_size(self) -> int:
        """현재 이력 크기."""
        return len(self._global_history)
