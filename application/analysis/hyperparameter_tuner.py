# -*- coding: utf-8 -*-
"""
application/analysis/hyperparameter_tuner.py - Optuna 기반 전략 파라미터 자동 튜닝 v1.0

개요:
    V10 Signal Pipeline의 핵심 파라미터들을 Optuna TPE 샘플러로 자동 최적화.
    백테스트 기록(HistoricalSample 목록)을 기반으로 목적 함수를 최소화/최대화.

최적화 대상 파라미터:
    ┌─────────────────────────────┬──────────┬──────────────────────────────┐
    │ 파라미터                    │ 범위     │ 설명                          │
    ├─────────────────────────────┼──────────┼──────────────────────────────┤
    │ buy_threshold               │ 0.55~0.75│ BUY 액션 임계값               │
    │ sell_threshold              │ 0.25~0.45│ SELL 액션 임계값              │
    │ min_confidence              │ 0.35~0.55│ HOLD 강제 최소 신뢰도         │
    │ sqi_v2_momentum_w           │ 0.15~0.45│ SQI v2 모멘텀 가중치         │
    │ sqi_v2_confidence_w         │ 0.25~0.55│ SQI v2 신뢰도 가중치         │
    │ trend_weight                │ 0.20~0.60│ TrendStrategy 앙상블 가중치   │
    │ reversal_weight             │ 0.15~0.45│ ReversalStrategy 가중치       │
    │ breakout_weight             │ 0.10~0.40│ BreakoutStrategy 가중치       │
    └─────────────────────────────┴──────────┴──────────────────────────────┘

목적 함수 (최대화):
    objective = w_sr × sharpe_ratio
              + w_wr × win_rate
              - w_dd × max_drawdown
              - w_hold × hold_rate_penalty

사용 방법:
    dataset = [HistoricalSample(action, actual_return, sqi, confidence), ...]
    tuner = HyperparameterTuner(n_trials=100)
    result = tuner.optimize(dataset)
    print(result.best_params)
    print(result.best_value)
"""

import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import optuna

optuna.logging.set_verbosity(optuna.logging.WARNING)  # suppress trial logs

from core.logger import setup_logger

logger = setup_logger("hyperparameter_tuner")

# ─── 목적 함수 가중치 ──────────────────────────────────────────────
_OBJ_SHARPE_W    = 0.40
_OBJ_WIN_RATE_W  = 0.35
_OBJ_DRAWDOWN_W  = 0.15
_OBJ_HOLD_PEN_W  = 0.10

# ─── 파라미터 탐색 범위 ────────────────────────────────────────────
PARAM_SPACE: Dict[str, Dict[str, Any]] = {
    "buy_threshold":       {"low": 0.55, "high": 0.75},
    "sell_threshold":      {"low": 0.25, "high": 0.45},
    "min_confidence":      {"low": 0.35, "high": 0.55},
    "sqi_v2_momentum_w":   {"low": 0.15, "high": 0.45},
    "sqi_v2_confidence_w": {"low": 0.25, "high": 0.55},
    "trend_weight":        {"low": 0.20, "high": 0.60},
    "reversal_weight":     {"low": 0.15, "high": 0.45},
    "breakout_weight":     {"low": 0.10, "high": 0.40},
}

# ─── 기본 n_trials ─────────────────────────────────────────────────
DEFAULT_N_TRIALS = 50


# ═══════════════════════════════════════════════════════════════════
#  데이터 DTO
# ═══════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class HistoricalSample:
    """단일 과거 트레이드 샘플.

    Attributes:
        action: 실행된 Action 문자열 ('BUY', 'SELL', 'HOLD')
        actual_return: 실제 수익률 (소수, 예: 0.05 = +5%)
        sqi: Signal Quality Index 값 (0~1)
        confidence: 신뢰도 (0~1)
        score: 앙상블 스코어 (0~1)
        buy_threshold: 이 샘플이 생성될 때 사용된 BUY 임계값
        sell_threshold: 이 샘플이 생성될 때 사용된 SELL 임계값
    """
    action: str
    actual_return: float
    sqi: float = 0.5
    confidence: float = 0.5
    score: float = 0.5
    buy_threshold: float = 0.62
    sell_threshold: float = 0.38


