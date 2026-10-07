"""await 누락 회귀 테스트 (2026-10-07 실사고).

실사고: scanner/deep_analyzer.py에서 `self.decider.decide(...)`(async)를
await 없이 호출 → 실시간 분석 36,965건 전부
`'coroutine' object has no attribute 'get'`으로 실패 → decisions 0건.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))


def test_deep_analyzer_decide_is_awaited():
    src = (ROOT / "scanner" / "deep_analyzer.py").read_text(encoding="utf-8")
    assert "await self.decider.decide(" in src, "HybridDecider.decide는 async — await 필수"
    assert "decision = self.decider.decide(" not in src


def test_hybrid_decider_decide_is_async():
    from decision.hybrid_decider import HybridDecider
    import inspect
    assert inspect.iscoroutinefunction(HybridDecider.decide)


def test_source_checks_clean():
    """run_all의 전역 검사기가 현재 코드베이스에서 0건이어야 한다."""
    from run_all import check_sources
    assert check_sources() == 0
