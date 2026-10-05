# -*- coding: utf-8 -*-
"""infrastructure.kiwoom.monitor - 실시간 감시 정식(V10) 경로 (P12-1)

`scanner/realtime_monitor.py`의 `RealtimeMonitor`를 재수출한다.

배경은 `infrastructure/kiwoom/__init__.py` 참고:
    폴더 부재로 `app/bootstrap.py`의 try가 항상 실패해
    scanner/ 경로가 조용히 실행되고 있었다.

⚠️ 여기서 클래스를 재정의하지 말 것(원본 재수출만).
"""

from scanner.realtime_monitor import RealtimeMonitor

__all__ = ["RealtimeMonitor"]
