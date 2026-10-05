# -*- coding: utf-8 -*-
"""application/analysis/shadow_registry.py - 섀도우 전략 평가 레지스트리 (P8-2).

배경
----
`application/analysis/shadow_mode.py`(422줄, ShadowRunner/ShadowEvaluator)는
작성되어 있었으나 **프로덕션 참조 0건의 고아 모듈**이었다.
본 모듈은 이를 `SignalPipeline.process()`에 배선해,
**프로덕션 시그널과 실험 전략 시그널을 실시간으로 비교 기록**한다.

안전 불변식
-----------
- 섀도우 전략은 **주문을 만들지 않는다**. 시그널 비교 기록만 남긴다.
- 섀도우 실행 실패/타임아웃은 프로덕션 흐름에 **영향을 주지 않는다**
  (`ShadowRunner.run()`이 예외를 ShadowRecord.error로 흡수).
- 기록은 JSONL 파일로 남긴다(DB 스키마/PG 계약을 건드리지 않기 위함).

환경변수
--------
- `SHADOW_MODE_ENABLED` : "false"면 비활성(기본 활성)
- `SHADOW_MAX_RECORDS`  : 메모리 보관 최대 기록 수(기본 500)

사용:
    python -m application.analysis.shadow_registry --summary
"""

from __future__ import annotations

import json
import math
import os
import threading
from collections import deque
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional

from application.analysis.shadow_mode import ShadowEvaluator, ShadowRecord, ShadowRunner
from core.logger import setup_logger

logger = setup_logger("shadow_registry")

# P12-5: 테스트 격리용 — 운영 기록 경로를 환경변수로 덮어쓸 수 있다.
RECORDS_PATH = Path(
    os.getenv("SHADOW_RECORDS_PATH",
              str(Path(__file__).parent.parent.parent / "logs" / "shadow_records.jsonl"))
)
DEFAULT_MAX_RECORDS = 500


def _enabled() -> bool:
    raw = os.getenv("SHADOW_MODE_ENABLED", "true").strip().lower()
    return raw not in ("false", "0", "no", "off")


def _max_records() -> int:
    try:
        return int(os.getenv("SHADOW_MAX_RECORDS", str(DEFAULT_MAX_RECORDS)))
    except (TypeError, ValueError):
        return DEFAULT_MAX_RECORDS


# ═══════════════════════════════════════════════════════════════════
#  기본 실험 전략 — 저변동성(low-vol)
# ═══════════════════════════════════════════════════════════════════
# P5/P6 스캔에서 저변동성(120d)만이 OOS에서 양의 알파 후보였다(α +10.4%, t 0.81, 비유의).
# 유의성 미달이라 실거래 승격은 보류했고, 그 판단을 실데이터로 재검증하는 것이
# 섀도우 레이어의 목적이다.

LOW_VOL_ANN_THRESHOLD = 0.35     # 연환산 변동성 35% 미만이면 '저변동성'


async def low_volatility_shadow(data: Dict[str, Any]) -> Any:
    """저변동성 실험 전략: 변동성이 낮고 추세가 음(-)이 아니면 BUY.

    ⚠️ 계약상 반드시 `async def`여야 한다(ShadowRunner가 await).
    프로덕션과 동일한 입력(data + tech_data)만 사용한다.
    """
    from domain.models.signal import Action, Signal

    ticker = str(data.get("ticker", ""))
    price = float(data.get("price", 0) or 0)
    tech = data.get("tech_data") or {}
    closes = [float(c) for c in (tech.get("closes") or []) if c]

    if len(closes) < 21 or price <= 0:
        return Signal(ticker=ticker, action=Action.HOLD, score=0.5, confidence=0.3,
                      price=price, atr=price * 0.01)

    rets = []
    for prev, cur in zip(closes[-61:], closes[-60:]):
        if prev > 0:
            rets.append(math.log(cur / prev))
    if len(rets) < 20:
        return Signal(ticker=ticker, action=Action.HOLD, score=0.5, confidence=0.3,
                      price=price, atr=price * 0.01)

    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / max(1, len(rets) - 1)
    ann_vol = math.sqrt(var) * math.sqrt(252)

    trend_up = closes[-1] >= sum(closes[-20:]) / 20.0
    low_vol = ann_vol < LOW_VOL_ANN_THRESHOLD

    if low_vol and trend_up:
        score, action, conf = 0.70, Action.BUY, 0.6
    elif low_vol:
        score, action, conf = 0.50, Action.HOLD, 0.4
    else:
        score, action, conf = 0.35, Action.HOLD, 0.5

    return Signal(ticker=ticker, action=action, score=score, confidence=conf,
                  price=price, atr=price * 0.01)


# ═══════════════════════════════════════════════════════════════════
#  레지스트리
# ═══════════════════════════════════════════════════════════════════

