"""JSONL 기록 파일 회전 테스트 (2026-10-09 사고 대응).

사고: calibration_predictions.jsonl 1.2GB / shadow_records.jsonl 818MB 무한 증가.
      로거 파일과 달리 JSONL 기록에는 회전 정책이 없었다.
"""
import gzip
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.jsonl_rotator import rotate_if_needed, _backup_path


def _write(path: Path, size: int) -> None:
    path.write_text("x" * size, encoding="utf-8")


def test_small_file_not_rotated(tmp_path):
    p = tmp_path / "a.jsonl"
    _write(p, 100)
    assert rotate_if_needed(p, max_bytes=1000) is False
    assert p.exists()
    assert not _backup_path(p, 1).exists()


def test_large_file_rotated_and_content_preserved(tmp_path):
    p = tmp_path / "a.jsonl"
    _write(p, 2000)
    assert rotate_if_needed(p, max_bytes=1000) is True
    assert not p.exists()
    gz = _backup_path(p, 1)
    assert gz.exists()
    with gzip.open(gz, "rt", encoding="utf-8") as f:
        assert len(f.read()) == 2000


def test_oldest_backup_deleted(tmp_path):
    p = tmp_path / "a.jsonl"
    for _ in range(4):
        _write(p, 2000)
        rotate_if_needed(p, max_bytes=1000, backups=2)
    assert _backup_path(p, 1).exists()
    assert _backup_path(p, 2).exists()
    assert not _backup_path(p, 3).exists(), "backups 초과분은 삭제되어야 함"


def test_missing_file_is_noop(tmp_path):
    p = tmp_path / "nope.jsonl"
    assert rotate_if_needed(p, max_bytes=1) is False


def test_env_defaults_positive():
    from core import jsonl_rotator as r
    assert r.DEFAULT_MAX_BYTES > 0
    assert r.DEFAULT_BACKUP_COUNT >= 1
