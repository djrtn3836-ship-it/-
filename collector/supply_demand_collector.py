"""일별 수급(외국인/기관) 수집 (2026-10-10 Session 106).

배경
----
수급 API(외국인수급/기관수급)는 커넥터에 구현돼 있었지만 **주간 PDF·온디맨드
명령에서만** 쓰였고 판단에는 0으로 들어갔다(`regime_manager`의
`foreigner_futures=0.0` 상수 폴백).
P5/P6 결론: 가격 파생 팩터에는 유의한 알파가 없다. 실제 자금 흐름(수급)은
"가격 외 정보"로서 남은 미개척 경로다.

원칙 준수
--------
수집·저장·태그 기록만 한다(점수/행동 변경 없음). 태그별 승률이 검증되면
그때 판단에 반영한다 — 검증 없는 보정 금지.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Optional, Sequence

from core.logger import setup_logger

logger = setup_logger("supply_demand")


def extract_net(result: Any) -> Optional[float]:
    """수급 응답에서 순매수 수량을 방어적으로 꺼낸다.

    응답 구조가 문서와 다를 수 있어(커넥터에 경고 이력) 여러 형태를 시도한다.
    못 꺼내면 None — **추정 금지**(0으로 채우지 않는다).
    """
    if not isinstance(result, dict) or result.get("error"):
        return None

    def _num(v: Any) -> Optional[float]:
        if isinstance(v, bool):
            return None
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            try:
                return float(v.replace(",", "").replace("+", "").strip())
            except ValueError:
                return None
        return None

    for key in ("net_buy", "net_buy_qty", "foreign_net", "inst_net", "net"):
        n = _num(result.get(key))
        if n is not None:
            return n

    out = result.get("output")
    if isinstance(out, list) and out and isinstance(out[0], dict):
        first = out[0]
        for key in ("net_buy", "net_buy_qty", "trde_qty", "cntr_qty", "net"):
            n = _num(first.get(key))
            if n is not None:
                return n
    return None


async def collect_supply_demand(
    kiwoom: Any,
    db: Any,
    tickers: Sequence[str],
    *,
    date_str: Optional[str] = None,
    delay: float = 0.25,
    max_tickers: int = 200,
) -> int:
    """전 종목 수급을 수집해 DB에 저장한다. 저장 건수 반환.

    - 커넥터 실패/응답 형식 미상은 건너뛴다(예외 전파 없음 — 본류 보호).
    - 거래일 판단은 호출측(bootstrap) 책임.
    """
    if kiwoom is None or db is None or not tickers:
        return 0
    if not date_str:
        date_str = datetime.now().strftime("%Y-%m-%d")

    saved = 0
    targets = list(tickers)[:max_tickers]
    dt8 = date_str.replace("-", "")
    for ticker in targets:
        try:
            # 🔴 2026-10-13 실측: 외국인수급(ka10008)/기관수급(ka10009)은 해당 URI에서
            #   지원하지 않는 API ID였다(return_code=1). 반면 일봉 차트(ka10060) 레코드에
            #   frgnr_invsr(외국인)·orgn(기관) 순매수가 들어 있다 → 일봉을 소스로 사용.
            res = await kiwoom.request_tr(ticker, "일봉", dt=dt8)
            await asyncio.sleep(delay)
        except TypeError:
            # dt 미지원 커넥터(구버전) 호환
            try:
                res = await kiwoom.request_tr(ticker, "일봉")
                await asyncio.sleep(delay)
            except Exception as e:
                logger.debug(f"[{ticker}] 수급 요청 실패(무시): {e}")
                continue
        except Exception as e:
            logger.debug(f"[{ticker}] 수급 요청 실패(무시): {e}")
            continue
        if not res or res.get("error"):
            continue
        # 응답 봉의 날짜가 요청 날짜와 일치할 때만 저장(오라벨 방지)
        if str(res.get("date") or "")[:10] != date_str:
            continue
        f_net = res.get("foreign_net")
        i_net = res.get("inst_net")
        if f_net is None and i_net is None:
            continue
        try:
            n = await db.save_supply_demand(
                [{
                    "ticker": ticker,
                    "date": date_str,
                    "foreign_net": f_net,
                    "institution_net": i_net,
                }]
            )
            saved += int(n or 0)
        except Exception as e:
            logger.debug(f"[{ticker}] 수급 저장 실패(무시): {e}")

    logger.info(f"수급 수집 완료: {saved}건 저장(대상 {len(targets)}종목, {date_str})")
    if saved == 0 and targets:
        logger.warning(
            f"⚠️ 수급 저장 0건(대상 {len(targets)}종목) — API 구조/응답 확인 필요(무음 금지)"
        )
    return saved
