# -*- coding: utf-8 -*-
"""application/analysis/nonprice_factors.py - 비가격 팩터 계산 + 조건 태그 (P12-3).

배경
----
P5/P6 검증 결론: **가격기반 팩터에는 통계적으로 유의한 알파가 없다**(롱숏 시장중립 포함, |t| < 2).
차기 유일한 미개척 경로는 **비가격 정보**(감성/공시/수급/ML)다.

과거 자산 조사에서 확인된 참고 구현:
    - APEX bot `signals/filters/news_sentiment.py`: 감성 → 매수 임계값 boost(-2.0~+2.0) 인터페이스
    - evolution `pretrain_v2.py`: 비가격 피처 6종 + 보상 셰이핑
    - QDSS `smart_money_engine.py`: 외국인/기관 개별 스트릭 "쌍끌이"

본 모듈은 그 아이디어를 **현재 프로젝트에 실제로 존재하는 데이터**로 구현한다:
    ✅ 감성  : `SentimentPipeline.get_sentiment()` (캐시 기반, 이미 배선됨)
    ✅ 공시  : `infrastructure/dart.client.DartConnector.get_disclosures()` (환경변수로 on/off)
    ❌ 수급  : 현재 DB/커넥터에 외국인·기관 일별 수급이 없어 **구현하지 않음**
              (없는 값을 추정하지 않는다 — QDSS의 "데이터 없으면 None" 원칙)

산출물
------
`NonPriceFactors`(값 + 조건 태그). 태그는 결정 기록에 남겨
P12-4에서 **조건별 승률**을 계산하는 데 쓰인다.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.logger import setup_logger

logger = setup_logger("nonprice")

# ── 임계값 (근거와 함께) ─────────────────────────────────────
SENT_POS_THRESHOLD = 0.25      # 감성 점수 ≥ +0.25 → 긍정 태그
SENT_NEG_THRESHOLD = -0.25     # 감성 점수 ≤ -0.25 → 부정 태그
NEWS_BUSY_COUNT = 5            # 뉴스 5건 이상 → 관심 집중(과열 가능)
MAX_BOOST = 0.05               # 신호 점수 보정 상한(0~1 스케일의 ±5%)
DART_DAYS = 3                  # 공시 조회 기간(일)
FETCH_TIMEOUT_SEC = 2.0        # 팩터 계산이 본류를 지연시키지 않도록 하는 상한


def dart_enabled() -> bool:
    """DART 공시 팩터 사용 여부(기본 비활성 — 레이트리밋/키 의존)."""
    return os.getenv("NONPRICE_DART_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")


def nonprice_enabled() -> bool:
    """비가격 팩터 전체 on/off (기본 활성)."""
    return os.getenv("NONPRICE_FACTORS_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")


@dataclass
class NonPriceFactors:
    """한 종목의 비가격 팩터 스냅샷."""

    ticker: str
    sentiment_score: Optional[float] = None      # -1.0 ~ +1.0
    sentiment_impact: Optional[float] = None     # 0.0 ~ 1.0
    sentiment_label: str = "unknown"
    news_count: int = 0
    disclosure_count: int = 0
    has_disclosure: bool = False
    tags: List[str] = field(default_factory=list)
    boost: float = 0.0                            # 신호 점수 보정량(±MAX_BOOST)
    error: Optional[str] = None
    computed_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ticker": self.ticker,
            "sentiment_score": self.sentiment_score,
            "sentiment_impact": self.sentiment_impact,
            "sentiment_label": self.sentiment_label,
            "news_count": self.news_count,
            "disclosure_count": self.disclosure_count,
            "has_disclosure": self.has_disclosure,
            "tags": list(self.tags),
            "boost": round(self.boost, 4),
            "error": self.error,
        }


def build_tags(f: NonPriceFactors) -> List[str]:
    """조건 태그 생성 — P12-4 승률 통계의 키가 된다."""
    tags: List[str] = []
    if f.sentiment_score is not None:
        if f.sentiment_score >= SENT_POS_THRESHOLD:
            tags.append("sent_pos")
        elif f.sentiment_score <= SENT_NEG_THRESHOLD:
            tags.append("sent_neg")
        else:
            tags.append("sent_neutral")
        if f.news_count >= NEWS_BUSY_COUNT:
            tags.append("news_busy")
    if f.has_disclosure:
        tags.append("disc_yes")
    else:
        tags.append("disc_no")
    return tags


def compute_boost(f: NonPriceFactors) -> float:
    """감성 → 신호 점수 보정량(±MAX_BOOST). 근거 없는 보정은 하지 않는다.

    - 감성 점수가 없으면 0.0
    - confidence(신뢰도)로 가중해 뉴스 1~2건짜리 잡음을 억제한다.
    """
    if f.sentiment_score is None:
        return 0.0
    impact = f.sentiment_impact if f.sentiment_impact is not None else 0.0
    return round(max(-MAX_BOOST, min(MAX_BOOST, f.sentiment_score * impact * MAX_BOOST)), 4)


async def _sentiment_part(ticker: str, pipeline: Any) -> Dict[str, Any]:
    if pipeline is None:
        return {}
    try:
        result = await asyncio.wait_for(pipeline.get_sentiment(ticker), timeout=FETCH_TIMEOUT_SEC)
    except asyncio.TimeoutError:
        logger.debug(f"[{ticker}] 감성 조회 타임아웃({FETCH_TIMEOUT_SEC}s) — 건너뜀")
        return {}
    except Exception as e:
        logger.debug(f"[{ticker}] 감성 조회 실패(무시): {e}")
        return {}
    if result is None:
        return {}
    return {
        "sentiment_score": float(getattr(result, "score", 0.0)),
        "sentiment_impact": float(getattr(result, "impact_score", 0.0)),
        "sentiment_label": str(getattr(getattr(result, "label", None), "value", "unknown")),
        "news_count": int(getattr(result, "news_count", 0) or 0),
    }


async def _disclosure_part(ticker: str, dart: Any) -> Dict[str, Any]:
    """최근 공시 건수. DART 비활성/실패 시 빈 dict(추정하지 않음)."""
    if dart is None or not dart_enabled():
        return {}
    try:
        from datetime import datetime, timedelta

        corp_code = await asyncio.wait_for(_corp_code_for(ticker, dart), timeout=FETCH_TIMEOUT_SEC)
        if not corp_code:
            return {}
        to_date = datetime.now().strftime("%Y%m%d")
        from_date = (datetime.now() - timedelta(days=DART_DAYS)).strftime("%Y%m%d")
        items = await asyncio.wait_for(
            dart.get_disclosures(corp_code, from_date, to_date), timeout=FETCH_TIMEOUT_SEC * 2
        )
        count = len(items or [])
        return {"disclosure_count": count, "has_disclosure": count > 0}
    except asyncio.TimeoutError:
        logger.debug(f"[{ticker}] 공시 조회 타임아웃 — 건너뜀")
        return {}
    except Exception as e:
        logger.debug(f"[{ticker}] 공시 조회 실패(무시): {e}")
        return {}


async def _corp_code_for(ticker: str, dart: Any) -> Optional[str]:
    """종목코드 → DART 고유번호 매핑(커넥터가 제공하면 사용)."""
    for attr in ("get_corp_code", "corp_code_of"):
        fn = getattr(dart, attr, None)
        if fn is None:
            continue
        try:
            value = fn(ticker)
            if asyncio.iscoroutine(value):
                value = await value
            if value:
                return str(value)
        except Exception as e:
            logger.debug(f"corp_code 조회 실패({attr}): {e}")
    return None


async def compute(
    ticker: str,
    *,
    sentiment_pipeline: Any = None,
    dart: Any = None,
) -> NonPriceFactors:
    """비가격 팩터 계산(실패해도 예외를 던지지 않는다).

    Args:
        ticker: 6자리 종목코드
        sentiment_pipeline: `orchestrator.sentiment_pipeline.SentimentPipeline` (없으면 감성 생략)
        dart: DART 커넥터 (NONPRICE_DART_ENABLED=true일 때만 사용)
    """
    f = NonPriceFactors(ticker=ticker)
    if not nonprice_enabled():
        f.tags = []
        f.error = "disabled"
        return f

    try:
        parts: Dict[str, Any] = {}
        parts.update(await _sentiment_part(ticker, sentiment_pipeline))
        parts.update(await _disclosure_part(ticker, dart))
        for key, value in parts.items():
            setattr(f, key, value)
    except Exception as e:                       # 방어적: 본류 보호
        f.error = str(e)[:200]
        logger.debug(f"[{ticker}] 비가격 팩터 계산 실패(무시): {e}")

    f.tags = build_tags(f)
    f.boost = compute_boost(f)
    return f


def apply_boost(score: float, boost: float) -> float:
    """신호 점수에 보정량을 적용(0~1 클램프)."""
    return max(0.0, min(1.0, float(score) + float(boost)))
