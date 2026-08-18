"""
core/debug_tower.py - 중앙 디버그 관제탑 v2.0
- Ring Buffer (메모리 순환)로 최근 로그 유지
- JSONL 형식으로 모든 디버그 이벤트 기록 (자동 압축 순환)
- 오류 발생 시 스냅샷(Snapshot) 생성: 오류 전후 컨텍스트를 단일 파일로 저장
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

class DebugTower:
    """싱글톤 디버그 관제탑"""
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

        # 링 버퍼 (최근 10000개 이벤트)
        self._ring_buffer: deque = deque(maxlen=10000)

        # 추적 로그 파일 경로
        self.trace_file = self.trace_dir / "debug_trace.jsonl"
        self.trace_file.touch(exist_ok=True)

        # 오래된 크래시 파일 정리 (7일 이상)
        self._clean_old_crashes(days=7)

        # 쓰기 버퍼 (성능 향상)
        self._write_buffer = []
        self._buffer_size = 50  # 50개 이벤트마다 플러시
        self._flush_lock = threading.Lock()

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
        """버퍼의 내용을 파일에 쓰기"""
        with self._flush_lock:
            if not self._write_buffer:
                return
            try:
                with open(self.trace_file, 'a', encoding='utf-8') as f:
                    for entry in self._write_buffer:
                        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                self._write_buffer.clear()
            except Exception as e:
                # 로깅 실패는 무시 (디버그 도구이므로)
                pass

    def _rotate_trace(self):
        """50MB 초과 시 압축 순환"""
        if self.trace_file.stat().st_size > 50 * 1024 * 1024:
            try:
                # 기존 파일을 압축 백업
                backup_name = f"debug_trace_{int(time.time())}.jsonl.gz"
                backup_path = self.trace_dir / backup_name
                with open(self.trace_file, 'rb') as f_in:
                    with gzip.open(backup_path, 'wb') as f_out:
                        shutil.copyfileobj(f_in, f_out)
                # 기존 파일 비우기
                self.trace_file.unlink()
                self.trace_file.touch()
                # 오래된 백업 삭제 (최대 5개 유지)
                backups = sorted(self.trace_dir.glob("debug_trace_*.jsonl.gz"))
                for old in backups[:-5]:
                    old.unlink()
            except Exception:
                pass

    def log(self, ticker: str, event: str, details: Dict, trace_id: Optional[str] = None):
        """
        디버그 이벤트 기록
        - ticker: 종목코드 또는 'SYSTEM'
        - event: 이벤트명 (예: 'WS_RECV', 'ANALYZE_START', 'DECISION')
        - details: 추가 데이터 (딕셔너리)
        - trace_id: 선택적 추적 ID (없으면 자동 생성)
        """
        if trace_id is None:
            trace_id = f"{ticker}_{int(time.time()*1000)}"

        entry = {
            "ts": datetime.now().isoformat(timespec='milliseconds'),
            "trace_id": trace_id,
            "ticker": ticker,
            "event": event,
            "details": details,
        }

        # 링 버퍼에 추가
        self._ring_buffer.append(entry)

        # 쓰기 버퍼에 추가
        self._write_buffer.append(entry)
        if len(self._write_buffer) >= self._buffer_size:
            self._flush_buffer()

        # 용량 체크 (주기적으로)
        if len(self._write_buffer) % 100 == 0:
            self._rotate_trace()

    def capture_snapshot(self, ticker: str, error: Exception, trace_id: Optional[str] = None):
        """
        오류 발생 시 스냅샷 생성
        - 관련 이벤트를 링 버퍼에서 추출하여 단일 파일로 저장
        """
        if trace_id is None:
            trace_id = f"{ticker}_{int(time.time()*1000)}"

        # 관련 로그 필터링 (해당 ticker 또는 trace_id와 일치)
        related = []
        for e in self._ring_buffer:
            if e.get("ticker") == ticker or e.get("trace_id") == trace_id:
                related.append(e)

        # 현재 시스템 상태 추가 (선택)
        system_state = {
            "ring_buffer_size": len(self._ring_buffer),
            "trace_file_size": self.trace_file.stat().st_size if self.trace_file.exists() else 0,
        }

        snapshot = {
            "timestamp": datetime.now().isoformat(timespec='milliseconds'),
            "ticker": ticker,
            "trace_id": trace_id,
            "error_type": type(error).__name__,
            "error_msg": str(error),
            "traceback": traceback.format_exc(),
            "system_state": system_state,
            "recent_events": related[-50:]  # 최근 50개
        }

        filename = self.crash_dir / f"crash_{ticker}_{int(time.time())}.log"
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(snapshot, f, indent=2, ensure_ascii=False)
            # 중요: 파일명을 반환하여 알림 등에 활용
            return str(filename)
        except Exception as e:
            # 스냅샷 저장 실패는 무시
            return None

    def get_trace(self, trace_id: str) -> List[Dict]:
        """특정 trace_id의 전체 이벤트 조회 (운영 중)"""
        return [e for e in self._ring_buffer if e.get("trace_id") == trace_id]

    def flush(self):
        """버퍼 강제 플러시 (프로그램 종료 전 호출 권장)"""
        self._flush_buffer()

    def get_stats(self) -> Dict:
        """관제탑 상태 정보"""
        return {
            "ring_buffer_size": len(self._ring_buffer),
            "trace_file_size": self.trace_file.stat().st_size if self.trace_file.exists() else 0,
            "crash_files": len(list(self.crash_dir.glob("crash_*.log"))),
            "write_buffer_size": len(self._write_buffer),
        }

# 전역 인스턴스
debug_tower = DebugTower()