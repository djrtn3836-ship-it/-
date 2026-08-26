"""
Calibration Tracker v5.1.2
Confidence Calibration Drift 감지 (Regime별 분리)
"""

from collections import defaultdict
from datetime import datetime

from core.logger import setup_logger

logger = setup_logger("calibration")


class CalibrationTracker:
    """Calibration 추적기 (Regime × Confidence 교차)"""

    def __init__(self):
        self.data: dict[str, list[dict]] = defaultdict(list)  # regime별 저장

    def record(self, regime: str, confidence: float, actual_win: bool):
        """Calibration 데이터 기록"""
        self.data[regime].append(
            {"confidence": confidence, "actual_win": actual_win, "timestamp": datetime.now().isoformat()}
        )

    def get_calibration(self, regime: str) -> dict:
        """Regime별 Calibration 계산"""
        records = self.data.get(regime, [])
        if len(records) < 10:
            return {"status": "insufficient_data", "sample": len(records)}

        # Confidence 구간별 승률 계산
        buckets = [(0.90, 1.00, []), (0.80, 0.89, []), (0.70, 0.79, []), (0.60, 0.69, []), (0.00, 0.59, [])]

        for record in records:
            conf = record["confidence"]
            for low, high, bucket in buckets:
                if low <= conf <= high:
                    bucket.append(record["actual_win"])
                    break

        result = {}
        for low, high, bucket in buckets:
            if bucket:
                win_rate = sum(bucket) / len(bucket)
                result[f"{low:.0%}-{high:.0%}"] = {
                    "sample": len(bucket),
                    "win_rate": win_rate,
                    "expected": (low + high) / 2,
                }

        # ECE 계산
        ece = 0.0
        total_samples = sum(v["sample"] for v in result.values())
        for bucket, data in result.items():
            ece += (data["sample"] / total_samples) * abs(data["win_rate"] - data["expected"])

        return {"regime": regime, "ece": ece, "buckets": result, "status": "PASS" if ece < 0.05 else "WARN"}
