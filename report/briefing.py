"""종합 브리핑 리포트 (2026-10-10 Session 108).

"모든 요소를 한 뷰로" — 기존 리포트가 섹션별로 흩어져 있던 것을 하나의
읽기 쉬운 요약으로 조합한다.

- 아침(kind="morning", 평일 08:35): 장 시작 전 — 시장 상황·국면·감시 현황·
  데이터/시스템 상태·리스크. "오늘 무엇을 보고 시작할까"
- 저녁(kind="evening", 평일 17:25): 장 마감 후 — 오늘 신호/알림 요약·
  모의매매 채점·태그 승률·시스템 상태. "오늘 무슨 일이 있었나"

원칙: 각 섹션은 실패해도 나머지가 나온다(부분 브리핑 허용).
발송은 RAW 관리 알림(감사 로그 미기록 — alert_verifier 대조 오염 방지).
"""
from __future__ import annotations

import asyncio

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.logger import setup_logger

logger = setup_logger("briefing")

_MAX_LEN = 3600  # 텔레그램 안전 길이(4096 한도 여유)


def _pct(v: Any, digits: int = 2) -> str:
    try:
        return f"{float(v):+.{digits}f}%"
    except (TypeError, ValueError):
        return "—"


def _num(v: Any, digits: int = 1) -> str:
    try:
        return f"{float(v):.{digits}f}"
    except (TypeError, ValueError):
        return "—"


async def _market_section(db: Any) -> List[str]:
    out: List[str] = []
    try:
        from scheduler.macro_collector import get_cached_macro

        m = get_cached_macro()
        out.append("🌍 <b>시장</b>")
        out.append(
            f"  KOSPI 5일 {_pct(m.kospi_trend)} · 환율 {_num(m.usdkrw, 0)}원 · VIX {_num(m.vix, 1)}"
        )
        extra = []
        for label, attr in (("나스닥", "nasdaq"), ("니케이", "nikkei"), ("DXY", "dxy")):
            v = getattr(m, attr, None)
            if v is not None:
                extra.append(f"{label} {_num(v, 1)}")
        if extra:
            out.append("  " + " · ".join(extra))
        try:
            from analytics.market_breadth import (
                PANIC_NOTE,
                PANIC_THRESHOLD,
                compute_breadth,
                format_breadth,
                load_breadth_history,
                load_breadth_series,
            )

            b = compute_breadth(await asyncio.to_thread(load_breadth_series))
            hist = await asyncio.to_thread(load_breadth_history)
            line = format_breadth(b, hist)
            if line:
                out.append("  " + line)
            p20 = b.get("above_ma20_pct")
            if p20 is not None and float(p20) <= PANIC_THRESHOLD:
                out.append(f"  ⚠ 패닉권(≤{PANIC_THRESHOLD:.0f}%) — {PANIC_NOTE}")
            try:
                from analytics.market_zones import classify_zone, format_zone_line

                k5 = float(m.kospi_trend) if m.kospi_trend is not None else None
                b20 = float(p20) if p20 is not None else None
                z = classify_zone(k5, b20)
                out.append("  " + format_zone_line(z, k5, b20))
            except Exception as e:
                logger.debug(f"브리핑 구역 실패(무시): {e}")
        except Exception as e:
            logger.debug(f"브리핑 시장 폭 실패(무시): {e}")
    except Exception as e:
        logger.debug(f"브리핑 시장 섹션 실패(무시): {e}")
    return out


def _regime_section() -> List[str]:
    try:
        from core.regime_manager import regime_manager

        st = regime_manager.get_status()
        regime = st.get("current_regime") or st.get("regime") or "unknown"
        conf = st.get("confidence")
        line = f"🧭 <b>국면</b>: {regime}"
        if conf is not None:
            line += f" (신뢰도 {_num(float(conf) * 100 if float(conf) <= 1 else conf, 0)}%)"
        return [line]
    except Exception as e:
        logger.debug(f"브리핑 국면 섹션 실패(무시): {e}")
        return []


async def _signal_section(db: Any, day: str) -> List[str]:
    """오늘(저녁) 또는 어제(아침)의 신호 요약."""
    try:
        rows = await db.get_decisions_by_date(day)
    except Exception as e:
        logger.debug(f"브리핑 신호 섹션 실패(무시): {e}")
        return []
    if not rows:
        return [f"🔔 <b>신호</b>: {day} 기록 없음"]
    from collections import Counter

    counts = Counter(str(r.get("action", "")) for r in rows)
    tradable = counts.get("SIGNAL_ENTRY", 0) + counts.get("BUY", 0) + counts.get("SELL", 0)
    head = f"🔔 <b>신호</b> {len(rows)}건"
    if tradable:
        head += f" (매매신호 {tradable})"
    out = [head]
    top = [f"{a} {n}건" for a, n in counts.most_common(4) if a and a != "HOLD"]
    if top:
        out.append("  " + " · ".join(top))
    return out


