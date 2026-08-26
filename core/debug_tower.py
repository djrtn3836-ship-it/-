"""
core/debug_tower.py - v2.2.1 (쓰로틀링 TTL 정리)
- _last_crash_time에서 1시간 이상 지난 항목 자동 정리
- 메모리 누수 방지
"""

import gzip
import json
import shutil
import threading
import time
import traceback
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path


def _load_debug_config() -> dict:
    config_path = Path(__file__).parent.parent / "config" / "debug_config.yaml"
    default = {
        "ring_buffer_size": 10000,
        "trace_file_max_mb": 50,
        "crash_snapshot_ttl_days": 7,
        "include_system_state": True,
    }
    if config_path.exists():
        try:
            import yaml

            with open(config_path, encoding="utf-8") as f:
                config = yaml.safe_load(f)
                debug_cfg = config.get("debug_tower", {})
                return {
                    "ring_buffer_size": debug_cfg.get("ring_buffer_size", default["ring_buffer_size"]),
                    "trace_file_max_mb": debug_cfg.get("trace_file_max_mb", default["trace_file_max_mb"]),
                    "crash_snapshot_ttl_days": debug_cfg.get(
                        "crash_snapshot_ttl_days", default["crash_snapshot_ttl_days"]
                    ),
                    "include_system_state": debug_cfg.get("include_system_state", default["include_system_state"]),
                }
        except Exception as e:
            print(f"⚠️ debug_config.yaml 로드 실패: {e}, 기본값 사용")
    return default


_DEBUG_CONFIG = _load_debug_config()


class DebugTower:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._init()
        return cls._instance

    def _init(self):
        self.base_dir = Path(__file__).parent.parent / "logs"
        self.trace_dir = self.base_dir / "debug"
        self.crash_dir = self.base_dir / "crashes"
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        self.crash_dir.mkdir(parents=True, exist_ok=True)

        self._ring_buffer_size = _DEBUG_CONFIG.get("ring_buffer_size", 10000)
        self._ring_buffer: deque = deque(maxlen=self._ring_buffer_size)

        self.trace_file = self.trace_dir / "debug_trace.jsonl"
        self.trace_file.touch(exist_ok=True)

        self._clean_old_crashes(days=_DEBUG_CONFIG.get("crash_snapshot_ttl_days", 7))

        self._write_buffer = []
        self._buffer_size = 50
        self._flush_lock = threading.Lock()

        self._include_system_state = _DEBUG_CONFIG.get("include_system_state", True)

        self._last_crash_time: dict[str, float] = {}
        self._crash_throttle_seconds = 300

    def _clean_old_crashes(self, days: int = 7):
        cutoff = datetime.now() - timedelta(days=days)
        for f in self.crash_dir.glob("crash_*.log"):
            try:
                mtime = datetime.fromtimestamp(f.stat().st_mtime)
                if mtime < cutoff:
                    f.unlink()
            except Exception:
                pass

    # 🔥 P1-10: 쓰로틀링 캐시 TTL 정리
    def _clean_throttle_cache(self):
        """1시간 이상 지난 쓰로틀링 항목 제거"""
        now = time.time()
        expired = [k for k, t in self._last_crash_time.items() if now - t > 3600]
        for k in expired:
            del self._last_crash_time[k]

    def _flush_buffer(self):
        with self._flush_lock:
            if not self._write_buffer:
                return
            try:
                with open(self.trace_file, "a", encoding="utf-8") as f:
                    for entry in self._write_buffer:
                        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                self._write_buffer.clear()
            except Exception:
                pass

    def _rotate_trace(self):
        max_mb = _DEBUG_CONFIG.get("trace_file_max_mb", 50)
        if self.trace_file.stat().st_size > max_mb * 1024 * 1024:
            try:
                backup_name = f"debug_trace_{int(time.time())}.jsonl.gz"
                backup_path = self.trace_dir / backup_name
                with open(self.trace_file, "rb") as f_in:
                    with gzip.open(backup_path, "wb") as f_out:
                        shutil.copyfileobj(f_in, f_out)
                self.trace_file.unlink()
                self.trace_file.touch()
                backups = sorted(self.trace_dir.glob("debug_trace_*.jsonl.gz"))
                for old in backups[:-5]:
                    old.unlink()
            except Exception:
                pass

    def _get_system_state(self) -> dict:
        state = {}
        if not self._include_system_state:
            return state
        try:
            import psutil

            state["cpu_percent"] = psutil.cpu_percent(interval=0.1)
            state["memory_percent"] = psutil.virtual_memory().percent
            state["memory_used_mb"] = psutil.virtual_memory().used / (1024 * 1024)
            state["active_threads"] = threading.active_count()
        except ImportError:
            state["active_threads"] = threading.active_count()
        except Exception:
            pass
        return state

    def log(self, ticker: str, event: str, details: dict, trace_id: str | None = None):
        if trace_id is None:
            trace_id = f"{ticker}_{int(time.time()*1000)}"

        entry = {
            "ts": datetime.now().isoformat(timespec="milliseconds"),
            "trace_id": trace_id,
            "ticker": ticker,
            "event": event,
            "details": details,
        }

        self._ring_buffer.append(entry)
        self._write_buffer.append(entry)
        if len(self._write_buffer) >= self._buffer_size:
            self._flush_buffer()

        if len(self._write_buffer) % 100 == 0:
            self._rotate_trace()

    def capture_snapshot(self, ticker: str, error: Exception, trace_id: str | None = None):
        # 🔥 P1-10: 쓰로틀링 캐시 정리 (1시간 초과 항목 제거)
        self._clean_throttle_cache()

        now = time.time()
        last_time = self._last_crash_time.get(ticker, 0)
        if now - last_time < self._crash_throttle_seconds:
            # logger가 없을 수 있으므로 print로 대체
            print(f"⏳ {ticker} 크래시 스냅샷 쓰로틀링 (5분 내 중복 방지)")
            return None

        self._last_crash_time[ticker] = now

        if trace_id is None:
            trace_id = f"{ticker}_{int(now*1000)}"

        related = []
        for e in self._ring_buffer:
            if e.get("ticker") == ticker or e.get("trace_id") == trace_id:
                related.append(e)

        system_state = self._get_system_state()
        system_state["ring_buffer_size"] = len(self._ring_buffer)
        system_state["trace_file_size"] = self.trace_file.stat().st_size if self.trace_file.exists() else 0

        snapshot = {
            "timestamp": datetime.now().isoformat(timespec="milliseconds"),
            "ticker": ticker,
            "trace_id": trace_id,
            "error_type": type(error).__name__,
            "error_msg": str(error),
            "traceback": traceback.format_exc(),
            "system_state": system_state,
            "recent_events": related[-50:],
        }

        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = self.crash_dir / f"crash_{ticker}_{timestamp_str}.log"
        try:
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(snapshot, f, indent=2, ensure_ascii=False)
            return str(filename)
        except Exception:
            return None

    def get_trace(self, trace_id: str) -> list[dict]:
        return [e for e in self._ring_buffer if e.get("trace_id") == trace_id]

    def flush(self):
        self._flush_buffer()

    def get_stats(self) -> dict:
        return {
            "ring_buffer_size": len(self._ring_buffer),
            "ring_buffer_max": self._ring_buffer_size,
            "trace_file_size": self.trace_file.stat().st_size if self.trace_file.exists() else 0,
            "crash_files": len(list(self.crash_dir.glob("crash_*.log"))),
            "write_buffer_size": len(self._write_buffer),
            "include_system_state": self._include_system_state,
        }


debug_tower = DebugTower()
