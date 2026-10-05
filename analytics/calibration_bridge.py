# -*- coding: utf-8 -*-
"""analytics/calibration_bridge.py - 신뢰도 캘리브레이션 폐루프 (P8-3).

배경
----
`analytics/calibration_tracker.py`(208줄, ECE/Regime별 캘리브레이션)는
작성되어 있었으나 **프로덕션 참조 0건의 고아 모듈**이었다.
또한 `record(regime, confidence, actual_win)`는 **결과 라벨**이 필요한데,
현재 시스템에는 결과 피드 경로가 없다(자동매매 없음).

해결
----
매매 대신 **가격 기반 채점(settlement)** 으로 결과 라벨을 만든다:

1. 시그널 발생 시 예측 기록 (regime, confidence, ticker, action, price, date)
   → `logs/calibration_predictions.jsonl`
2. N거래일(기본 5일) 경과 후, DB의 OHLCV로 실현 수익률을 계산해 승/패 판정
   - BUY  : 수익률 > +임계 → 승
   - SELL : 수익률 < -임계 → 승
   - HOLD : |수익률| < 임계 → 승 (관망이 맞았는지)
3. `CalibrationTracker.record()`에 투입 → Regime별 ECE 산출
4. 판정 완료분은 `logs/calibration_settled.jsonl`에 적재(재기동 시 재구성)

주의: 트래커는 메모리 기반이므로 매 점검 시 settled 파일로부터 **재구성**한다(멱등).

사용:
    python -m analytics.calibration_bridge --settle
    python -m analytics.calibration_bridge --report
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from analytics.calibration_tracker import CalibrationTracker
from core.logger import setup_logger

logger = setup_logger("calibration")

BASE = Path(__file__).parent.parent / "logs"
PRED_PATH = BASE / "calibration_predictions.jsonl"
SETTLED_PATH = BASE / "calibration_settled.jsonl"

DEFAULT_HORIZON = 5          # 거래일
DEFAULT_THRESHOLD = 0.005    # ±0.5% 를 승/패 경계로


# ═══════════════════════════════════════════════════════════════════
#  기록
# ═══════════════════════════════════════════════════════════════════

def record_prediction(
    regime: str,
    confidence: float,
    ticker: str,
    action: str,
    price: float,
    tags: Optional[List[str]] = None,
    factors: Optional[Dict[str, Any]] = None,
    path: Path = PRED_PATH,
) -> bool:
    """시그널 예측을 기록한다(실패해도 프로덕션에 영향 없음).

    Args:
        tags: 비가격 조건 태그(P12-3) — P12-4 승률 통계의 키
        factors: 비가격 팩터 스냅샷(감성/공시 등). 값이 없으면 생략.
    """
    try:
        if not ticker or price <= 0:
            return False
        row = {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "ts": datetime.now().isoformat(),
            "regime": str(regime or "unknown"),
            "confidence": round(float(confidence), 4),
            "ticker": str(ticker),
            "action": str(action),
            "price": float(price),
        }
        if tags:
            row["tags"] = list(tags)
        if factors:
            row["factors"] = dict(factors)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return True
    except Exception as e:
        logger.warning(f"예측 기록 실패(무시): {e}")
        return False


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError as e:
        logger.warning(f"{path} 읽기 실패: {e}")
    return out


# ═══════════════════════════════════════════════════════════════════
#  채점
# ═══════════════════════════════════════════════════════════════════

def judge(action: str, price_at: float, price_after: float, threshold: float = DEFAULT_THRESHOLD) -> Optional[bool]:
    """실현 수익률로 승/패를 판정한다. 판정 불가면 None."""
    if price_at <= 0 or price_after <= 0:
        return None
    ret = (price_after - price_at) / price_at
    a = (action or "").upper()
    if a in ("BUY", "STRONG_BUY"):
        return ret > threshold
    if a in ("SELL", "STRONG_SELL"):
        return ret < -threshold
    if a == "HOLD":
        return abs(ret) < threshold
    return None


async def settle_predictions(
    db: Any,
    horizon: int = DEFAULT_HORIZON,
    threshold: float = DEFAULT_THRESHOLD,
    pred_path: Path = PRED_PATH,
    settled_path: Path = SETTLED_PATH,
) -> Dict[str, Any]:
    """미채점 예측을 실현가로 채점해 settled 파일에 적재한다."""
    preds = _read_jsonl(pred_path)
    settled = _read_jsonl(settled_path)
    done_keys = {(r.get("ticker"), r.get("date"), r.get("action"), r.get("price")) for r in settled}

    cutoff = (datetime.now() - timedelta(days=horizon)).strftime("%Y-%m-%d")
    pending = [p for p in preds if str(p.get("date", "")) <= cutoff]
    if not pending:
        return {"status": "no_pending", "pending": 0, "settled": len(settled)}

    new_rows: List[Dict[str, Any]] = []
    skipped = 0
    for p in pending:
        key = (p.get("ticker"), p.get("date"), p.get("action"), p.get("price"))
        if key in done_keys:
            skipped += 1
            continue
        try:
            start = str(p.get("date"))
            end = (datetime.fromisoformat(start) + timedelta(days=horizon * 2 + 5)).strftime("%Y-%m-%d")
            bars = await db.get_ohlcv_range(str(p["ticker"]), start, end)
        except Exception as e:
            logger.debug(f"채점 시세 조회 실패({p.get('ticker')}): {e}")
            continue
        if len(bars) < 2:
            continue
        entry = float(p["price"])
        after = float(bars[-1]["close"])
        win = judge(str(p.get("action")), entry, after, threshold)
        if win is None:
            continue
        new_rows.append({
            **p,
            "settled_at": datetime.now().isoformat(),
            "price_after": after,
            "return": round((after - entry) / entry, 6),
            "actual_win": bool(win),
        })

    if new_rows:
        try:
            settled_path.parent.mkdir(parents=True, exist_ok=True)
            with open(settled_path, "a", encoding="utf-8") as f:
                for r in new_rows:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"채점 결과 저장 실패: {e}")
            return {"status": "error", "error": str(e)}

    logger.info(f"캘리브레이션 채점: 신규 {len(new_rows)}건 (기존 {skipped}건, 누적 {len(settled) + len(new_rows)}건)")
    return {
        "status": "ok",
        "new": len(new_rows),
        "already": skipped,
        "total_settled": len(settled) + len(new_rows),
    }


def get_tag_win_rates(
    min_samples: int = 5,
    settled_path: Path = SETTLED_PATH,
) -> Dict[str, Any]:
    """조건 태그별 승률 (P12-4).

    P12-3이 기록한 비가격 태그(감성/공시 등)와 실현 결과(actual_win)를 결합해
    **어떤 조건에서 승률이 높은지**를 계산한다.

    ★ 태그가 승률과 상관있다는 증거가 쌓이기 전에는 신호 점수를 바꾸지 않는다.
      (표본 min_samples 미만 태그는 통계에서 제외 — 과신 방지)

    Returns:
        {"tags": {tag: {"n", "wins", "win_rate"}}, "min_samples", "total_settled", "tagged"}
    """
    rows = _read_jsonl(settled_path)
    buckets: Dict[str, Dict[str, int]] = {}
    tagged = 0
    for r in rows:
        tags = r.get("tags") or []
        if not tags:
            continue
        tagged += 1
        win = bool(r.get("actual_win"))
        for tag in tags:
            b = buckets.setdefault(str(tag), {"n": 0, "wins": 0})
            b["n"] += 1
            if win:
                b["wins"] += 1

    out: Dict[str, Any] = {}
    for tag, b in sorted(buckets.items()):
        if b["n"] < min_samples:
            out[tag] = {"n": b["n"], "wins": b["wins"], "win_rate": None,
                        "status": "insufficient_data"}
            continue
        out[tag] = {
            "n": b["n"],
            "wins": b["wins"],
            "win_rate": round(b["wins"] / b["n"], 4),
            "status": "ok",
        }

    return {
        "tags": out,
        "min_samples": min_samples,
        "total_settled": len(rows),
        "tagged": tagged,
    }


def build_tracker(settled_path: Path = SETTLED_PATH) -> CalibrationTracker:
    """settled 기록으로 트래커를 재구성한다(멱등)."""
    tracker = CalibrationTracker()
    for r in _read_jsonl(settled_path):
        try:
            tracker.record(
                regime=str(r.get("regime") or "unknown"),
                confidence=float(r["confidence"]),
                actual_win=bool(r["actual_win"]),
            )
        except (KeyError, TypeError, ValueError):
            continue
    return tracker


def get_calibration_summary(settled_path: Path = SETTLED_PATH) -> Dict[str, Any]:
    """Regime별 캘리브레이션 요약(대시보드/리포트용)."""
    tracker = build_tracker(settled_path)
    out: Dict[str, Any] = {"regimes": {}, "sample_counts": {}, "worst": None}
    try:
        counts = tracker.get_sample_counts()
        out["sample_counts"] = counts
        worst_ece = -1.0
        for regime in tracker.get_all_regimes():
            cal = tracker.get_calibration(regime)
            out["regimes"][regime] = cal
            if cal.get("status") != "insufficient_data":
                ece = float(cal.get("ece", 0.0))
                if ece > worst_ece:
                    worst_ece = ece
                    out["worst"] = {"regime": regime, "ece": ece, "status": cal.get("status")}
    except Exception as e:
        logger.warning(f"캘리브레이션 요약 실패: {e}")
    return out


async def settle_and_feed_ab(horizon: int = DEFAULT_HORIZON) -> Dict[str, Any]:
    """스케줄러 진입점: 채점 → (가능하면) AB 프레임워크에 ECE 피드백."""
    from data.db_manager import DatabaseManager

    db = DatabaseManager()
    try:
        result = await settle_predictions(db, horizon=horizon)
    except Exception as e:
        logger.error(f"캘리브레이션 채점 실패: {e}")
        return {"status": "error", "error": str(e)}
    finally:
        try:
            await db.close()
        except Exception:
            pass

    tracker = build_tracker()
    for regime in tracker.get_all_regimes():
        cal = tracker.get_calibration(regime)
        if cal.get("status") == "insufficient_data":
            continue
        try:
            await tracker.record_ab_result(regime, cal)
        except Exception as e:
            logger.debug(f"AB 피드백 실패({regime}) 무시: {e}")
    return result


def _main() -> int:
    import argparse
    import asyncio
    import sys

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    parser = argparse.ArgumentParser(description="신뢰도 캘리브레이션 폐루프")
    parser.add_argument("--settle", action="store_true", help="미채점 예측 채점")
    parser.add_argument("--report", action="store_true", help="Regime별 캘리브레이션 요약")
    parser.add_argument("--horizon", type=int, default=DEFAULT_HORIZON)
    parser.add_argument("--tags", action="store_true", help="조건 태그별 승률 출력(P12-4)")
    args = parser.parse_args()

    if args.tags:
        t = get_tag_win_rates()
        print(f"채점 {t['total_settled']}건 중 태그 보유 {t['tagged']}건 (표본 기준 {t['min_samples']})")
        if not t["tags"]:
            print("  태그 데이터 없음 — 시그널 발생 후 5거래일 뒤 채점되면 집계됩니다")
        for tag, v in t["tags"].items():
            if v["status"] == "ok":
                print(f"  {tag:14} 승률 {v['win_rate']:.1%} (n={v['n']})")
            else:
                print(f"  {tag:14} 표본 부족 (n={v['n']})")
        return 0

    if args.settle:
        print(asyncio.run(settle_and_feed_ab(horizon=args.horizon)))
        return 0

    summary = get_calibration_summary()
    preds = _read_jsonl(PRED_PATH)
    print(f"예측 기록 {len(preds)}건 | 채점 완료 {sum(summary['sample_counts'].values()) if summary['sample_counts'] else 0}건")
    for regime, cal in summary["regimes"].items():
        if cal.get("status") == "insufficient_data":
            print(f"  {regime}: 표본 부족({cal.get('sample')}건)")
        else:
            print(f"  {regime}: ECE {cal.get('ece')} [{cal.get('status')}] n={cal.get('sample')}")
    if summary.get("worst"):
        w = summary["worst"]
        print(f"최악 캘리브레이션: {w['regime']} (ECE {w['ece']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
