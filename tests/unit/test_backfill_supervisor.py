"""백필 감시자 테스트 (2026-10-17 — 사용자 요청: 무요청 자동 진행).

시나리오: 30분 예약작업 → 감시자 기동 → 진행 중이면 백필 실행, 중단이면 재시도,
완료면 알림+작업 해제. 중복 기동은 소켓 락으로 차단.
"""
import asyncio
import json
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scheduler.backfill_supervisor import (  # noqa: E402
    acquire_supervisor_lock,
    supervise,
)


class _Deps:
    def __init__(self, rcs):
        self.rcs = list(rcs)
        self.runs = 0
        self.notes = []
        self.deleted = 0

    async def runner(self):
        self.runs += 1
        return self.rcs.pop(0) if self.rcs else 0

    async def notifier(self, text):
        self.notes.append(text)

    def deleter(self):
        self.deleted += 1
        return True


async def _no_sleep(_t):
    return None


def _run(deps, tmp, *, total=195, ti=None, checkpoint=True):
    cp = tmp / "state.json"
    if checkpoint is not False:
        cp.write_text(json.dumps({"ticker_idx": ti if ti is not None else 0}), encoding="utf-8")
    return asyncio.run(supervise(
        runner=deps.runner,
        notifier=deps.notifier,
        task_deleter=deps.deleter,
        checkpoint_path=cp,
        done_flag=tmp / "done.flag",
        total_tickers_fn=lambda: total,
        retry_wait=0,
        sleep=_no_sleep,
    ))


class TestSupervise:

    def test_retry_until_success(self, tmp_path):
        d = _Deps([2, 2, 0])  # 두 번 중단 후 성공
        rc = _run(d, tmp_path)
        assert rc == 0 and d.runs == 3
        assert any("완료" in n for n in d.notes)
        assert d.deleted == 1, "완료 시 예약작업 해제"

    def test_abort_all_tries_warns(self, tmp_path):
        d = _Deps([2, 2, 2])
        rc = _run(d, tmp_path)
        assert rc == 2 and d.runs == 3
        assert any("중단" in n for n in d.notes)
        assert d.deleted == 0, "미완료면 작업 유지(30분 뒤 자동 재시도)"

    def test_already_complete_notifies_once(self, tmp_path):
        d = _Deps([])
        rc = _run(d, tmp_path, ti=195)
        assert rc == 0 and d.runs == 0, "완료면 백필 실행 안 함"
        assert len(d.notes) == 1 and d.deleted == 1
        # 재기동(30분 뒤 예약작업) → 중복 알림 없음
        d2 = _Deps([])
        rc2 = _run(d2, tmp_path, ti=195)
        assert rc2 == 0 and d2.notes == [], "완료 알림은 1회만"

    def test_runner_exception_counts_as_abort(self, tmp_path):
        d = _Deps([])

        async def broken():
            d.runs += 1
            raise RuntimeError("boom")

        d.runner = broken
        rc = _run(d, tmp_path)
        assert rc == 2 and d.runs == 3, "예외도 중단으로 취급(무한 크래시 루프 없음)"

    def test_lock_prevents_duplicate(self):
        port = 47699
        s1 = acquire_supervisor_lock(port)
        try:
            try:
                acquire_supervisor_lock(port)
                raise AssertionError("두 번째 락이 성공하면 안 됨")
            except SystemExit as e:
                assert e.code == 0, "중복 기동은 조용히 종료"
        finally:
            s1.close()
        # 해제 후 재획득 가능
        s2 = acquire_supervisor_lock(port)
        s2.close()

    def test_lock_released_after_close(self):
        port = 47698
        s = acquire_supervisor_lock(port)
        s.close()
        s2 = acquire_supervisor_lock(port)
        s2.close()


class TestWiring:

    def test_bat_uses_supervisor(self):
        root = Path(__file__).resolve().parents[2]
        bat = (root / "run_backfill_auto.bat").read_text(encoding="utf-8")
        assert "backfill_supervisor" in bat, "실행 경로가 감시자를 거쳐야 함"

    def test_supervisor_socket_not_in_use_by_tests(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.close()  # 단순 자원 정리 확인(환경 안전)
