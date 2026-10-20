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
import json
import os
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

    # 2026-10-10: 운영 관측(디스크 여유 · 미전송 알림 보관함)
    try:
        from core.disk_guard import disk_status

        out["disk"] = disk_status()
    except Exception as e:
        logger.debug(f"디스크 정보 수집 실패(무시): {e}")
    try:
        from pathlib import Path as _Path

        _bp = _Path(os.getenv("ALERT_BACKLOG_PATH", "logs/pending_alerts.jsonl"))
        if _bp.exists():
            out["alert_backlog"] = len(
                [ln for ln in _bp.read_text(encoding="utf-8").splitlines() if ln.strip()]
            )
        else:
            out["alert_backlog"] = 0
    except Exception as e:
        logger.debug(f"미전송 알림 확인 실패(무시): {e}")
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

        snap = get_ops_snapshot() or {}
        out["quality"] = snap.get("quality", {})
        out["tuning"] = snap.get("tuning", {})
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


def collect_risk_section() -> Dict[str, Any]:
    """집중도 리스크(상관행렬) — 마지막 점검 결과 재사용(P8-1)."""
    try:
        from risk.correlation_monitor import get_last_report

        return get_last_report()
    except Exception as e:
        logger.debug(f"집중도 리스크 섹션 실패(무시): {e}")
        return {}


def collect_shadow_section() -> Dict[str, Any]:
    """섀도우 전략 평가 요약(P8-2) — 세션 내 기록 기준."""
    try:
        from application.analysis.shadow_registry import get_shadow_summary

        return get_shadow_summary()
    except Exception as e:
        logger.debug(f"섀도우 섹션 실패(무시): {e}")
        return {}


def collect_calibration_section() -> Dict[str, Any]:
    """신뢰도 캘리브레이션(ECE) 요약(P8-3)."""
    try:
        from analytics.calibration_bridge import get_calibration_summary, get_tag_win_rates

        summary = get_calibration_summary()
        summary["tag_rates"] = get_tag_win_rates()
        return summary
    except Exception as e:
        logger.debug(f"캘리브레이션 섹션 실패(무시): {e}")
        return {}


def collect_paper_trade_section() -> Dict[str, Any]:
    """모의매매 성과(신호 → 실현가 채점). 2026-10-10 신설.

    승률을 과장하지 않도록 항상 Wilson 95% 신뢰구간과 무작위 기준선을 함께 보여준다.
    """
    try:
        from analytics.paper_trader import PaperTrader

        return PaperTrader(db=None).summary(days=90)
    except Exception as e:
        logger.debug(f"모의매매 섹션 실패(무시): {e}")
        return {}


def collect_trace_section() -> Dict[str, Any]:
    """최근 의사결정 경로 요약(P8-4)."""
    try:
        from observability.trace_bridge import recent_summaries

        return {"recent": recent_summaries(5)}
    except Exception as e:
        logger.debug(f"trace 섹션 실패(무시): {e}")
        return {}


def collect_signal_registry_section() -> Dict[str, Any]:
    """시장 신호 기록부(S139) — 최근 기록 A/B/C + 채점 성적."""
    try:
        from scheduler.signal_logger import latest_signals, scorecard

        latest = latest_signals()
        disc: Dict[str, Any] = {}
        try:
            from analytics.disclosure_portfolio import load_summary as load_pf
            from scheduler.disclosure_settle import disclosure_stats
            from scheduler.disclosure_watch import events_for_day, latest_event_day

            day = latest_event_day()
            if day:
                disc = {"date": day, "rows": events_for_day(day), "settled": disclosure_stats()}
            try:
                disc["portfolio"] = load_pf()
            except Exception:
                pass
        except Exception:
            disc = {}
        return {
            "date": latest.get("date", ""),
            "active": latest.get("signals", []),
            "scorecard": scorecard(),
            "disclosures": disc,
        }
    except Exception as e:
        logger.debug(f"신호 기록부 섹션 실패(무시): {e}")
        return {}


