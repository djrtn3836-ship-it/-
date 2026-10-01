# -*- coding: utf-8 -*-
"""report/html_dashboard.py - 단일 파일 HTML 시스템 대시보드 (P6-4).

목적
----
P5/P6 검증 결론: **이 시스템의 가치는 알파 생성이 아니라 감시·리스크 알림**이다.
그 결론에 맞춰, 흩어져 있는 운영 지표를 **서버 없이 열리는 단일 HTML 파일**로
한 화면에 모은다(스케줄러가 주기적으로 재생성).

포함 섹션
---------
1. 시스템 상태   — 부팅/스케줄러 잡 수/유니버스 소스·신선도
2. 데이터 축적   — ohlcv 행수, decisions/outcomes 준비도(P7 게이트)
3. 감시·알림     — OpsMonitor 품질 지표 + 개선 힌트, 서킷브레이커 상태
4. 모멘텀 모의   — momentum_paper 누적 성과(참고 신호, 주문 아님)
5. 매크로        — 13개 지표 값 + 점수
6. 검증 결론     — P5/P6 결과 고정 표기(알파 없음 사실 명시)

사용:
    python -m report.html_dashboard
    python -m report.html_dashboard --out reports/dashboard.html
"""

from __future__ import annotations

import argparse
import html
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Sequence

from core.logger import setup_logger

logger = setup_logger("dashboard")

DEFAULT_OUT = Path(__file__).parent.parent / "reports" / "dashboard.html"


def _esc(v: Any) -> str:
    return html.escape(str(v), quote=True)


def _fmt(v: Any, digits: int = 2, suffix: str = "") -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:,.{digits}f}{suffix}"
    if isinstance(v, int):
        return f"{v:,}{suffix}"
    return _esc(v)


def _badge(text: str, level: str = "ok") -> str:
    return f'<span class="badge {level}">{_esc(text)}</span>'


# ============================================================
# 데이터 수집 (실패해도 대시보드는 생성되어야 한다)
# ============================================================

async def collect_system_section() -> Dict[str, Any]:
    out: Dict[str, Any] = {"universe_source": "unknown", "csv_age_days": None, "jobs": 13}
    try:
        from infrastructure.market_data.universe_provider import (
            get_csv_age_days,
            get_last_source,
        )

        out["universe_source"] = get_last_source()
        out["csv_age_days"] = get_csv_age_days()
    except Exception as e:
        logger.debug(f"유니버스 정보 수집 실패(무시): {e}")
    return out


async def collect_data_section(db: Any) -> Dict[str, Any]:
    try:
        from scheduler.data_readiness_monitor import check_data_readiness

        return await check_data_readiness(db)
    except Exception as e:
        logger.warning(f"데이터 준비도 수집 실패: {e}")
        return {}


async def collect_paper_section(db: Any) -> Dict[str, Any]:
    try:
        return await db.get_momentum_paper_stats()
    except Exception as e:
        logger.debug(f"모의 추적 통계 수집 실패(무시): {e}")
        return {}


def collect_monitor_section() -> Dict[str, Any]:
    out: Dict[str, Any] = {"quality": {}, "circuit_breakers": {}}
    try:
        from observability.ops_monitor import get_ops_snapshot

        out["quality"] = (get_ops_snapshot() or {}).get("quality", {})
    except Exception as e:
        logger.debug(f"OpsMonitor 스냅샷 실패(무시): {e}")
    try:
        from risk.market_risk_monitor import get_circuit_breaker_manager

        mgr = get_circuit_breaker_manager()
        if mgr is not None:
            st = mgr.status()
            out["circuit_breakers"] = {
                "is_open": bool(st.is_open),
                "updated_at": str(getattr(st, "updated_at", "")),
            }
    except Exception as e:
        logger.debug(f"서킷브레이커 상태 실패(무시): {e}")
    return out


def collect_macro_section() -> Dict[str, Any]:
    try:
        from filters.macro_filter import MacroFilter

        result = MacroFilter().check({})
        return {"score": result.get("score", 0.0), "indicators": result.get("indicators", {})}
    except Exception as e:
        logger.debug(f"매크로 섹션 실패(무시): {e}")
        return {}


# ============================================================
# 렌더링
# ============================================================

