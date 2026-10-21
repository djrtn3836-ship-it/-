# -*- coding: utf-8 -*-
import gzip
import os
import time

from scripts.log_maintenance import run


def test_gzip_and_retention(tmp_path):
    old = tmp_path / "a.log.1"
    old.write_bytes(b"x" * 100)
    gzold = tmp_path / "old.log.1.gz"
    gzold.write_bytes(b"y" * 50)
    old_ts = time.time() - 100 * 86400
    os.utime(gzold, (old_ts, old_ts))
    cur = tmp_path / "a.log"
    cur.write_bytes(b"current")

    now = time.time()
    r = run(tmp_path, keep_days=90, gzip_min_bytes=10, now=now)
    assert r["gzip"] == 1 and r["deleted_gz"] == 1
    assert not old.exists() and (tmp_path / "a.log.1.gz").exists()
    assert cur.read_bytes() == b"current", "현재 로그는 보존"
    with gzip.open(tmp_path / "a.log.1.gz", "rb") as f:
        assert f.read() == b"x" * 100


def test_missing_dir(tmp_path):
    assert run(tmp_path / "nope")["skipped"] == "no_log_dir"