def _paper_section(paper_trader: Any) -> List[str]:
    if paper_trader is None:
        return []
    try:
        s = paper_trader.summary(days=90)
        out = ["🧪 <b>모의매매</b> (최근 90일)"]
        horizons = s.get("by_horizon") or {}
        parts = []
        for h in ("1", "3", "5", "10"):
            hh = horizons.get(h) or horizons.get(int(h)) if isinstance(horizons, dict) else None
            if isinstance(hh, dict) and hh.get("n"):
                parts.append(f"{h}일 {_num(hh.get('win_rate'), 0)}%({int(hh['n'])})")
        if parts:
            out.append("  승률 " + " · ".join(parts))
        base = s.get("baseline", None)
        if base:
            out.append(f"  기준선(무작위 상승) {_num(base, 1)}%")
        pend = s.get("pending")
        if pend:
            out.append(f"  미확정 {pend}건")
        return out if len(out) > 1 else []
    except Exception as e:
        logger.debug(f"브리핑 모의매매 섹션 실패(무시): {e}")
        return []


def _tag_section(db_stats: Optional[Dict[str, Any]]) -> List[str]:
    if not db_stats:
        return []
    try:
        raw = db_stats.get("tag_win_rates") or []
        rows: List[Dict[str, Any]] = []
        if isinstance(raw, dict):  # {tag: {n, win_rate}} 형태도 허용
            for k, v in raw.items():
                if isinstance(v, dict):
                    rows.append({"tag": k, **v})
        elif isinstance(raw, list):
            rows = [r for r in raw if isinstance(r, dict)]
        best = sorted(
            [r for r in rows if int(r.get("n") or 0) >= 5],
            key=lambda r: int(r.get("n") or 0),
            reverse=True,
        )[:4]
        if not best:
            return []
        out = ["🏷️ <b>태그 승률</b>(n≥5)"]

        def _wr(r: Dict[str, Any]) -> str:
            try:
                w = float(r.get("win_rate"))
                if w <= 1.0:
                    w *= 100.0
                return f"{w:.0f}%"
            except (TypeError, ValueError):
                return "—"

        out.append("  " + " · ".join(f"{r.get('tag')} {_wr(r)}({r.get('n')})" for r in best))
        return out
    except Exception as e:
        logger.debug(f"브리핑 태그 섹션 실패(무시): {e}")
        return []


async def _system_section(db: Any) -> List[str]:
    out: List[str] = ["🖥️ <b>시스템</b>"]
    try:
        from core.disk_guard import disk_status

        st = disk_status()
        out.append(f"  디스크 여유 {st['free_gb']}GB ({st['free_pct']}%)")
    except Exception:
        pass
    try:
        from core.db_backup import last_backup_age_hours

        age = last_backup_age_hours()
        out.append(f"  마지막 백업 {_num(age, 1)}시간 전" if age is not None else "  백업 없음")
    except Exception:
        pass
    try:
        import os

        bp = Path(os.getenv("ALERT_BACKLOG_PATH", "logs/pending_alerts.jsonl"))
        if bp.exists():
            n = len([ln for ln in bp.read_text(encoding="utf-8").splitlines() if ln.strip()])
            if n:
                out.append(f"  ⚠️ 미전송 알림 {n}건")
    except Exception:
        pass
    try:
        total = 0
        for table, attr in (("decisions", "count_decisions"),):
            if hasattr(db, attr):
                total = int(await getattr(db, attr)() or 0)
        if total:
            gate = 500
            out.append(f"  학습 데이터 {total}건 (게이트 {gate})")
    except Exception:
        pass
    return out if len(out) > 1 else []


async def build_briefing(
    db: Any,
    *,
    kind: str = "morning",
    paper_trader: Any = None,
    exit_soon: Optional[List[str]] = None,
) -> str:
    """종합 브리핑 텍스트(HTML). 각 섹션 실패는 무시하고 부분 브리핑을 낸다."""
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    try:
        from core.holiday_utils import is_trading_day

        day_note = "거래일" if is_trading_day(now) else "비거래일"
    except Exception:
        day_note = ""

    if kind == "evening":
        header = f"🌙 <b>저녁 종합 브리핑</b> — {today} ({day_note})"
        day_for_signals = today
    else:
        header = f"☀️ <b>아침 종합 브리핑</b> — {today} ({day_note})"
        day_for_signals = (now - timedelta(days=1)).strftime("%Y-%m-%d")

    lines: List[str] = [header, "━━━━━━━━━━━━━━━━━━━━━"]
    lines += await _market_section(db)
    lines += _regime_section()
    lines += await _signal_section(db, day_for_signals)
    if exit_soon:
        lines.append("⏳ <b>오늘 청산 예정</b> (유형별 시간 만료)")
        for s_line in exit_soon[:8]:
            lines.append(f"  • {s_line}")
    lines += _paper_section(paper_trader)
    try:
        tag_rows = await db.get_tag_win_rates()
    except Exception:
        tag_rows = None
    lines += _tag_section({"tag_win_rates": tag_rows} if tag_rows else None)
    lines += await _system_section(db)

    text = "\n".join(lines)
    if len(text) > _MAX_LEN:
        text = text[: _MAX_LEN - 20] + "\n…(생략)"
    return text