@dataclass
class TuningResult:
    """최적화 결과 DTO.

    Attributes:
        best_params: 최적 파라미터 딕셔너리
        best_value: 최적 목적 함수 값 (클수록 좋음)
        n_trials: 실행된 trial 수
        study_name: Optuna study 이름
        elapsed_sec: 최적화 소요 시간 (초)
        trial_history: 각 trial의 (trial_number, value) 목록
    """
    best_params: Dict[str, float]
    best_value: float
    n_trials: int
    study_name: str
    elapsed_sec: float
    trial_history: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """JSON 직렬화 가능한 딕셔너리로 변환."""
        return {
            "best_params": {k: round(v, 6) for k, v in self.best_params.items()},
            "best_value": round(self.best_value, 6),
            "n_trials": self.n_trials,
            "study_name": self.study_name,
            "elapsed_sec": round(self.elapsed_sec, 3),
            "trial_history": self.trial_history,
        }


# ═══════════════════════════════════════════════════════════════════
#  목적 함수 계산 (순수 함수 — 테스트 가능)
# ═══════════════════════════════════════════════════════════════════

def _simulate_actions(
    samples: List[HistoricalSample],
    buy_threshold: float,
    sell_threshold: float,
    min_confidence: float,
) -> List[Dict[str, Any]]:
    """파라미터 세트로 과거 샘플에 대한 행동을 재시뮬레이션합니다.

    실제 파이프라인의 _combine_scores + HOLD 강제 로직을 경량 재현.

    Args:
        samples: 과거 샘플 목록
        buy_threshold: BUY 판정 임계값
        sell_threshold: SELL 판정 임계값
        min_confidence: 최소 신뢰도 (이하 HOLD 강제)

    Returns:
        List[dict]: {action, actual_return} 목록
    """
    results = []
    for s in samples:
        action = s.action  # 기본값: 원본 action 유지
        # 신뢰도 미달 → HOLD 강제
        if s.confidence < min_confidence:
            action = "HOLD"
        # 임계값 재판정
        elif s.score >= buy_threshold:
            action = "BUY"
        elif s.score <= sell_threshold:
            action = "SELL"
        else:
            action = "HOLD"
        results.append({"action": action, "actual_return": s.actual_return})
    return results


def compute_objective(
    simulated: List[Dict[str, Any]],
) -> float:
    """시뮬레이션 결과로부터 목적 함수 값을 계산합니다.

    목적 함수 (최대화):
        objective = w_sr × sharpe_ratio
                  + w_wr × win_rate
                  - w_dd × max_drawdown
                  - w_hold × hold_rate_penalty

    Args:
        simulated: _simulate_actions() 결과 목록

    Returns:
        float: 목적 함수 값 (클수록 좋음)
    """
    if not simulated:
        return -1.0

    trades = [r for r in simulated if r["action"] in ("BUY", "SELL")]
    holds = [r for r in simulated if r["action"] == "HOLD"]
    total = len(simulated)

    # ── 거래 없을 때 패널티 ─────────────────────────────────────
    if not trades:
        return -1.0

    returns = [t["actual_return"] for t in trades]

    # ── 승률 ──────────────────────────────────────────────────
    win_rate = sum(1 for r in returns if r > 0) / len(returns)

    # ── Sharpe Ratio (무위험 이자율 0 가정) ───────────────────
    n = len(returns)
    mean_r = sum(returns) / n
    if n > 1:
        variance = sum((r - mean_r) ** 2 for r in returns) / (n - 1)
        std_r = math.sqrt(variance) if variance > 0 else 1e-9
    else:
        std_r = 1e-9
    sharpe = mean_r / std_r

    # ── 최대 낙폭 (누적 수익률 기준) ─────────────────────────
    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for r in returns:
        cumulative += r
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_drawdown:
            max_drawdown = dd

    # ── HOLD 비율 패널티 (너무 많이 HOLD 하면 감점) ──────────
    hold_rate = len(holds) / total
    hold_penalty = max(0.0, hold_rate - 0.50)  # 50% 초과분만 패널티

    # ── 목적 함수 합산 ─────────────────────────────────────────
    objective = (
        _OBJ_SHARPE_W * sharpe
        + _OBJ_WIN_RATE_W * win_rate
        - _OBJ_DRAWDOWN_W * max_drawdown
        - _OBJ_HOLD_PEN_W * hold_penalty
    )
    return float(objective)