def collect_macro_section() -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    try:
        from filters.macro_filter import MacroFilter

        result = MacroFilter().check({})
        out = {"score": result.get("score", 0.0), "indicators": result.get("indicators", {})}
    except Exception as e:
        logger.debug(f"매크로 섹션 실패(무시): {e}")
    try:
        from analytics.market_breadth import (
            PANIC_NOTE,
            PANIC_THRESHOLD,
            breadth_percentile,
            compute_breadth,
            load_breadth_history,
            load_breadth_series,
        )

        b = compute_breadth(load_breadth_series())
        if int(b.get("n") or 0) >= 20:
            p20 = b.get("above_ma20_pct")
            panic = p20 is not None and float(p20) <= PANIC_THRESHOLD
            out["breadth"] = dict(
                b,
                percentile=breadth_percentile(load_breadth_history(), p20),
                panic=panic,
                note=PANIC_NOTE if panic else "",
            )
            try:
                from analytics.market_zones import classify_zone

                k5 = None
                try:
                    from scheduler.macro_collector import get_cached_macro

                    m = get_cached_macro()
                    k5 = float(m.kospi_trend) if m.kospi_trend is not None else None
                except Exception:
                    pass
                z = classify_zone(k5, float(p20) if p20 is not None else None)
                out["zone"] = {"zone": z.get("zone"), "reasons": z.get("reasons", []), "kospi_5d": k5}
            except Exception as e:
                logger.debug(f"구역 섹션 실패(무시): {e}")
    except Exception as e:
        logger.debug(f"시장 폭 섹션 실패(무시): {e}")
    return out


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
    risk = data.get("risk", {}) or {}
    shadow = data.get("shadow", {}) or {}
    calib = data.get("calibration", {}) or {}
    trace = data.get("trace", {}) or {}
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

    # 디스크/알림 보관함 배지 (2026-10-10)
    disk = sysd.get("disk") or {}
    if not disk:
        d_badge = _badge("알 수 없음", "warn")
    elif (disk.get("free_gb") or 0) < 3 or (disk.get("free_pct") or 100) < 5:
        d_badge = _badge(f"{disk.get('free_gb')}GB ({disk.get('free_pct')}%) 부족", "bad")
    else:
        d_badge = _badge(f"{disk.get('free_gb')}GB ({disk.get('free_pct')}%)", "ok")
    backlog = sysd.get("alert_backlog") or 0
    b_badge = _badge(f"{backlog}건 대기", "warn") if backlog else _badge("없음", "ok")

    system_html = f"""
    <div class="card">
      <h2>1. 시스템 상태</h2>
      <table>
        <tr><th>스케줄러 잡</th><td>{_fmt(sysd.get('jobs'))}개</td></tr>
        <tr><th>유니버스 소스</th><td>{u_badge}</td></tr>
        <tr><th>CSV 나이</th><td>{_fmt(age, 1, '일')}</td></tr>
        <tr><th>디스크 여유</th><td>{d_badge}</td></tr>
        <tr><th>미전송 알림</th><td>{b_badge}</td></tr>
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
    tuning = mon.get("tuning", {}) or {}
    tun_rows = "".join(
        f"<tr><th>{_esc(str(t.get('target')))}</th><td>{_esc(str(t.get('current')))} → "
        f"<b>{_esc(str(t.get('suggested')))}</b></td></tr>"
        for t in (tuning.get("suggestions") or [])
    )
    tuning_html = (
        f'<div class="note" style="margin-top:8px">자동 튜닝 제안(미적용)</div><table>{tun_rows}</table>'
        if tun_rows else ""
    )
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
      {tuning_html}
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
    bl = macro.get("breadth") or {}
    breadth_html = ""
    if bl.get("above_ma20_pct") is not None:
        p20 = float(bl["above_ma20_pct"])
        pct = bl.get("percentile")
        panic = bool(bl.get("panic"))
        lvl = "bad" if panic else ("warn" if pct is not None and float(pct) <= 20 else "ok")
        tag = "패닉권" if panic else ("하위 분위" if lvl == "warn" else "정상")
        pct_txt = f" · 역사 백분위 {float(pct):.0f}%" if pct is not None else ""
        note = f'<div class="note">{_esc(bl.get("note") or "")}</div>' if panic else ""
        breadth_html = f"""
      <table style="margin-top:10px">
        <tr><th>시장 폭 MA20↑</th><td>{_fmt(p20, 0, '%')} {_badge(tag, lvl)}{pct_txt}</td></tr>
        <tr><th>MA60↑ / 상승·하락</th><td>{_fmt(bl.get('above_ma60_pct'), 0, '%')} / {_fmt(bl.get('advancers'), 0)}·{_fmt(bl.get('decliners'), 0)}</td></tr>
      </table>{note}"""
    z = macro.get("zone") or {}
    zone_html = ""
    if z.get("zone"):
        zlvl = {"진입금지": "bad", "기회": "warn", "관찰": "warn", "정상": "ok"}.get(str(z["zone"]), "ok")
        reason = (z.get("reasons") or [""])[0]
        zone_html = (
            f'<div class="note">🚦 구역: {_badge(str(z["zone"]), zlvl)}'
            f'{" — " + _esc(reason) if reason else ""} (참고용 — 주문/점수 반영 없음)</div>'
        )
    macro_html = f"""
    <div class="card">
      <h2>5. 매크로 (13지표)</h2>
      <div class="kpi">{_fmt(macro.get('score'), 3)} <small>종합 점수 (0~1)</small></div>
      <table style="margin-top:10px">{rows}</table>{breadth_html}{zone_html}
    </div>"""

    # --- 집중도 리스크 (P8-1)
    rscore = risk.get("score")
    if rscore is None:
        risk_body = '<div class="note">아직 점검 전 — 평일 17:00 자동 실행</div>'
    else:
        lvl = "ok" if float(rscore) >= 0.6 else ("warn" if float(rscore) >= 0.4 else "bad")
        pairs = "".join(
            f"<tr><th>{_esc(str(p.get('ticker_a', '?')))} ↔ {_esc(str(p.get('ticker_b', '?')))}</th>"
            f"<td>{float(p.get('correlation', 0)):+.2f}</td></tr>"
            for p in (risk.get("top_pairs") or [])[:5]
        )
        risk_body = f"""
      <div class="kpi">{float(rscore):.2f} {_badge('분산도 ' + lvl, lvl)} <small>평균 |ρ| {_fmt(risk.get('avg_abs_correlation'), 3)}</small></div>
      <table style="margin-top:10px">
        <tr><th>평가 종목</th><td>{_fmt(risk.get('evaluated_tickers'))}개 / 공통 {_fmt(risk.get('common_dates'))}일</td></tr>
        <tr><th>고상관 쌍(|ρ|≥0.8)</th><td>{_fmt(risk.get('high_corr_pair_count'))}개</td></tr>
        {pairs}
      </table>
      <div class="note">{_esc(risk.get('recommendation', ''))}</div>"""

    risk_html = f"""
    <div class="card">
      <h2>7. 집중도 리스크 (상관행렬)</h2>{risk_body}
    </div>"""

    # --- 섀도우 전략 (P8-2)
    strategies = shadow.get("strategies") or {}
    if not strategies:
        shadow_body = '<div class="note">등록된 섀도우 전략 없음 또는 아직 평가 전</div>'
    else:
        rows = ""
        for name, sm in strategies.items():
            total = int(sm.get("total", 0) or 0)
            rate = float(sm.get("agreement_rate", 0.0) or 0.0)
            lvl = "ok" if rate >= 0.7 else ("warn" if rate >= 0.4 else "bad")
            rows += (
                f"<tr><th>{_esc(name)}</th><td>{total}건 "
                f"{_badge(f'일치 {rate:.0%}', lvl)} "
                f"<small>오류 {_fmt(sm.get('errors'))}</small></td></tr>"
            )
        stat = shadow.get("stats", {}) or {}
        shadow_body = f"""
      <table>{rows}</table>
      <div class="note">평가 {_fmt(stat.get('evaluated'))} · 기록 {_fmt(stat.get('recorded'))} ·
      오류 {_fmt(stat.get('errors'))} — 섀도우는 <b>주문을 만들지 않습니다</b>(비교 기록만).</div>"""

    shadow_html = f"""
    <div class="card">
      <h2>8. 섀도우 전략 평가</h2>{shadow_body}
    </div>"""

    # --- 캘리브레이션 (P8-3)
    regimes = calib.get("regimes") or {}
    if not regimes:
        calib_body = '<div class="note">채점된 예측 없음 — 시그널 발생 후 5거래일 뒤 자동 채점</div>'
    else:
        grows = ""
        for name, cal in regimes.items():
            if cal.get("status") == "insufficient_data":
                grows += f"<tr><th>{_esc(name)}</th><td>표본 부족({_fmt(cal.get('sample'))})</td></tr>"
            else:
                ece = float(cal.get("ece", 0.0))
                lvl = "ok" if ece <= 0.05 else ("warn" if ece <= 0.12 else "bad")
                grows += (f"<tr><th>{_esc(name)}</th><td>{_badge(f'ECE {ece:.3f}', lvl)} "
                          f"<small>n={_fmt(cal.get('total_samples'))}</small></td></tr>")
        tag_rows = ""
        tag_rates = (calib.get("tag_rates") or {}).get("tags") or {}
        for tag, v in tag_rates.items():
            if v.get("status") == "ok":
                wr = float(v.get("win_rate") or 0.0)
                lvl = "ok" if wr >= 0.55 else ("warn" if wr >= 0.45 else "bad")
                tag_rows += (f"<tr><th>{_esc(tag)}</th><td>{_badge(f'{wr:.0%}', lvl)} "
                             f"<small>n={_fmt(v.get('n'))}</small></td></tr>")
            else:
                tag_rows += f"<tr><th>{_esc(tag)}</th><td>표본 부족(n={_fmt(v.get('n'))})</td></tr>"
        tag_html = (f'<div class="note" style="margin-top:8px">비가격 조건 태그별 승률(P12-4)</div>'
                    f"<table>{tag_rows}</table>") if tag_rows else ""
        tagged = (calib.get("tag_rates") or {}).get("tagged", 0)
        calib_body = (f"<table>{grows}</table>{tag_html}"
                      f"<div class='note'>ECE 낮을수록 신뢰도가 실제 적중률과 일치(≤0.05 양호). "
                      f"태그 보유 {_fmt(tagged)}건.</div>")

    calib_html = f"""
    <div class="card">
      <h2>9. 신뢰도 캘리브레이션 (ECE)</h2>{calib_body}
    </div>"""

    # --- 의사결정 경로 (P8-4)
    traces = trace.get("recent") or []
    if not traces:
        trace_body = '<div class="note">기록된 trace 없음 — 시그널 발생 후 표시(텔레그램 <code>/trace</code>)</div>'
    else:
        trows = ""
        for t in traces:
            nodes = t.get("node_count", t.get("nodes", "—"))
            dur = t.get("total_duration_ms", t.get("duration_ms"))
            ok = t.get("failed_nodes", 0)
            trows += (f"<tr><th>{_esc(str(t.get('trace_id', '?')))[:28]}</th>"
                      f"<td>{_fmt(nodes)}단계 {_fmt(dur, 1, 'ms')} "
                      f"{_badge('실패 ' + str(ok), 'bad') if ok else _badge('정상', 'ok')}</td></tr>")
        trace_body = f"<table>{trows}</table>"

    # --- 모의매매 성과 (2026-10-10) — 실현가 채점, 주문 없음
    ptd = data.get("paper_trades", {}) or {}
    pt_horizons = ptd.get("by_horizon") or {}
    baseline = float(ptd.get("baseline_up_prob", 0.0) or 0.0)
    if not ptd.get("settled"):
        pt_body = (
            f'<div class="note">확정 신호 없음 — 신호 발생 후 자동 채점됩니다 '
            f'(미확정 {_fmt(ptd.get("pending"))}건). '
            f'무작위 기준선(상승확률) {baseline:.1f}%</div>'
        )
    else:
        pt_rows = ""
        for h in sorted(pt_horizons, key=lambda x: int(x)):
            st = pt_horizons[h] or {}
            st_n = int(st.get("n", 0) or 0)
            if not st_n:
                continue
            st_wr = float(st.get("win_rate", 0.0) or 0.0)
            st_lo = float(st.get("ci_low", 0.0) or 0.0)
            st_hi = float(st.get("ci_high", 0.0) or 0.0)
            st_avg = float(st.get("avg_return", 0.0) or 0.0)
            lvl = "ok" if st.get("beats_baseline") else "warn"
            pt_rows += (
                f"<tr><th>{h}일</th><td>{_fmt(st_n)}건 "
                f"{_badge(f'승률 {st_wr:.1f}%', lvl)} "
                f"<small>95% {st_lo:.0f}~{st_hi:.0f}% · 평균 {st_avg:+.2f}%</small></td></tr>"
            )
        pt_tag_rows = ""
        for t, st_t in (ptd.get("by_tag") or {}).items():
            st_t = st_t or {}
            if int(st_t.get("n", 0) or 0) < 5:
                continue
            pt_tag_rows += (
                f"<tr><th>{_esc(t)}</th><td>{_fmt(st_t.get('n'))}건 "
                f"승률 {float(st_t.get('win_rate', 0.0)):.1f}%</td></tr>"
            )
        pt_body = f"<table>{pt_rows}</table>"
        if pt_tag_rows:
            pt_body += f'<div class="note">조건 태그별(5일)</div><table>{pt_tag_rows}</table>'
        pt_body += (
            f'<div class="note">기준선(무작위 상승확률) {baseline:.1f}% · '
            f'확정 {_fmt(ptd.get("settled"))}건 · 미확정 {_fmt(ptd.get("pending"))}건. '
            f'✅ = 95% 신뢰구간 하한이 기준선을 넘음(통계적 우위). '
            f'거래비용(수수료·세금) 미반영. '
            f'<b>실제 주문은 발생하지 않습니다.</b></div>'
        )

    paper_trades_html = f"""
    <div class="card">
      <h2>11. 모의매매 성과 (실현가 채점)</h2>{pt_body}
    </div>"""

    trace_html = f"""
    <div class="card">
      <h2>10. 의사결정 경로 (Trace)</h2>{trace_body}
    </div>"""

    sig = data.get("signals") or {}
    sig_rows = ""
    for r in (sig.get("active") or []):
        gl = str(r.get("grade") or "-")
        lvl = "bad" if gl == "A" else ("warn" if gl == "B" else "ok")
        sig_rows += (
            f"<tr><th>{_badge(gl, lvl)} {_esc(str(r.get('label') or ''))}</th><td></td></tr>"
        )
    sc_rows = ""
    for r in (sig.get("scorecard") or []):
        sc_rows += (
            f"<tr><th>{_esc(str(r.get('label') or ''))}</th>"
            f"<td>n={_fmt(r.get('n'))} 평균 {_fmt(r.get('avg'), 2, '%')} 적중 {_fmt(r.get('hit'), 0, '%')}</td></tr>"
        )
    sc_block = (
        f'<div class="note">성적표(5거래일, KOSPI)</div><table>{sc_rows}</table>'
        if sc_rows else '<div class="note">성적표: 채점 대기(5거래일 경과 후 자동)</div>'
    )
    signals_html = f"""
    <div class="card">
      <h2>12. 시장 신호 기록부</h2>
      <div class="note">최근 기록 {_esc(str(sig.get('date') or '—'))} · A=검증 생존 / B=경계 / C=참고(추적)</div>
      <table style="margin-top:8px">{sig_rows or '<tr><td class="note">기록 없음</td></tr>'}</table>
      {sc_block}
      <div class="note">매일 17:20 자동 기록·채점. 실제 주문은 발생하지 않습니다.</div>
    </div>"""

    disc = sig.get("disclosures") or {}
    disc_rows = ""
    for r in (disc.get("rows") or []):
        gl = str(r.get("grade") or "-")
        disc_rows += (
            f"<tr><th>{_badge('A' if gl.startswith('A') else 'B', 'bad' if gl.startswith('A') else 'warn')} "
            f"{_esc(str(r.get('name') or ''))}({_esc(str(r.get('ticker') or ''))})</th>"
            f"<td>{_esc(str(r.get('category') or ''))} · 5d {_fmt(r.get('mean5'), 2, '%')} "
            f"적중 {_fmt(r.get('hit5'), 0, '%')}</td></tr>"
        )
    srows = ""
    for r in (disc.get("settled") or []):
        srows += (
            f"<tr><th>{_esc(str(r.get('category') or ''))}</th>"
            f"<td>n={_fmt(r.get('n'))} 평균 {_fmt(r.get('mean5'), 2, '%')} "
            f"적중 {_fmt(r.get('hit5'), 0, '%')} <span class='note'>({_esc(str(r.get('grade') or ''))})</span></td></tr>"
        )
    disc_settled_block = (
        f'<div class="note">실현 성적(5거래일, KOSPI 대비)</div><table>{srows}</table>'
        if srows else '<div class="note">실현 성적: 첫 채점 대기(5거래일 경과 후 자동)</div>'
    )
    pf = disc.get("portfolio") or {}
    pf_block = (
        f"<div class='note'>공시 전략 모의(가상)</div><table>"
        f"<tr><th>누적</th><td>{_fmt(pf.get('total_pct'), 2, '%')}</td></tr>"
        f"<tr><th>보유</th><td>{_fmt(pf.get('positions'))}종목</td></tr>"
        f"<tr><th>청산</th><td>{_fmt(pf.get('closed'))}건 · 승률 {_fmt(pf.get('win_rate'), 1, '%')}</td></tr>"
        f"</table>"
        if pf else "<div class='note'>공시 전략 모의: 첫 실행 대기(16:45)</div>"
    )

    disclosures_html = f"""
    <div class="card">
      <h2>13. 공시 신호 (A/B)</h2>
      <div class="note">최근 기록 {_esc(str(disc.get('date') or '—'))} · DART 전체 공시 → 유니버스 필터</div>
      <table style="margin-top:8px">{disc_rows or '<tr><td class="note">기록 없음</td></tr>'}</table>
      {disc_settled_block}
      {pf_block}
      <div class="note">A급은 텔레그램 알림 · 매일 08:10·14:50·16:40 스캔 · 실제 주문 없음</div>
    </div>"""


    # --- 스트레스 · 취약점 (S152)
    stress_html = ""
    try:
        sd = json.loads((Path(__file__).resolve().parent.parent / "data" / "disclosure_strategy.json").read_text(encoding="utf-8"))
        sv = sd.get("variants") or {}
        srows = []
        for key, label in (("A_all", "기준"), ("A_cost06", "비용 0.6%"), ("A_cost10", "비용 1.0%"),
                           ("A_invvol", "역변동성"), ("A_cap10", "자본 10억"), ("A_cap30", "자본 30억")):
            pp = (sv.get(key) or {}).get("portfolio") or {}
            if pp:
                srows.append(
                    f"<tr><td>{label}</td><td>{pp.get('cagr')}%</td><td>{pp.get('sharpe')}</td><td>{pp.get('mdd')}%</td></tr>"
                )
        cap = sd.get("capacity") or {}
        cap_txt = ""
        if cap:
            cap_txt = (
                f"<div class=\"note\">수용규모(1% ADV): 1회 중앙 <b>{cap.get('median_krw', 0)/1e8:.2f}억</b> · "
                f"5종목 동시 <b>{cap.get('portfolio_krw', 0)/1e8:.1f}억</b></div>"
            )
        stress_html = f"""
    <div class="card">
      <h2>14. 취약점 — 비용·수용규모</h2>
      <table>
        <tr><th>변형</th><th>CAGR</th><th>Sharpe</th><th>MDD</th></tr>
        {''.join(srows) or '<tr><td class="note">백테스트 결과 없음</td></tr>'}
      </table>
      {cap_txt}
      <div class="note">🔴 왕복비용 1%면 CAGR 5.3%·Sharpe 0.34 — 비용·슬리피지 관리가 성패의 핵심.</div>
    </div>"""
    except Exception:
        stress_html = ""

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
<div class="grid">{system_html}{data_html}{monitor_html}{paper_html}{macro_html}{signals_html}{disclosures_html}{stress_html}{verdict_html}{risk_html}{shadow_html}{calib_html}{trace_html}{paper_trades_html}</div>
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
    data["risk"] = collect_risk_section()
    data["shadow"] = collect_shadow_section()
    data["calibration"] = collect_calibration_section()
    data["trace"] = collect_trace_section()
    data["paper_trades"] = collect_paper_trade_section()
    data["signals"] = collect_signal_registry_section()

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
