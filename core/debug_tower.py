"""
core/debug_tower.py - v2.1 FINAL (동적 버퍼 + 시스템 상태 스냅샷)
- config/debug_config.yaml에서 링 버퍼 크기 동적 로드
- 크래시 스냅샷에 CPU/메모리/스레드 상태 포함 (psutil)
- 스냅샷 파일명에 ticker + timestamp 포함 (파일 관리 개선)
- JSONL 형식 로그 유지, 자동 압축 순환 유지
"""

import json
import gzip
import shutil
import time
import traceback
from collections import deque
from pathlib import Path
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, List
import threading
import os

# ============================================================
# 설정 파일 로드 (동적 버퍼 크기)
# ============================================================
def _load_debug_config() -> Dict:
    """config/debug_config.yaml 로드 (없으면 기본값)"""
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
            with open(config_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
                debug_cfg = config.get('debug_tower', {})
                return {
                    "ring_buffer_size": debug_cfg.get("ring_buffer_size", default["ring_buffer_size"]),
                    "trace_file_max_mb": debug_cfg.get("trace_file_max_mb", default["trace_file_max_mb"]),
                    "crash_snapshot_ttl_days": debug_cfg.get("crash_snapshot_ttl_days", default["crash_snapshot_ttl_days"]),
                    "include_system_state": debug_cfg.get("include_system_state", default["include_system_state"]),
                }
        except Exception as e:
            print(f"⚠️ debug_config.yaml 로드 실패: {e}, 기본값 사용")
    return default

_DEBUG_CONFIG = _load_debug_config()


class DebugTower:
    """싱글톤 디버그 관제탑 (v2.1)"""
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
        # 디렉토리 준비
        self.base_dir = Path(__file__).parent.parent / "logs"
        self.trace_dir = self.base_dir / "debug"
        self.crash_dir = self.base_dir / "crashes"
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        self.crash_dir.mkdir(parents=True, exist_ok=True)

        # 🔥 v2.1: 링 버퍼 크기 동적 설정
        self._ring_buffer_size = _DEBUG_CONFIG.get("ring_buffer_size", 10000)
        self._ring_buffer: deque = deque(maxlen=self._ring_buffer_size)

        # 추적 로그 파일 경로
        self.trace_file = self.trace_dir / "debug_trace.jsonl"
        self.trace_file.touch(exist_ok=True)

        # 오래된 크래시 파일 정리
        self._clean_old_crashes(days=_DEBUG_CONFIG.get("crash_snapshot_ttl_days", 7))

        # 쓰기 버퍼
        self._write_buffer = []
        self._buffer_size = 50
        self._flush_lock = threading.Lock()

        self._include_system_state = _DEBUG_CONFIG.get("include_system_state", True)

    def _clean_old_crashes(self, days: int = 7):
        """오래된 크래시 스냅샷 삭제"""
        cutoff = datetime.now() - timedelta(days=days)
        for f in self.crash_dir.glob("crash_*.log"):
            try:
                mtime = datetime.fromtimestamp(f.stat().st_mtime)
                if mtime < cutoff:
                    f.unlink()
            except Exception:
                pass

    def _flush_buffer(self):
        """버퍼 내용을 파일에 쓰기"""
        with self._flush_lock:
            if not self._write_buffer:
                return
            try:
                with open(self.trace_file, 'a', encoding='utf-8') as f:
                    for entry in self._write_buffer:
                        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                self._write_buffer.clear()
            except Exception:
                pass

    def _rotate_trace(self):
        """trace_file_max_mb 초과 시 압축 순환"""
        max_mb = _DEBUG_CONFIG.get("trace_file_max_mb", 50)
        if self.trace_file.stat().st_size > max_mb * 1024 * 1024:
            try:
                backup_name = f"debug_trace_{int(time.time())}.jsonl.gz"
                backup_path = self.trace_dir / backup_name
                with open(self.trace_file, 'rb') as f_in:
                    with gzip.open(backup_path, 'wb') as f_out:
                        shutil.copyfileobj(f_in, f_out)
                self.trace_file.unlink()
                self.trace_file.touch()
                # 오래된 백업 삭제 (최대 5개 유지)
                backups = sorted(self.trace_dir.glob("debug_trace_*.jsonl.gz"))
                for old in backups[:-5]:
                    old.unlink()
            except Exception:
                pass

    def _get_system_state(self) -> Dict:
        """시스템 상태 수집 (CPU/메모리/스레드) - v2.1 신규"""
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
            # psutil 없으면 기본 정보만
            state["active_threads"] = threading.active_count()
        except Exception:
            pass
        return state

    # ============================================================
    # 코어 메서드
    # ============================================================
    def log(self, ticker: str, event: str, details: Dict, trace_id: Optional[str] = None):
        """디버그 이벤트 기록"""
        if trace_id is None:
            trace_id = f"{ticker}_{int(time.time()*1000)}"

        entry = {
            "ts": datetime.now().isoformat(timespec='milliseconds'),
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

    def capture_snapshot(self, ticker: str, error: Exception, trace_id: Optional[str] = None):
        """
        오류 발생 시 스냅샷 생성 (v2.1: 시스템 상태 포함)
        """
        if trace_id is None:
            trace_id = f"{ticker}_{int(time.time()*1000)}"

        # 관련 로그 필터링
        related = []
        for e in self._ring_buffer:
            if e.get("ticker") == ticker or e.get("trace_id") == trace_id:
                related.append(e)

        # 시스템 상태 수집 (v2.1)
        system_state = self._get_system_state()
        system_state["ring_buffer_size"] = len(self._ring_buffer)
        system_state["trace_file_size"] = self.trace_file.stat().st_size if self.trace_file.exists() else 0

        snapshot = {
            "timestamp": datetime.now().isoformat(timespec='milliseconds'),
            "ticker": ticker,
            "trace_id": trace_id,
            "error_type": type(error).__name__,
            "error_msg": str(error),
            "traceback": traceback.format_exc(),
            "system_state": system_state,
            "recent_events": related[-50:]
        }

        # 🔥 v2.1: 파일명에 ticker + timestamp 포함
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = self.crash_dir / f"crash_{ticker}_{timestamp_str}.log"
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(snapshot, f, indent=2, ensure_ascii=False)
            return str(filename)
        except Exception:
            return None

    def get_trace(self, trace_id: str) -> List[Dict]:
        """특정 trace_id의 전체 이벤트 조회"""
        return [e for e in self._ring_buffer if e.get("trace_id") == trace_id]

    def flush(self):
        """버퍼 강제 플러시"""
        self._flush_buffer()

    def get_stats(self) -> Dict:
        """관제탑 상태 정보"""
        return {
            "ring_buffer_size": len(self._ring_buffer),
            "ring_buffer_max": self._ring_buffer_size,
            "trace_file_size": self.trace_file.stat().st_size if self.trace_file.exists() else 0,
            "crash_files": len(list(self.crash_dir.glob("crash_*.log"))),
            "write_buffer_size": len(self._write_buffer),
            "include_system_state": self._include_system_state,
        }

# 전역 인스턴스
debug_tower = DebugTower()