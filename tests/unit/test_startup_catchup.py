"""기동 시 놓친 잡 자가 복구 테스트 (2026-10-15 Session 114).

사고: 윈도우 업데이트 재부팅(00:33) 후 로그인 전이라 앱이 18:44에야 시작 →
16:30 일봉·16:35 수급·17:05 정산을 통째로 놓침(하루 공백).
→ 기동 시 오늘 데이터 부재를 감지해 백그라운드 자가 복구.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

_ROOT = Path(__file__).resolve().parents[2]


class TestCatchupWiring:

    def _src(self):
        return (_ROOT / "app" / "bootstrap.py").read_text(encoding="utf-8")

    def test_task_created_at_startup(self):
        src = self._src()
        assert "self._catchup_task = asyncio.create_task(self._catchup_daily_jobs())" in src
        i = src.index("self._backup_if_stale()")
        j = src.index("self._catchup_task = asyncio.create_task", i)
        assert j > i, "자가 복구가 기동 배선에 없음"

    def test_guards_present(self):
        src = self._src()
        seg = src[src.index("async def _catchup_daily_jobs"):]
        seg = seg[: seg.index("async def _run_supply_demand")]
        assert "is_trading_day()" in seg, "거래일 가드 없음"
        assert "16 * 60 + 40" in seg and "16 * 60 + 45" in seg and "17 * 60 + 10" in seg
        assert "settled_today" in seg, "정산 중복 리포트 방지 가드 없음"
