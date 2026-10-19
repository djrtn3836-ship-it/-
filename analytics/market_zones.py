# -*- coding: utf-8 -*-
"""analytics/market_zones.py — 시장 구역 판정 (S136 ③, 표시 전용).

    목적:
    "지금 사라/팔아라"가 아니라 **"지금 어떤 구역인가"**를 정확히 전달.
    구역 판정에 쓰는 규칙은 전부 **검증된 것만** 사용한다:
      - 진입금지: KOSPI 5일 ≤ -3% — SafetyGuard 급락 차단 기준(운영 검증, 리스크 규칙)
      - 기회:     폭 ≤15%(패닉권) — S132 검증(5일 +5.1%p·78%) + S136 조건 스캔에서
                  '폭≤15 · 폭≤15&하락중' 둘 다 생존(+5.2%p·p=0.001, 반기·연도 일관성).
                  단 2022 약세장 무효(국면 의존) 명시
      - 관찰:     폭 ≤25% 또는 KOSPI 5일 ≤ -1.5% (경계 근접)
      - 정상:     그 외
    판단 점수·주문·알림 필터는 불변(표시 전용 — 프로젝트 원칙).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

ZONE_BLOCK = "진입금지"
ZONE_WATCH = "관찰"
ZONE_OPPORTUNITY = "기회"
ZONE_NORMAL = "정상"

CRASH_5D = -3.0        # SafetyGuard kospi_drop 기준
PANIC_BREADTH = 15.0   # S132 검증 임계
WATCH_BREADTH = 25.0
WATCH_5D = -1.5

_PANIC_NOTE = "폭≤15%: 과거 18회 유사일 후 5일 +5.1%p(78%) · 2022년엔 무효"


def classify_zone(kospi_5d: Optional[float], breadth_pct: Optional[float]) -> Dict[str, Any]:
    """구역 판정. kospi_5d = KOSPI 5일 수익률(%), breadth_pct = MA20 상회 비율(%).

    규칙 순서(검증 기반 — S132 폭 검증 + S136 조건 스캔):
      1) KOSPI 5일 ≤ -3%        → 진입금지 (SafetyGuard 운영 기준 — 통계적 수익 근거가
                                  아니라 리스크 관리 규칙임을 명시)
      2) 폭 ≤ 15%               → 기회  (5일 +5.1%p·78%·p=0.001, 2022 약세장 무효.
                                  S136: '폭≤15&하락중'도 +5.21%p로 생존 — 하락 진행 중이라도
                                  패닉 폭이면 기회 후보, 단 1)이 우선)
      3) 폭 ≤ 25% 또는 5일 ≤ -1.5% → 관찰
      4) 그 외                   → 정상
    """
    if kospi_5d is not None and kospi_5d <= CRASH_5D:
        return {
            "zone": ZONE_BLOCK,
            "reasons": [f"KOSPI 5일 {kospi_5d:+.1f}% ≤ {CRASH_5D:.0f}% (급락 진행 — SafetyGuard 기준)"],
        }
    if breadth_pct is not None and breadth_pct <= PANIC_BREADTH:
        return {
            "zone": ZONE_OPPORTUNITY,
            "reasons": [f"폭 {breadth_pct:.0f}% ≤ {PANIC_BREADTH:.0f}% (패닉권) — {_PANIC_NOTE}"],
        }
    if (breadth_pct is not None and breadth_pct <= WATCH_BREADTH) or (
        kospi_5d is not None and kospi_5d <= WATCH_5D
    ):
        parts = []
        if breadth_pct is not None and breadth_pct <= WATCH_BREADTH:
            parts.append(f"폭 {breadth_pct:.0f}% ≤ {WATCH_BREADTH:.0f}%")
        if kospi_5d is not None and kospi_5d <= WATCH_5D:
            parts.append(f"KOSPI 5일 {kospi_5d:+.1f}%")
        return {"zone": ZONE_WATCH, "reasons": [" · ".join(parts) + " (경계 근접)"]}
    return {"zone": ZONE_NORMAL, "reasons": []}


def format_zone_line(z: Dict[str, Any], kospi_5d: Optional[float], breadth_pct: Optional[float]) -> str:
    """브리핑/대시보드용 한 줄."""
    icon = {ZONE_BLOCK: "⛔", ZONE_OPPORTUNITY: "🎯", ZONE_WATCH: "👀", ZONE_NORMAL: "✅"}.get(
        str(z.get("zone")), "•"
    )
    parts = []
    if breadth_pct is not None:
        parts.append(f"폭 {breadth_pct:.0f}%")
    if kospi_5d is not None:
        parts.append(f"KOSPI 5일 {kospi_5d:+.1f}%")
    ctx = f" ({' · '.join(parts)})" if parts else ""
    reasons = z.get("reasons") or []
    note = f" — {reasons[0]}" if reasons else ""
    return f"{icon} 구역: <b>{z.get('zone')}</b>{ctx}{note} (참고용)"