# ═══════════════════════════════════════════════════════════════════
#  HyperparameterTuner
# ═══════════════════════════════════════════════════════════════════

class HyperparameterTuner:
    """Optuna TPE 샘플러 기반 전략 파라미터 자동 튜닝.

    Args:
        n_trials: 탐색할 trial 수 (기본 50)
        study_name: Optuna study 이름
        seed: 재현성을 위한 랜덤 시드 (None이면 무작위)
        sampler: Optuna 샘플러 (None이면 TPE 기본)
        pruner: Optuna 프루너 (None이면 MedianPruner)
    """

    def __init__(
        self,
        n_trials: int = DEFAULT_N_TRIALS,
        study_name: str = "v10_strategy_tuning",
        seed: Optional[int] = 42,
        sampler: Optional[optuna.samplers.BaseSampler] = None,
        pruner: Optional[optuna.pruners.BasePruner] = None,
    ) -> None:
        if n_trials <= 0:
            raise ValueError(f"n_trials must be > 0, got {n_trials}")
        self._n_trials = n_trials
        self._study_name = study_name
        self._seed = seed
        self._sampler = sampler or optuna.samplers.TPESampler(seed=seed)
        self._pruner = pruner or optuna.pruners.MedianPruner(n_startup_trials=5)
        self._last_result: Optional[TuningResult] = None

    def optimize(
        self,
        dataset: List[HistoricalSample],
        callbacks: Optional[List[Callable]] = None,
    ) -> TuningResult:
        """주어진 데이터셋으로 하이퍼파라미터를 최적화합니다.

        Args:
            dataset: 과거 트레이드 샘플 목록 (최소 10개 권장)
            callbacks: Optuna study에 추가할 콜백 목록 (None이면 없음)

        Returns:
            TuningResult: 최적 파라미터 및 성과 지표

        Raises:
            ValueError: dataset이 비어 있을 때
        """
        if not dataset:
            raise ValueError("dataset must not be empty")

        t_start = time.perf_counter()
        history: List[Dict[str, Any]] = []

        def _objective(trial: optuna.Trial) -> float:
            # ── 파라미터 제안 ──────────────────────────────────
            buy_th = trial.suggest_float(
                "buy_threshold",
                PARAM_SPACE["buy_threshold"]["low"],
                PARAM_SPACE["buy_threshold"]["high"],
            )
            sell_th = trial.suggest_float(
                "sell_threshold",
                PARAM_SPACE["sell_threshold"]["low"],
                PARAM_SPACE["sell_threshold"]["high"],
            )
            min_conf = trial.suggest_float(
                "min_confidence",
                PARAM_SPACE["min_confidence"]["low"],
                PARAM_SPACE["min_confidence"]["high"],
            )
            # SQI v2 가중치 (합이 ~1이 되도록 정규화)
            mom_w = trial.suggest_float(
                "sqi_v2_momentum_w",
                PARAM_SPACE["sqi_v2_momentum_w"]["low"],
                PARAM_SPACE["sqi_v2_momentum_w"]["high"],
            )
            conf_w = trial.suggest_float(
                "sqi_v2_confidence_w",
                PARAM_SPACE["sqi_v2_confidence_w"]["low"],
                PARAM_SPACE["sqi_v2_confidence_w"]["high"],
            )
            # 전략 가중치
            trend_w = trial.suggest_float(
                "trend_weight",
                PARAM_SPACE["trend_weight"]["low"],
                PARAM_SPACE["trend_weight"]["high"],
            )
            reversal_w = trial.suggest_float(
                "reversal_weight",
                PARAM_SPACE["reversal_weight"]["low"],
                PARAM_SPACE["reversal_weight"]["high"],
            )
            breakout_w = trial.suggest_float(
                "breakout_weight",
                PARAM_SPACE["breakout_weight"]["low"],
                PARAM_SPACE["breakout_weight"]["high"],
            )

            # ── buy > sell 제약 ─────────────────────────────────
            if buy_th <= sell_th:
                return -2.0

            # ── SQI v2 가중치 정규화 및 effective_min_confidence 조정 ──
            # 합이 1이 되도록 정규화 후 consensus 가중치를 역산
            total_sqi_w = mom_w + conf_w
            if total_sqi_w > 0:
                norm_conf_w = conf_w / total_sqi_w
            else:
                norm_conf_w = 0.5
            # 신뢰도 가중치가 높을수록 min_confidence를 소폭 상향 조정
            effective_min_conf = min_conf * (1.0 + 0.05 * (norm_conf_w - 0.5))
            effective_min_conf = max(0.30, min(0.60, effective_min_conf))

            # ── 전략 가중치 정규화 → 지배적 전략 결정 ──────────
            total_strategy_w = trend_w + reversal_w + breakout_w
            if total_strategy_w > 0:
                norm_trend_w = trend_w / total_strategy_w
            else:
                norm_trend_w = 1 / 3
            # 추세 전략 지배적이면 BUY 임계값을 소폭 낮춤 (더 공격적)
            adjusted_buy_th = buy_th - 0.02 * (norm_trend_w - 1 / 3)
            adjusted_buy_th = max(buy_th - 0.03, min(buy_th + 0.03, adjusted_buy_th))

            # ── 시뮬레이션 및 목적 함수 계산 ──────────────────
            simulated = _simulate_actions(
                dataset, adjusted_buy_th, sell_th, effective_min_conf
            )
            value = compute_objective(simulated)

            history.append({
                "trial": trial.number,
                "value": round(value, 6),
                "buy_threshold": round(buy_th, 4),
                "sell_threshold": round(sell_th, 4),
            })
            return value

        study = optuna.create_study(
            direction="maximize",
            study_name=self._study_name,
            sampler=self._sampler,
            pruner=self._pruner,
        )
        study.optimize(
            _objective,
            n_trials=self._n_trials,
            callbacks=callbacks or [],
            show_progress_bar=False,
        )

        elapsed = time.perf_counter() - t_start
        best = study.best_trial

        result = TuningResult(
            best_params=dict(best.params),
            best_value=best.value,
            n_trials=len(study.trials),
            study_name=self._study_name,
            elapsed_sec=elapsed,
            trial_history=history,
        )
        self._last_result = result

        logger.info(
            "[HyperparameterTuner] Done: study=%s trials=%d best=%.4f elapsed=%.2fs",
            self._study_name, result.n_trials, result.best_value, elapsed,
        )
        return result

    # ── 편의 메서드 ────────────────────────────────────────────────

    @property
    def last_result(self) -> Optional[TuningResult]:
        """마지막 optimize() 호출 결과. 아직 호출 안 됐으면 None."""
        return self._last_result

    @property
    def n_trials(self) -> int:
        """설정된 trial 수."""
        return self._n_trials

    @staticmethod
    def param_space() -> Dict[str, Dict[str, Any]]:
        """파라미터 탐색 범위 딕셔너리 반환 (읽기 전용 복사본)."""
        return dict(PARAM_SPACE)


# ═══════════════════════════════════════════════════════════════════
#  편의 함수
# ═══════════════════════════════════════════════════════════════════

def quick_tune(
    dataset: List[HistoricalSample],
    n_trials: int = 30,
    seed: int = 42,
) -> TuningResult:
    """빠른 튜닝용 편의 함수.

    Args:
        dataset: 과거 트레이드 샘플 목록
        n_trials: 탐색 횟수 (기본 30)
        seed: 랜덤 시드

    Returns:
        TuningResult
    """
    tuner = HyperparameterTuner(n_trials=n_trials, seed=seed)
    return tuner.optimize(dataset)