class ShadowRegistry:
    """섀도우 전략들을 등록하고, 프로덕션 시그널과 비교 기록을 남긴다."""

    def __init__(self, records_path: Path = RECORDS_PATH) -> None:
        self._runners: List[ShadowRunner] = []
        self._evaluators: Dict[str, ShadowEvaluator] = {}
        self._records_path = records_path
        self._recent: Deque[ShadowRecord] = deque(maxlen=_max_records())
        self._lock = threading.Lock()
        self._stats = {"evaluated": 0, "recorded": 0, "errors": 0, "skipped": 0}

    # ── 등록 ──────────────────────────────────────────────
    def register(self, name: str, fn: Callable[[Dict[str, Any]], Any], timeout: float = 5.0) -> None:
        """섀도우 전략 등록(중복 이름은 무시).

        ⚠️ `fn`은 async callable이어야 한다(ShadowRunner가 await).
        """
        if any(r.strategy_name == name for r in self._runners):
            logger.debug(f"섀도우 전략 중복 등록 무시: {name}")
            return
        self._runners.append(ShadowRunner(name, fn, timeout=timeout))
        self._evaluators[name] = ShadowEvaluator(name)
        logger.info(f"섀도우 전략 등록: {name}")

    def register_defaults(self) -> None:
        self.register("low_vol_120d", low_volatility_shadow)

    # ── 평가 ──────────────────────────────────────────────
    async def evaluate(self, data: Dict[str, Any], production_signal: Any) -> List[ShadowRecord]:
        """등록된 모든 섀도우 전략을 실행하고 기록한다(실패는 흡수)."""
        if not _enabled() or not self._runners:
            self._stats["skipped"] += 1
            return []

        self._stats["evaluated"] += 1
        records: List[ShadowRecord] = []
        for runner in self._runners:
            try:
                record = await runner.run(data, production_signal)
            except Exception as e:                       # 방어적: runner가 흡수하지만 이중 안전
                self._stats["errors"] += 1
                logger.warning(f"섀도우 실행 실패({runner.strategy_name}): {e}")
                continue
            records.append(record)
            if record.error:
                self._stats["errors"] += 1

        if records:
            with self._lock:
                for r in records:
                    self._recent.append(r)
                    self._evaluators[r.strategy_name].record(r)
            self._stats["recorded"] += len(records)
            self._append_jsonl(records)
        return records

    def _append_jsonl(self, records: List[ShadowRecord]) -> None:
        try:
            self._records_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._records_path, "a", encoding="utf-8") as f:
                for r in records:
                    f.write(json.dumps(r.to_dict(), ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning(f"섀도우 기록 저장 실패(무시): {e}")

    # ── 조회 ──────────────────────────────────────────────
    def summary(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"stats": dict(self._stats), "strategies": {}}
        with self._lock:
            for name, ev in self._evaluators.items():
                try:
                    out["strategies"][name] = ev.summary().to_dict()
                except Exception as e:
                    logger.debug(f"{name} 요약 실패(무시): {e}")
            out["recent"] = [r.to_dict() for r in list(self._recent)[-5:]]
        return out

    def recent(self, n: int = 10) -> List[Dict[str, Any]]:
        with self._lock:
            return [r.to_dict() for r in list(self._recent)[-n:]]

    @property
    def strategy_names(self) -> List[str]:
        return [r.strategy_name for r in self._runners]


# ── 모듈 싱글턴 ────────────────────────────────────────────
_registry: Optional[ShadowRegistry] = None


def get_shadow_registry() -> ShadowRegistry:
    """전역 레지스트리(기본 전략 자동 등록)."""
    global _registry
    if _registry is None:
        _registry = ShadowRegistry()
        _registry.register_defaults()
    return _registry


def get_shadow_summary() -> Dict[str, Any]:
    return get_shadow_registry().summary()


# ═══════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════

def _main() -> int:
    import argparse
    import sys

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    parser = argparse.ArgumentParser(description="섀도우 전략 평가 요약")
    parser.add_argument("--summary", action="store_true", help="현재 세션 요약 출력")
    parser.add_argument("--file", action="store_true", help="기록 파일 통계 출력")
    args = parser.parse_args()

    if args.file:
        path = RECORDS_PATH
        if not path.exists():
            print(f"기록 파일 없음: {path}")
            return 1
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        agree = sum(1 for r in rows if r.get("agreement"))
        print(f"기록 {len(rows)}건 | 일치율 {agree / len(rows):.1%}" if rows else "기록 0건")
        by_strategy: Dict[str, List[Dict[str, Any]]] = {}
        for r in rows:
            by_strategy.setdefault(str(r.get("strategy_name")), []).append(r)
        for name, rs in by_strategy.items():
            a = sum(1 for r in rs if r.get("agreement"))
            err = sum(1 for r in rs if r.get("error"))
            print(f"  {name}: {len(rs)}건 | 일치 {a / len(rs):.1%} | 오류 {err}건")
        return 0

    s = get_shadow_summary()
    print(f"활성 전략: {', '.join(get_shadow_registry().strategy_names) or '없음'}")
    print(f"통계: {s['stats']}")
    for name, summary in s.get("strategies", {}).items():
        print(f"  [{name}] {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