_CSS = """
:root{--bg:#0f1116;--card:#171a21;--fg:#e6e8ee;--mut:#949aa8;--ok:#2ecc71;--warn:#f5a623;--bad:#e74c3c;--acc:#4a90e2}
*{box-sizing:border-box}body{margin:0;padding:24px;background:var(--bg);color:var(--fg);
font-family:'Segoe UI',-apple-system,'Malgun Gothic',sans-serif;font-size:14px}
h1{font-size:20px;margin:0 0 4px}h2{font-size:15px;margin:0 0 12px;color:var(--acc);font-weight:600}
.sub{color:var(--mut);font-size:12px;margin-bottom:20px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px}
.card{background:var(--card);border:1px solid #232733;border-radius:10px;padding:16px}
table{width:100%;border-collapse:collapse;font-size:13px}
td,th{padding:5px 4px;border-bottom:1px solid #232733;text-align:left}
th{color:var(--mut);font-weight:500;font-size:12px}
td:last-child,th:last-child{text-align:right;font-variant-numeric:tabular-nums}
.badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600}
.badge.ok{background:rgba(46,204,113,.16);color:var(--ok)}
.badge.warn{background:rgba(245,166,35,.16);color:var(--warn)}
.badge.bad{background:rgba(231,76,60,.16);color:var(--bad)}
.kpi{font-size:26px;font-weight:600;line-height:1.2}
.kpi small{font-size:12px;color:var(--mut);font-weight:400}
.bar{height:6px;background:#232733;border-radius:3px;overflow:hidden;margin-top:6px}
.bar>i{display:block;height:100%;background:var(--acc)}
.note{color:var(--mut);font-size:12px;margin-top:10px;line-height:1.5}
ul.hint{margin:6px 0 0;padding-left:18px;color:var(--warn);font-size:12px}
"""


def render_html(data: Dict[str, Any]) -> str:
    sysd = data.get("system", {})
    dat = data.get("data", {})
    paper = data.get("paper", {})
    mon = data.get("monitor", {})
    macro = data.get("macro", {})
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # --- 시스템 상태
    src = sysd.get("universe_source", "unknown")
    age = sysd.get("csv_age_days")
    if src == "csv":
        u_badge = _badge("CSV 정상", "ok")
    elif src == "csv_stale":
        u_badge = _badge("CSV 스테일", "warn")
    else:
        u_badge = _badge("폴백(위험)", "bad")

    system_html = f"""
    <div class="card">
      <h2>1. 시스템 상태</h2>
      <table>
        <tr><th>스케줄러 잡</th><td>{_fmt(sysd.get('jobs'))}개</td></tr>
        <tr><th>유니버스 소스</th><td>{u_badge}</td></tr>
        <tr><th>CSV 나이</th><td>{_fmt(age, 1, '일')}</td></tr>
      </table>
    </div>"""

    # --- 데이터 축적
    prog = float(dat.get("progress", 0.0) or 0.0)
    ready_badge = _badge("검증 가능", "ok") if dat.get("ml_ready") else _badge("축적 중", "warn")
    data_html = f"""
    <div class="card">
      <h2>2. 데이터 축적 (P7 게이트)</h2>
      <div class="kpi">{prog:.0%} <small>비가격 팩터 검증 준비도</small></div>
      <div class="bar"><i style="width:{prog * 100:.1f}%"></i></div>
      <table style="margin-top:12px">
        <tr><th>decisions</th><td>{_fmt(dat.get('decisions'))} / 500</td></tr>
        <tr><th>outcomes</th><td>{_fmt(dat.get('outcomes'))} / 300</td></tr>
        <tr><th>ohlcv</th><td>{_fmt(dat.get('ohlcv'))}행</td></tr>
        <tr><th>판정</th><td>{ready_badge}</td></tr>
      </table>
    </div>"""

    # --- 감시·알림
    q = mon.get("quality", {}) or {}
    cb = mon.get("circuit_breakers", {}) or {}
    cb_badge = _badge("OPEN(차단)", "bad") if cb.get("is_open") else _badge("CLOSED(정상)", "ok")
    hints = "".join(f"<li>{_esc(h)}</li>" for h in q.get("hints", []) or [])
    hints_html = f'<ul class="hint">{hints}</ul>' if hints else ""
    monitor_html = f"""
    <div class="card">
      <h2>3. 감시 · 알림</h2>
      <table>
        <tr><th>서킷브레이커</th><td>{cb_badge}</td></tr>
        <tr><th>관측 신호</th><td>{_fmt(q.get('signals_observed'))}</td></tr>
        <tr><th>이상 감지율</th><td>{_fmt(q.get('anomaly_rate', 0) * 100, 2, '%')}</td></tr>
        <tr><th>알림 발송</th><td>{_fmt(q.get('alerts_sent'))}건</td></tr>
        <tr><th>억제율</th><td>{_fmt(q.get('suppression_ratio', 0) * 100, 1, '%')}</td></tr>
        <tr><th>관측 오류율</th><td>{_fmt((q.get('errors', 0) / q.get('signals_observed', 1) if q.get('signals_observed') else 0) * 100, 2, '%')}</td></tr>
      </table>
      {hints_html}
      <div class="note">임계: {_esc(q.get('thresholds', {}))}</div>
    </div>"""

    # --- 모멘텀 모의
    ev = paper.get("evaluated", 0) or 0
    paper_html = f"""
    <div class="card">
      <h2>4. 모멘텀 참고 신호 (모의)</h2>
      <div class="note">주문/포지션 아님 · 20거래일 경과 후 자동 평가</div>
      <table style="margin-top:8px">
        <tr><th>평가 완료 / 대기</th><td>{_fmt(ev)} / {_fmt(paper.get('pending'))}</td></tr>
        <tr><th>승률</th><td>{_fmt((paper.get('win_rate') or 0) * 100, 1, '%')}</td></tr>
        <tr><th>평균 수익률</th><td>{_fmt(paper.get('avg_return'), 2, '%')}</td></tr>
        <tr><th>중앙값</th><td>{_fmt(paper.get('median_return'), 2, '%')}</td></tr>
        <tr><th>최고 / 최저</th><td>{_fmt(paper.get('best'), 1, '%')} / {_fmt(paper.get('worst'), 1, '%')}</td></tr>
      </table>
      <div class="note">검증 결론: 모멘텀은 시장 베타(β)를 사는 전략 — α(연) −4.8%, t −0.74.
      단순 보유(Sharpe 1.23)가 전략(0.94)보다 우위 → <b>실거래 승격 부결</b>.</div>
    </div>"""

    # --- 매크로
    ind = macro.get("indicators", {}) or {}
    rows = "".join(
        f"<tr><th>{_esc(k)}</th><td>{_fmt(v.get('raw'))} "
        f"<span class='badge {'ok' if v.get('score', 0) >= 0.55 else ('warn' if v.get('score', 0) >= 0.45 else 'bad')}'>"
        f"{v.get('score', 0):.2f}</span></td></tr>"
        for k, v in ind.items()
    )
    macro_html = f"""
    <div class="card">
      <h2>5. 매크로 (13지표)</h2>
      <div class="kpi">{_fmt(macro.get('score'), 3)} <small>종합 점수 (0~1)</small></div>
      <table style="margin-top:10px">{rows}</table>
    </div>"""

    # --- 검증 결론
    verdict_html = """
    <div class="card">
      <h2>6. 전략 검증 결론 (P5/P6)</h2>
      <table>
        <tr><th>앙상블 OOS</th><td>엣지 없음 (IS +2.16 → OOS −0.19)</td></tr>
        <tr><th>모멘텀 α(연)</th><td>−4.8% (β 1.22, t −0.74)</td></tr>
        <tr><th>롱숏 Sharpe</th><td>−0.72 ~ +0.41 (알파 부재)</td></tr>
        <tr><th>유의 전략</th><td>0개 (t &gt; 2 통과 없음)</td></tr>
      </table>
      <div class="note">→ 시스템 가치는 <b>알파 생성이 아니라 감시·리스크 알림</b>에 있다.
      차기 경로는 비가격 팩터(감성/공시/ML)이며, 데이터 축적 게이트(2번 카드)가 선행 조건이다.</div>
    </div>"""

    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>stock_analyzer 운영 대시보드</title><style>{_CSS}</style></head>
