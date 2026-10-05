# -*- coding: utf-8 -*-
"""infrastructure/kiwoom - 키움 연동 정식(V10) 경로

배경 (P12-1, 2026-10-04)
-----------------------
V10 DDD 마이그레이션에서 `infrastructure/kiwoom/`을 신설하기로 했으나
실제 구현이 옮겨지지 않아 **폴더 자체가 존재하지 않았다**.
그 결과 `app/bootstrap.py`의 아래 폴백에서 `try`가 **항상 실패**했고,

    try:
        from infrastructure.kiwoom import KiwoomConnectorV512
    except ImportError:
        from data.kiwoom_connector import KiwoomConnectorV512   # ← 실제 실행 경로

"레거시"로 문서화됐던 `data/kiwoom_connector.py`, `scanner/realtime_monitor.py`가
**조용히 실제 활성 코드**로 동작하고 있었다(문서 ↔ 실제 불일치).

본 패키지의 역할
---------------
- 공식 경로(`infrastructure.kiwoom.*`)를 **실체화**해 폴백을 정상화한다.
- **동작 변화 없음**: 같은 클래스 객체를 재수출한다(별칭 shim).
- 향후 실제 구현을 이 위치로 옮길 때 진입점이 이미 준비되어 있다.

주의: 여기서 정의를 복제하지 말 것 — 반드시 원본을 재수출해야
      두 경로가 서로 다른 클래스가 되는 사고(타입 불일치)를 막을 수 있다.
"""

from data.kiwoom_connector import AsyncRateLimiter, KiwoomConnectorV512

__all__ = ["KiwoomConnectorV512", "AsyncRateLimiter"]
