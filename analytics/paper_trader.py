# -*- coding: utf-8 -*-
"""모의매매 측정 시스템 (2026-10-10) — "정확도를 맞추는" 유일하게 정직한 방법.

목적:
    실제 돈을 쓰지 않고, 시스템이 낸 **모든 매매 신호**를 기록한 뒤
    N거래일 뒤 실현 가격으로 채점한다. 그래서 매일 진짜 승률이 나온다.

왜 필요한가:
    사용자 최대 요구사항은 "하루 1번 80% 확률로 5~10% 수익"이다.
    실측(5년 230,251건) 결과 무작위 상승 확률이 44.3%,
    5~10% 상승은 3.54%에 불과하다. 이 목표는 달성 불가능하지만,
    "무작위 44.3% 대비 초과 승률이 있는가"는 **측정할 수 있다.**
    이 모듈이 그 측정기다(추정·희망 없이 실현 가격만 사용).

설계:
    - 기록: logs/paper_signals.jsonl (미확정)
    - 확정: logs/paper_settled.jsonl (지평별 1줄)
    - 지평: 1 / 3 / 5 / 10 거래일
    - 통계: 승률 + Wilson 95% 신뢰구간 + 평균/중앙 수익률
      (표본이 적을 때 승률을 과대해석하지 않도록 신뢰구간을 항상 함께 표시)
    - 비가격 팩터 연구: 태그별(감성/공시) 승률 분해
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.jsonl_rotator import rotate_if_needed  # noqa: E402
from core.logger import setup_logger  # noqa: E402

logger = setup_logger("paper_trader")

_BASE = Path(__file__).resolve().parents[1]
SIGNALS_PATH = Path(os.getenv("PAPER_SIGNALS_PATH", str(_BASE / "logs" / "paper_signals.jsonl")))
SETTLED_PATH = Path(os.getenv("PAPER_SETTLED_PATH", str(_BASE / "logs" / "paper_settled.jsonl")))

# 채점 지평(거래일)
HORIZONS: Tuple[int, ...] = (1, 3, 5, 10)

# 무작위 기준선(%) — 5년 실측: 아무 종목이나 사면 상승 확률 44.3%
BASELINE_UP_PROB = float(os.getenv("PAPER_BASELINE_UP_PROB", "44.3"))

# 기록 대상(실제 매매 판단이 되는 신호만). HOLD는 매매가 아니므로 제외.
TRADEABLE_ACTIONS: Set[str] = {"SIGNAL_ENTRY", "BUY", "SELL"}


_HOUR_ORDER = ["09:00~09:04", "09:05~09:59", "10:00~11:59", "12:00~13:59", "14:00~15:35", "장전"]


def _hour_bucket(ts: Any) -> str:
    """신호 시각 → 진입 시간대 라벨. 09:00~09:04(개장 몰림)를 분리 측정.

    (2026-10-16 냉철 평가: SIGNAL_ENTRY가 개장 직후 3분에 몰림 — 변동성 최대 구간.
     개장 몰림이 실제로 성과가 나쁜지 데이터로 판정하기 위한 측정 장치.)
    """
    try:
        d = datetime.fromisoformat(str(ts)[:19])
    except (ValueError, TypeError):
        return ""
    hm = d.hour * 60 + d.minute
    if d.hour < 9:
        return "장전"
    if hm < 9 * 60 + 5:
        return "09:00~09:04"
    if hm < 10 * 60:
        return "09:05~09:59"
    if hm < 12 * 60:
        return "10:00~11:59"
    if hm < 14 * 60:
        return "12:00~13:59"
    return "14:00~15:35"


def _avg(vals: List[float]) -> float:
    return sum(vals) / len(vals) if vals else 0.0


def _wilson_ci(wins: int, n: int, z: float = 1.96) -> Tuple[float, float]:
    """승률의 Wilson 95% 신뢰구간(%). 소표본에서 정규근사보다 정확하다."""
    if n <= 0:
        return (0.0, 0.0)
    p = wins / n
    d = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / d
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / d
    return (max(0.0, (center - half) * 100.0), min(100.0, (center + half) * 100.0))


class PaperTrader:
    """신호 기록 → 실현가 채점 → 성과 요약."""

    def __init__(
        self,
        db: Optional[Any] = None,
        signals_path: Optional[Path] = None,
        settled_path: Optional[Path] = None,
    ) -> None:
        self.db = db
        self.signals_path = Path(signals_path) if signals_path else SIGNALS_PATH
        self.settled_path = Path(settled_path) if settled_path else SETTLED_PATH

    # ── 기록 ────────────────────────────────────────────────────────
    def record_signal(
        self,
        ticker: str,
        action: str,
        price: float,
        confidence: float = 0.0,
        regime: str = "",
        tags: Optional[List[str]] = None,
        factors: Optional[Dict[str, Any]] = None,
        trade_type: str = "",
        ts: Optional[float] = None,
    ) -> bool:
        """매매 신호를 기록한다(가격이 없거나 대상 액션이 아니면 무시)."""
        try:
            if not ticker or str(action) not in TRADEABLE_ACTIONS:
                return False
            if float(price) <= 0:
                return False
            now = datetime.fromtimestamp(ts) if ts else datetime.now()
            row = {
                "ts": now.isoformat(timespec="seconds"),
                "date": now.strftime("%Y-%m-%d"),
                "ticker": str(ticker),
                "action": str(action),
                "price": float(price),
                "confidence": round(float(confidence or 0.0), 4),
                "regime": str(regime or ""),
                "trade_type": str(trade_type or ""),
                "settled_horizons": [],
            }
            if tags:
                row["tags"] = list(tags)
            if factors:
                row["factors"] = dict(factors)
            self._append(self.signals_path, row)
            return True
        except Exception as e:
            logger.warning(f"모의매매 기록 실패(무시): {e}")
            return False

    # ── 채점 ────────────────────────────────────────────────────────
    async def settle_pending(self, max_rows: int = 200) -> int:
        """미확정 신호를 실현 종가로 채점한다. 확정한 (신호×지평) 건수 반환."""
        if self.db is None:
            return 0
        pending = self._load_jsonl(self.signals_path)
        if not pending:
            return 0

        # 🔴 2026-10-10 만료 정책: 진입일이 OHLCV 창(최근 60일)보다 과거인 기록은
        #   영원히 채점되지 않고 파일만 무한히 커진다 → 오래된 미확정은 정리한다.
        try:
            max_age_days = max(1, int(os.getenv("PAPER_PENDING_MAX_DAYS", "45")))
        except ValueError:
            max_age_days = 45
        cutoff = (datetime.now() - timedelta(days=max_age_days)).strftime("%Y-%m-%d")

        def _expired(rec: Dict[str, Any]) -> bool:
            d = str(rec.get("date") or "")[:10]
            return bool(d) and d < cutoff

        expired = [r for r in pending if _expired(r)]
        if expired:
            pending = [r for r in pending if not _expired(r)]
            logger.info(
                f"모의매매 미확정 {len(expired)}건 만료 제거(진입일 < {cutoff})"
                f" — 남은 미확정 {len(pending)}건"
            )
            if not pending:
                self._write_all(self.signals_path, [])
                return 0

        # 🔴 2026-10-10 수정: 처리 대상(최근 max_rows)만 다루면서 파일 전체를
        #   그 결과로 덮어쓰면, 200건을 넘는 오래된 미확정 기록이 **삭제**된다.
        #   → 처리하지 않는 나머지는 그대로 보존한다.
        untouched = pending[:-max_rows] if len(pending) > max_rows else []
        remaining: List[Dict[str, Any]] = list(untouched)
        settled_count = 0
        for rec in pending[-max_rows:]:
            try:
                ticker = str(rec.get("ticker", ""))
                price = float(rec.get("price") or 0.0)
                entry_date = str(rec.get("date") or "")
                if not ticker or price <= 0 or not entry_date:
                    continue
                rows = await self.db.get_ohlcv(ticker, 60)
                rows = sorted(rows, key=lambda r: str(r.get("date", "")))
                if not rows:
                    remaining.append(rec)
                    continue
                # 🔴 get_ohlcv는 "최근 N일"을 반환한다. 진입일이 그 창보다 과거면
                #   엉뚱한 가격으로 채점되므로(실측으로 발견) 확정하지 않고 보류한다.
                first_date = str(rows[0].get("date", ""))[:10]
                if first_date and first_date > entry_date:
                    remaining.append(rec)
                    continue
                idx = next(
                    (i for i, r in enumerate(rows) if str(r.get("date", ""))[:10] >= entry_date),
                    None,
                )
                if idx is None:
                    remaining.append(rec)
                    continue
                if idx is None:
                    remaining.append(rec)
                    continue
                done: Set[int] = set(rec.get("settled_horizons") or [])
                for h in HORIZONS:
                    if h in done or idx + h >= len(rows):
                        continue
                    exit_price = float(rows[idx + h].get("close") or 0.0)
                    if exit_price <= 0:
                        continue
                    ret_pct = (exit_price - price) / price * 100.0
                    out = dict(rec)
                    out.update({
                        "horizon": h,
                        "exit_date": str(rows[idx + h].get("date", ""))[:10],
                        "exit_price": exit_price,
                        "return_pct": round(ret_pct, 4),
                        "win": ret_pct > 0,
                        "settled_at": datetime.now().isoformat(timespec="seconds"),
                    })
                    out.pop("settled_horizons", None)
                    self._append(self.settled_path, out)
                    done.add(h)
                    settled_count += 1
                if len(done) < len(HORIZONS):
                    rec["settled_horizons"] = sorted(done)
                    remaining.append(rec)
            except Exception as e:
                logger.warning(f"모의매매 채점 실패(무시): {e}")
                remaining.append(rec)

        self._write_all(self.signals_path, remaining)
        if settled_count:
            logger.info(f"모의매매 채점 완료: {settled_count}건 확정 (미확정 {len(remaining)}건)")
        return settled_count

    # ── 요약 ────────────────────────────────────────────────────────
    def summary(self, days: int = 90) -> Dict[str, Any]:
        """확정된 성과 요약(지평별·액션별·태그별)."""
        cutoff = time.time() - days * 86400
        rows: List[Dict[str, Any]] = []
        for r in self._load_jsonl(self.settled_path):
            ts = self._parse_ts(r.get("ts"))
            if ts is None or ts >= cutoff:
                rows.append(r)

        out: Dict[str, Any] = {
            "days": days,
            "settled": len(rows),
            "pending": len(self._load_jsonl(self.signals_path)),
            "baseline_up_prob": BASELINE_UP_PROB,
            "by_horizon": {},
            "by_action": {},
            "by_tag": {},
            "expectancy_note": "무작위 기준선 대비 초과 승률이 있는지로만 판단",
        }
        for h in HORIZONS:
            sub = [r for r in rows if int(r.get("horizon") or 0) == h]
            out["by_horizon"][h] = self._stats(sub)

        for act in sorted({str(r.get("action")) for r in rows if r.get("action")}):
            out["by_action"][act] = self._stats([r for r in rows if str(r.get("action")) == act], horizon=5)

        tag_map: Dict[str, List[Dict[str, Any]]] = {}
        for r in rows:
            if int(r.get("horizon") or 0) != 5:
                continue
            for t in (r.get("tags") or []):
                tag_map.setdefault(str(t), []).append(r)
        out["by_tag"] = {t: self._stats(v) for t, v in sorted(tag_map.items())}

        hour_map: Dict[str, List[Dict[str, Any]]] = {}
        for r in rows:
            if int(r.get("horizon") or 0) != 5:
                continue
            b = _hour_bucket(r.get("ts"))
            if b:
                hour_map.setdefault(b, []).append(r)
        out["by_hour"] = {b: self._stats(v) for b, v in hour_map.items()}
        return out

    def _stats(self, rows: List[Dict[str, Any]], horizon: Optional[int] = None) -> Dict[str, Any]:
        """승률 + Wilson 95% CI + 평균/중앙 수익률 + 기준선 대비."""
        n = len(rows)
        if n == 0:
            return {"n": 0, "win_rate": 0.0, "ci_low": 0.0, "ci_high": 0.0,
                    "avg_return": 0.0, "median_return": 0.0, "beats_baseline": None}
        wins = sum(1 for r in rows if r.get("win"))
        rets = sorted(float(r.get("return_pct") or 0.0) for r in rows)
        mid = rets[n // 2] if n % 2 else (rets[n // 2 - 1] + rets[n // 2]) / 2.0
        lo, hi = _wilson_ci(wins, n)
        win_rate = wins / n * 100.0
        return {
            "n": n,
            "win_rate": round(win_rate, 2),
            "ci_low": round(lo, 2),
            "ci_high": round(hi, 2),
            "avg_return": round(sum(rets) / n, 4),
            "median_return": round(mid, 4),
            "beats_baseline": bool(lo > BASELINE_UP_PROB),   # 신뢰구간 하한이 기준선을 넘어야 유의
            # 2026-10-16: 기대값 운영(Top 트레이더 방식) — 승/패 평균과 손익분기 승률.
            "avg_win": round(_avg([float(r.get("return_pct") or 0.0) for r in rows if r.get("win")]), 4),
            "avg_loss": round(_avg([float(r.get("return_pct") or 0.0) for r in rows if not r.get("win")]), 4),
        }

    def format_report(self, days: int = 90) -> str:
        """텔레그램용 텍스트 리포트."""
        s = self.summary(days)
        lines = [
            f"📊 <b>모의매매 성과</b> (최근 {days}일)",
            "━━━━━━━━━━━━━━━━━━━━━",
            f"• 확정 <b>{s['settled']}건</b> / 미확정 {s['pending']}건",
            f"• 무작위 기준선: 상승확률 <b>{s['baseline_up_prob']:.1f}%</b>",
        ]
        for h in HORIZONS:
            st = s["by_horizon"][h]
            if st["n"] == 0:
                lines.append(f"• {h}일: 표본 없음")
                continue
            mark = "✅" if st["beats_baseline"] else "➖"
            lines.append(
                f"• {h}일: 승률 <b>{st['win_rate']:.1f}%</b> "
                f"(95% {st['ci_low']:.0f}~{st['ci_high']:.0f}%) "
                f"평균 {st['avg_return']:+.2f}% n={st['n']} {mark}"
            )
            aw, al = st.get("avg_win") or 0.0, st.get("avg_loss") or 0.0
            if aw > 0 and al < 0:
                be = abs(al) / (aw + abs(al)) * 100.0
                lines.append(
                    f"  ↳ 승 +{aw:.1f}% / 패 {al:.1f}% → 손익분기 승률 <b>{be:.0f}%</b>"
                )
        if s["by_tag"]:
            lines.append("━━━ 조건 태그별(5일) ━━━")
            for tag, st in s["by_tag"].items():
                if st["n"] >= 5:
                    lines.append(f"• {tag}: 승률 {st['win_rate']:.1f}% (n={st['n']})")
        if s.get("by_hour"):
            shown = [(b, st) for b, st in s["by_hour"].items() if st["n"] >= 10]
            shown.sort(key=lambda kv: _HOUR_ORDER.index(kv[0]) if kv[0] in _HOUR_ORDER else 99)
            if shown:
                lines.append("━━━ 진입 시간대별(5일, n≥10) ━━━")
                for b, st in shown:
                    lines.append(
                        f"• {b}: 승률 {st['win_rate']:.1f}% "
                        f"평균 {st['avg_return']:+.2f}% (n={st['n']})"
                    )
        if s["settled"] < 30:
            lines.append("<i>⚠️ 표본 부족 — 승률을 신뢰하지 마세요(최소 30건)</i>")
        lines.append("<i>✅=신뢰구간 하한이 기준선 초과(통계적 우위)</i>")
        lines.append(
            "<i>※ 거래비용(수수료·세금·슬리피지) 미반영 — "
            "왕복 약 0.3%를 빼면 실질 수익은 그만큼 낮습니다</i>"
        )
        return "\n".join(lines)

    # ── 파일 유틸 ───────────────────────────────────────────────────
    @staticmethod
    def _parse_ts(value: Any) -> Optional[float]:
        try:
            return datetime.fromisoformat(str(value)).timestamp()
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
        if not Path(path).exists():
            return []
        out: List[Dict[str, Any]] = []
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(rec, dict):
                        out.append(rec)
        except OSError:
            return []
        return out

    @staticmethod
    def _append(path: Path, row: Dict[str, Any]) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        rotate_if_needed(path)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    @staticmethod
    def _write_all(path: Path, rows: List[Dict[str, Any]]) -> None:
        """미확정 목록을 원자적으로 다시 쓴다."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
            os.replace(tmp, path)
        except OSError as e:
            logger.warning(f"모의매매 미확정 목록 저장 실패(무시): {e}")