<body>
<h1>stock_analyzer 운영 대시보드</h1>
<div class="sub">Phase 1 Shadow Mode · 생성 {now} · 자동 생성물(수동 편집 금지)</div>
<div class="grid">{system_html}{data_html}{monitor_html}{paper_html}{macro_html}{verdict_html}</div>
</body></html>"""


# ============================================================
# 진입점
# ============================================================

async def generate_dashboard(out_path: Path = DEFAULT_OUT) -> Path:
    """대시보드 HTML을 생성하고 경로를 반환한다."""
    from data.db_manager import DatabaseManager

    db = DatabaseManager()
    try:
        data = {
            "system": await collect_system_section(),
            "data": await collect_data_section(db),
            "paper": await collect_paper_section(db),
        }
    finally:
        try:
            await db.close()
        except Exception:
            pass

    data["monitor"] = collect_monitor_section()
    data["macro"] = collect_macro_section()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_html(data), encoding="utf-8")
    logger.info(f"대시보드 생성: {out_path}")
    return out_path


async def scheduled_dashboard() -> Dict[str, Any]:
    """스케줄러 진입점(평일 장 마감 후)."""
    try:
        path = await generate_dashboard()
        return {"status": "ok", "path": str(path)}
    except Exception as e:
        logger.error(f"대시보드 생성 실패: {e}")
        return {"status": "error", "error": str(e)}


def _main(argv: Sequence[str] | None = None) -> int:
    import asyncio
    import sys

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    parser = argparse.ArgumentParser(description="단일 파일 HTML 운영 대시보드 생성")
    parser.add_argument("--out", type=str, default=str(DEFAULT_OUT), help="출력 HTML 경로")
    args = parser.parse_args(list(argv) if argv is not None else None)

    path = asyncio.run(generate_dashboard(Path(args.out)))
    print(f"대시보드 생성 완료: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
