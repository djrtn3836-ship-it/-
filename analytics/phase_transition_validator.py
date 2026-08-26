"""
Phase Transition Validator v5.1.2 — Claude 피드백 반영

Shadow → Paper 전환 7대 MUST 조건 자동 검증
- 최소 Shadow 기간: 28일
- 최소 신호 수: 50건
- 승률: 50% 이상
- Profit Factor: 1.3 이상
- MDD: 15% 이하
- FP Ratio: 30% 이하
- 시스템 다운타임: 2시간 이하

하나라도 미충족 시 자동 "Shadow 2주 연장"
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


@dataclass
class PhaseTransitionCriteria:
    """Phase 전환 조건"""

    min_days: int = 28
    min_signals: int = 50
    min_win_rate: float = 0.50
    min_profit_factor: float = 1.30
    max_mdd: float = 0.15
    max_fp_ratio: float = 0.30
    max_downtime_hours: float = 2.0


class PhaseTransitionValidator:
    """Phase 전환 검증기 (자동 Go/No-Go 판정)"""

    def __init__(self, criteria: PhaseTransitionCriteria | None = None):
        self.criteria = criteria or PhaseTransitionCriteria()
        self._validation_history: list[dict] = []

    def validate(self, shadow_data: dict) -> dict:
        """
        Shadow Mode 데이터 기반 Phase 전환 검증

        Args:
            shadow_data: {
                'start_date': str,
                'end_date': str,
                'total_signals': int,
                'win_rate': float,
                'profit_factor': float,
                'max_drawdown': float,
                'fp_ratio': float,
                'downtime_hours': float,
                'signals': List[Dict]
            }

        Returns:
            {
                'passed': bool,
                'results': Dict[str, bool],
                'details': Dict[str, any],
                'recommendation': str,
                'extend_days': int
            }
        """
        results = {}
        details = {}

        # 1. 최소 기간 검증
        start = datetime.fromisoformat(shadow_data.get("start_date", "2026-08-12"))
        end = datetime.fromisoformat(shadow_data.get("end_date", datetime.now().isoformat()))
        days = (end - start).days
        passed_days = days >= self.criteria.min_days
        results["min_days"] = passed_days
        details["min_days"] = {"actual": days, "required": self.criteria.min_days}

        # 2. 최소 신호 수 검증
        total_signals = shadow_data.get("total_signals", 0)
        passed_signals = total_signals >= self.criteria.min_signals
        results["min_signals"] = passed_signals
        details["min_signals"] = {"actual": total_signals, "required": self.criteria.min_signals}

        # 3. 승률 검증
        win_rate = shadow_data.get("win_rate", 0.0)
        passed_win_rate = win_rate >= self.criteria.min_win_rate
        results["min_win_rate"] = passed_win_rate
        details["min_win_rate"] = {"actual": f"{win_rate:.1%}", "required": f"{self.criteria.min_win_rate:.1%}"}

        # 4. Profit Factor 검증
        pf = shadow_data.get("profit_factor", 0.0)
        passed_pf = pf >= self.criteria.min_profit_factor
        results["min_profit_factor"] = passed_pf
        details["min_profit_factor"] = {"actual": pf, "required": self.criteria.min_profit_factor}

        # 5. MDD 검증
        mdd = shadow_data.get("max_drawdown", 1.0)
        passed_mdd = mdd <= self.criteria.max_mdd
        results["max_mdd"] = passed_mdd
        details["max_mdd"] = {"actual": f"{mdd:.1%}", "required": f"{self.criteria.max_mdd:.1%}"}

        # 6. FP Ratio 검증
        fp = shadow_data.get("fp_ratio", 1.0)
        passed_fp = fp <= self.criteria.max_fp_ratio
        results["max_fp_ratio"] = passed_fp
        details["max_fp_ratio"] = {"actual": f"{fp:.1%}", "required": f"{self.criteria.max_fp_ratio:.1%}"}

        # 7. 다운타임 검증
        downtime = shadow_data.get("downtime_hours", 0.0)
        passed_downtime = downtime <= self.criteria.max_downtime_hours
        results["max_downtime"] = passed_downtime
        details["max_downtime"] = {"actual": f"{downtime:.1f}h", "required": f"{self.criteria.max_downtime_hours:.1f}h"}

        # 8. 종합 판정
        all_passed = all(results.values())
        failed_items = [k for k, v in results.items() if not v]

        if all_passed:
            recommendation = "✅ Phase 2 (Paper Portfolio) 진입 승인"
            extend_days = 0
        else:
            # 자동 2주 연장
            extend_days = 14
            recommendation = f"❌ Phase 2 진입 불가 — {len(failed_items)}개 조건 미충족 (2주 연장)"
            logger.warning(f"Phase transition failed: {failed_items}")

        # 검증 이력 저장
        self._validation_history.append(
            {
                "timestamp": datetime.now().isoformat(),
                "passed": all_passed,
                "results": results,
                "failed_items": failed_items,
                "recommendation": recommendation,
                "extend_days": extend_days,
            }
        )

        return {
            "passed": all_passed,
            "results": results,
            "details": details,
            "failed_items": failed_items,
            "recommendation": recommendation,
            "extend_days": extend_days,
            "next_review_date": (datetime.now() + timedelta(days=extend_days)).isoformat() if not all_passed else None,
        }

    def get_history(self) -> list[dict]:
        """검증 이력 반환"""
        return self._validation_history

    def get_summary(self) -> dict:
        """검증 요약 반환"""
        if not self._validation_history:
            return {"status": "no_validation", "message": "아직 검증 실행되지 않음"}

        latest = self._validation_history[-1]
        return {
            "status": "PASS" if latest["passed"] else "FAIL",
            "latest_check": latest["timestamp"],
            "passed_count": sum(1 for h in self._validation_history if h["passed"]),
            "total_count": len(self._validation_history),
            "latest_recommendation": latest["recommendation"],
        }
