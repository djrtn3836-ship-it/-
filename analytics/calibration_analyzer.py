"""
analytics/calibration_analyzer.py - Calibration 통계 분석기
- debug_trace.jsonl 파일을 분석하여 체결률, 슬리피지, 국면 전환 통계 산출
- 현재 시스템 파라미터(30%/70%, ATR 승수)의 적정성 평가
- 리포트 생성 (JSON + 사람이 읽기 쉬운 텍스트)

🔥 수정: sys.path 추가로 core 모듈 import 가능하도록 함
"""

import sys
import os
from pathlib import Path

# 🔥 프로젝트 루트를 PYTHONPATH에 추가 (core 모듈 import용)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from collections import Counter
import statistics

from core.logger import setup_logger

logger = setup_logger("calibration_analyzer")

TRACE_FILE = PROJECT_ROOT / "logs" / "debug" / "debug_trace.jsonl"
REPORT_DIR = PROJECT_ROOT / "logs" / "calibration"


class CalibrationAnalyzer:
    def __init__(self):
        self.trace_file = TRACE_FILE
        self.report_dir = REPORT_DIR
        self.report_dir.mkdir(parents=True, exist_ok=True)

    def load_traces(self, hours: int = 24) -> List[Dict]:
        """최근 N시간의 디버그 트레이스 로드"""
        if not self.trace_file.exists():
            logger.error(f"❌ 트레이스 파일 없음: {self.trace_file}")
            return []

        cutoff = datetime.now() - timedelta(hours=hours)
        traces = []

        try:
            with open(self.trace_file, 'r', encoding='utf-8') as f:
                for line in f:
                    try:
                        entry = json.loads(line)
                        ts_str = entry.get('ts', '')
                        if ts_str:
                            # ISO 형식 처리 (Z, +00:00 등 제거)
                            ts_str = ts_str.replace('Z', '').replace('+00:00', '')
                            ts = datetime.fromisoformat(ts_str)
                            if ts >= cutoff:
                                traces.append(entry)
                    except:
                        continue
        except Exception as e:
            logger.error(f"❌ 트레이스 로드 오류: {e}")

        logger.info(f"📂 {len(traces)}개 이벤트 로드 (최근 {hours}시간)")
        return traces

    def analyze_fill_ratios(self, traces: List[Dict]) -> Dict:
        """체결률 분포 분석"""
        fill_ratios = []
        fill_events = []

        for entry in traces:
            details = entry.get('details', {})
            if 'fill_ratio' in details:
                fill_ratios.append(details['fill_ratio'])
                fill_events.append({
                    'ticker': entry.get('ticker'),
                    'fill_ratio': details['fill_ratio'],
                    'slippage': details.get('slippage_bps', 0),
                    'ts': entry.get('ts'),
                })

        if not fill_ratios:
            return {"status": "insufficient_data", "count": 0}

        return {
            "count": len(fill_ratios),
            "mean": statistics.mean(fill_ratios),
            "median": statistics.median(fill_ratios),
            "p25": statistics.quantiles(fill_ratios, n=4)[0] if len(fill_ratios) >= 4 else 0,
            "p75": statistics.quantiles(fill_ratios, n=4)[2] if len(fill_ratios) >= 4 else 0,
            "under_30": sum(1 for x in fill_ratios if x < 0.30),
            "under_30_pct": sum(1 for x in fill_ratios if x < 0.30) / len(fill_ratios) * 100,
            "under_50": sum(1 for x in fill_ratios if x < 0.50),
            "under_50_pct": sum(1 for x in fill_ratios if x < 0.50) / len(fill_ratios) * 100,
            "over_70": sum(1 for x in fill_ratios if x >= 0.70),
            "over_70_pct": sum(1 for x in fill_ratios if x >= 0.70) / len(fill_ratios) * 100,
            "events": fill_events[:20],
        }

    def analyze_slippage(self, traces: List[Dict]) -> Dict:
        """슬리피지 분포 분석"""
        slippages = []
        for entry in traces:
            details = entry.get('details', {})
            if 'slippage_bps' in details:
                slippages.append(abs(details['slippage_bps']))

        if not slippages:
            return {"status": "insufficient_data", "count": 0}

        return {
            "count": len(slippages),
            "mean_bps": statistics.mean(slippages),
            "median_bps": statistics.median(slippages),
            "max_bps": max(slippages),
            "p90": statistics.quantiles(slippages, n=10)[8] if len(slippages) >= 10 else 0,
        }

    def analyze_regime_transitions(self, traces: List[Dict]) -> Dict:
        """국면 전환 빈도 분석"""
        regimes = []
        for entry in traces:
            details = entry.get('details', {})
            if 'regime' in details:
                regimes.append(details['regime'])

        if not regimes:
            return {"status": "insufficient_data", "count": 0}

        counts = Counter(regimes)

        return {
            "total_samples": len(regimes),
            "distribution": dict(counts),
            "bull_pct": counts.get('Bull', 0) / len(regimes) * 100,
            "bear_pct": counts.get('Bear', 0) / len(regimes) * 100,
            "sideways_pct": counts.get('Sideways', 0) / len(regimes) * 100,
        }

    def generate_recommendations(self, fill_stats: Dict, slippage_stats: Dict) -> List[str]:
        """Calibration 권장사항 생성"""
        recs = []

        if fill_stats.get('count', 0) > 10:
            p25 = fill_stats.get('p25', 0)
            under_30_pct = fill_stats.get('under_30_pct', 0)

            if under_30_pct > 40:
                recs.append(f"⚠️ 체결률 30% 미만 발생 {under_30_pct:.1f}% → 보류 임계값을 20%로 낮출 것을 권장")
            elif under_30_pct < 10:
                recs.append(f"✅ 체결률 30% 미만 발생 {under_30_pct:.1f}% → 현재 임계값(30%) 적정")

            if p25 > 0.5:
                recs.append(f"✅ 체결률 중위수 {fill_stats.get('median', 0):.1%} → 양호한 유동성")

        if slippage_stats.get('count', 0) > 10:
            mean_slip = slippage_stats.get('mean_bps', 0)
            if mean_slip > 20:
                recs.append(f"⚠️ 평균 슬리피지 {mean_slip:.1f}bp → 과도함. 주문량 비율(ORDER_VOLUME_RATIO)을 낮출 것을 권장")
            elif mean_slip < 5:
                recs.append(f"✅ 평균 슬리피지 {mean_slip:.1f}bp → 양호")

        if not recs:
            recs.append("✅ 추가 데이터 필요 (최소 50개 이상 체결 이벤트 필요)")

        return recs

    def run(self, hours: int = 24) -> Dict:
        """전체 분석 실행"""
        logger.info(f"🔬 Calibration 분석 시작 (최근 {hours}시간)")

        traces = self.load_traces(hours)
        if not traces:
            return {"status": "error", "message": "분석할 데이터 없음"}

        fill_stats = self.analyze_fill_ratios(traces)
        slippage_stats = self.analyze_slippage(traces)
        regime_stats = self.analyze_regime_transitions(traces)

        recommendations = self.generate_recommendations(fill_stats, slippage_stats)

        result = {
            "timestamp": datetime.now().isoformat(),
            "period_hours": hours,
            "total_events": len(traces),
            "fill_ratio": fill_stats,
            "slippage": slippage_stats,
            "regime": regime_stats,
            "recommendations": recommendations,
        }

        # 리포트 저장
        report_file = self.report_dir / f"calibration_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(report_file, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

        self._print_summary(result)

        logger.info(f"📄 리포트 저장: {report_file}")
        return result

    def _print_summary(self, result: Dict):
        """콘솔에 요약 출력"""
        print("\n" + "=" * 60)
        print("🔬 Calibration 분석 리포트")
        print("=" * 60)
        print(f"📊 분석 기간: 최근 {result['period_hours']}시간")
        print(f"📊 전체 이벤트: {result['total_events']}개")

        fill = result.get('fill_ratio', {})
        if fill.get('count', 0) > 0:
            print("\n📈 체결률 통계:")
            print(f"   샘플 수: {fill['count']}개")
            print(f"   평균: {fill.get('mean', 0):.1%}")
            print(f"   중위수: {fill.get('median', 0):.1%}")
            print(f"   25% 백분위: {fill.get('p25', 0):.1%}")
            print(f"   75% 백분위: {fill.get('p75', 0):.1%}")
            print(f"   ⚠️ 30% 미만: {fill.get('under_30_pct', 0):.1f}%")
            print(f"   ✅ 70% 이상: {fill.get('over_70_pct', 0):.1f}%")

        slip = result.get('slippage', {})
        if slip.get('count', 0) > 0:
            print(f"\n📉 슬리피지 통계:")
            print(f"   평균: {slip.get('mean_bps', 0):.1f}bp")
            print(f"   중위수: {slip.get('median_bps', 0):.1f}bp")
            print(f"   최대: {slip.get('max_bps', 0):.1f}bp")

        print("\n💡 권장사항:")
        for rec in result.get('recommendations', ['추가 데이터 필요']):
            print(f"   • {rec}")

        print("=" * 60)


def main():
    analyzer = CalibrationAnalyzer()
    analyzer.run(hours=24)


if __name__ == "__main__":
    main()