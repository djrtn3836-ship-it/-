"""
analytics/calibration_analyzer.py - v2.0 FINAL (자동 임계값 튜닝 + 설정 파일 생성)
"""

import sys
import os
import json
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import statistics
from datetime import datetime, timedelta
from typing import Dict, List

from core.logger import setup_logger
logger = setup_logger("calibration")

TRACE_FILE = PROJECT_ROOT / "logs" / "debug" / "debug_trace.jsonl"
CONFIG_FILE = PROJECT_ROOT / "config" / "calibration_config.json"
REPORT_DIR = PROJECT_ROOT / "logs" / "calibration"


class CalibrationAnalyzer:
    def __init__(self):
        self.trace_file = TRACE_FILE
        self.config_file = CONFIG_FILE
        self.report_dir = REPORT_DIR
        self.report_dir.mkdir(parents=True, exist_ok=True)

    def load_traces(self, hours: int = 72) -> List[Dict]:
        if not self.trace_file.exists():
            return []
        cutoff = datetime.now() - timedelta(hours=hours)
        traces = []
        try:
            with open(self.trace_file, 'r', encoding='utf-8') as f:
                for line in f:
                    try:
                        entry = json.loads(line)
                        ts_str = entry.get('ts', '').replace('Z', '').replace('+00:00', '')
                        if ts_str:
                            ts = datetime.fromisoformat(ts_str)
                            if ts >= cutoff:
                                traces.append(entry)
                    except:
                        continue
        except Exception as e:
            logger.error(f"트레이스 로드 오류: {e}")
        return traces

    def generate_config(self, hours: int = 72) -> Dict:
        traces = self.load_traces(hours)
        fill_ratios = []
        for entry in traces:
            details = entry.get('details', {})
            if 'fill_ratio' in details and isinstance(details['fill_ratio'], (int, float)):
                fill_ratios.append(details['fill_ratio'])

        if len(fill_ratios) < 10:
            logger.warning(f"⚠️ 데이터 부족 ({len(fill_ratios)}개) → 기본값 유지")
            return self._get_default_config()

        sorted_ratios = sorted(fill_ratios)
        reject_idx = max(0, int(len(sorted_ratios) * 0.20) - 1)
        reject_val = sorted_ratios[reject_idx]
        reduce_val = statistics.median(sorted_ratios)

        reject_val = max(0.10, min(0.50, reject_val))
        reduce_val = max(0.40, min(0.90, reduce_val))
        if reject_val >= reduce_val:
            reject_val = max(0.10, reduce_val - 0.15)

        config = {
            "FILL_RATIO_REJECT": round(reject_val, 3),
            "FILL_RATIO_REDUCE": round(reduce_val, 3),
            "ORDER_VOLUME_RATIO": 0.008,
            "ORDER_VOLUME_MIN": 10,
            "ORDER_VOLUME_MAX": 500,
            "sample_count": len(fill_ratios),
            "last_updated": datetime.now().isoformat()
        }

        try:
            self.config_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            logger.info(f"✅ Calibration 설정 저장 완료: REJECT={config['FILL_RATIO_REJECT']:.1%}, REDUCE={config['FILL_RATIO_REDUCE']:.1%}")
        except Exception as e:
            logger.error(f"설정 저장 실패: {e}")

        return config

    def _get_default_config(self) -> Dict:
        return {
            "FILL_RATIO_REJECT": 0.30,
            "FILL_RATIO_REDUCE": 0.70,
            "ORDER_VOLUME_RATIO": 0.008,
            "ORDER_VOLUME_MIN": 10,
            "ORDER_VOLUME_MAX": 500,
            "sample_count": 0,
            "last_updated": "2026-08-18 (default)"
        }


def main():
    analyzer = CalibrationAnalyzer()
    analyzer.generate_config(hours=72)


if __name__ == "__main__":
    main()