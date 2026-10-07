# -*- coding: utf-8 -*-
"""
data/kiwoom_connector.py - v6.1.8 (Session 43: mypy strict 잔여 오류 완전 제거)

v6.1.8 변경 사항:
    - .post(..., timeout=10) 의 int 리터럴을 aiohttp.ClientTimeout(total=10)으로 교체
      (5곳: request_tr()의 4개 분기 + _refresh_token()) — 순수 타입 수정, 실제 타임아웃
      값(10초)은 완전히 동일하여 런타임 동작 변화 없음.
    - log_error()의 두 번째 인자로 dict를 직접 넘기던 7곳을, 동일한 정보를 문자열로
      포함한 Exception 객체(ValueError/RuntimeError/TimeoutError)로 감싸도록 수정.
      log_error() 자체(오류 레벨 로깅)는 그대로 유지하며 log_event()로 바꾸지 않음
      — 로그 심각도와 함수 의미를 원본과 100% 동일하게 보존.
    - asyncio.wait_for(self._ws.recv(), ...)의 타입 불일치는 mypy가 실제로 오류를
      보고한 단 한 곳(_connect_websocket의 LOGIN 응답 수신부, 원본 540번째 줄)에만
      cast(Awaitable[str], ...)를 적용. _ws_receiver의 동일 패턴은 mypy 오류 목록에
      없었으므로 의도적으로 손대지 않음(검증되지 않은 지점 임의 수정 금지 원칙).
    - 그 외 로직/동작 100% 무변경.
"""

import asyncio
import json
import os
import socket
import time
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, DefaultDict, Dict, List, Optional, cast

import aiohttp
import websockets
from aiohttp.resolver import ThreadedResolver
from dotenv import load_dotenv

from core.blackbox_logger import log_error, log_event, log_raw_data
from core.config import get_config
from core.debug_tower import debug_tower
from core.logger import setup_logger

logger = setup_logger("kiwoom_rest")
config = get_config()

DISCOVERED_KEYS_FILE = Path(__file__).parent.parent / "config" / "discovered_keys.json"

# 🔧 Session 43: int 타임아웃을 aiohttp.ClientTimeout으로 교체 (5개 .post() 호출 공유)
_POST_TIMEOUT_10 = aiohttp.ClientTimeout(total=10)


class AsyncRateLimiter:
    def __init__(self, rate: float, per: float = 1.0) -> None:
        self.rate: float = rate
        self.per: float = per
        self.tokens: float = rate
        self.last_refill: float = time.perf_counter()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.perf_counter()
            elapsed = now - self.last_refill
            refill_amount = elapsed * (self.rate / self.per)
            self.tokens = min(self.rate, self.tokens + refill_amount)
            self.last_refill = now
            if self.tokens < 1:
                wait_time = (1 - self.tokens) / (self.rate / self.per)
                await asyncio.sleep(wait_time)
                now = time.perf_counter()
                elapsed = now - self.last_refill
                self.tokens = min(self.rate, self.tokens + elapsed * (self.rate / self.per))
                self.last_refill = now
                self.tokens -= 1
            else:
                self.tokens -= 1



# 키움 웹소켓 REAL 메시지의 values(숫자 키) → 표준 필드명 매핑
#   0B(주식체결): 10=현재가 11=전일대비 12=등락률 13=누적거래량 14=누적거래대금(백만원)
#                 16=시가 17=고가 18=저가 20=체결시간 27=매도호가 28=매수호가
# 가격 필드: 키움은 '+'/'-'를 "전일 종가 대비 방향" 표시로 붙인다(값의 부호가 아님).
#   예: 000720 하락일 → 시가 "+115100"(전일보다 높음), 저가 "-112500"(전일보다 낮음)
#   → 가격 필드는 abs()로 절댓값 사용. 11(전일대비)/12(등락률)은 부호가 실제 값이므로 유지.
_WS_ABS_FIELDS = frozenset({"price", "open", "high", "low", "ask_price", "bid_price"})

_WS_VALUE_FIELDS: Dict[str, Dict[str, str]] = {
    "0B": {
        "10": "price",
        "11": "change",
        "12": "change_rate",
        "13": "volume",
        "14": "trading_value",
        "16": "open",
        "17": "high",
        "18": "low",
        "20": "tick_time",
        "27": "ask_price",
        "28": "bid_price",
    },
}


def _to_float(value: Any) -> Optional[float]:
    """'+18630' / '-20' / ' 0' 등 부호·공백·콤마 포함 문자열을 float로 변환."""
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


class KiwoomConnectorV512:
    REST_BASE_URL: str = "https://api.kiwoom.com"
    WS_URL: str = "wss://api.kiwoom.com:10000/api/dostk/websocket"

    def __init__(self, rate_limit: float = 5.0) -> None:
        load_dotenv()
        self.api_key: Optional[str] = os.getenv("KIWOOM_APP_KEY") or os.getenv("KIWOOM_API_KEY")
        self.api_secret: Optional[str] = os.getenv("KIWOOM_APP_SECRET") or os.getenv("KIWOOM_SECRET_KEY")
        self.access_token: Optional[str] = None
        self.token_expires_at: float = 0.0

        self._rate_limiters: DefaultDict[str, AsyncRateLimiter] = defaultdict(
            lambda: AsyncRateLimiter(rate=rate_limit, per=1.0)
        )
        self._session: Optional[aiohttp.ClientSession] = None
        self._connector: Optional[aiohttp.TCPConnector] = None

        self._connect_lock = asyncio.Lock()

        self._ws: Optional[Any] = None
        self._ws_task: Optional[asyncio.Task[Any]] = None
        self._realtime_handlers: Dict[str, Callable[..., Any]] = {}
        self._shutdown_event = asyncio.Event()
        self._reconnecting: bool = False

        self._subscribed_items: Dict[str, List[str]] = {}
        self._group_allocator: Dict[str, str] = {}
        self._next_group_no: int = 1
        self._group_max_size: int = 100

        self._is_connected: bool = False
        self._ws_running: bool = False
        self._ws_logged_in: bool = False
        self._silence_timeout: int = config.get_int("ws_silence_timeout", 60)

        self._priority_keys: List[str] = ["ticker", "symbol", "item", "stk_cd", "code", "item_cd"]
        self._discovered_keys: List[str] = self._load_discovered_keys()

        log_event("KIWOOM_INIT", {"version": "v6.1.8", "rate_limit": rate_limit})

    def _load_discovered_keys(self) -> List[str]:
        if DISCOVERED_KEYS_FILE.exists():
            try:
                with open(DISCOVERED_KEYS_FILE) as f:
                    data = json.load(f)
                    return list(data.get("keys", []))
            except Exception:
                return []
        return []

    def _save_discovered_keys(self) -> None:
        try:
            DISCOVERED_KEYS_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(DISCOVERED_KEYS_FILE, "w") as f:
                json.dump({"keys": self._discovered_keys}, f, indent=2)
        except Exception as e:
            log_error("키 저장 실패", e)

    def _extract_ticker(self, data: Dict[str, Any]) -> Optional[str]:
        for key in self._priority_keys:
            if key in data:
                return str(data[key])
        for key in self._discovered_keys:
            if key in data:
                return str(data[key])
        for key, value in data.items():
            if isinstance(value, str) and len(value) >= 6 and value.isdigit():
                lower_key = key.lower()
                if "cd" in lower_key or "code" in lower_key or "ticker" in lower_key or "sym" in lower_key:
                    if key not in self._priority_keys and key not in self._discovered_keys:
                        self._discovered_keys.append(key)
                        self._save_discovered_keys()
                        log_event("NEW_KEY_DISCOVERED", {"key": key, "value": value})
                    return value
        return None

    def _normalize_ws_values(self, data: Dict[str, Any]) -> None:
        """웹소켓 REAL 메시지의 values(숫자 키)를 표준 필드명으로 변환한다.

        키움은 {"values": {"10": "+18630", ...}, "type": "0B"} 형태로 보내지만
        하위 핸들러(RealtimeMonitor 등)는 price/volume 같은 이름 필드를 기대한다.
        이 변환이 없으면 모든 틱이 price=None → 0.0 → 무효 처리된다.
        """
        values = data.get("values")
        if not isinstance(values, dict):
            return
        mapping = _WS_VALUE_FIELDS.get(str(data.get("type") or ""))
        if not mapping:
            return
        for code, field in mapping.items():
            if field in data:
                continue
            raw = values.get(code)
            if raw is None:
                continue
            if field == "tick_time":
                data[field] = str(raw).strip()
            elif field == "volume":
                num = _to_float(raw)
                data[field] = int(num) if num is not None else 0
            else:
                num = _to_float(raw)
                if num is not None:
                    data[field] = abs(num) if field in _WS_ABS_FIELDS else num

    async def _handle_ws_message(self, data: Dict[str, Any]) -> None:
        ticker = self._extract_ticker(data)
        if not ticker:
            keys = list(data.keys())
            if not (set(keys) - {"price", "timestamp", "time"}):
                return
            # 🔧 log_error 2번째 인자는 Exception|None 이어야 함. dict 정보를
            # 그대로 ValueError 문자열에 담아 전달(로그 레벨/의미 100% 동일 유지).
            log_error("파싱실패 - 인식불가 키", ValueError(f"keys={keys}, sample={str(data)[:200]}"))
            return

        data["ticker"] = ticker
        self._normalize_ws_values(data)
        debug_tower.log(
            ticker,
            "WS_RECV",
            {"price": data.get("price"), "keys": list(data.keys())},
            trace_id=f"T-{ticker}-{int(time.time()*1000)}",
        )

        if ticker in self._realtime_handlers:
            try:
                self._realtime_handlers[ticker](data)
            except Exception as e:
                log_error(f"핸들러 오류 ({ticker})", e)
                debug_tower.capture_snapshot(ticker, e, f"WS_HANDLER_{ticker}")
        else:
            logger.debug(f"📩 미등록 종목 데이터: {ticker}")

    async def _ws_receiver(self) -> None:
        logger.info(f"📡 WebSocket 수신 시작 (침묵 감지: {self._silence_timeout}초)")
        log_event("WS_RECEIVER_START", {"timeout": self._silence_timeout})
        try:
            while True:
                try:
                    if self._ws is None:
                        break
                    # 🔧 이 지점은 mypy 오류 목록에 없었으므로 원본 그대로 유지
                    # (self._ws가 Optional[Any]로 선언되어 있어 Any로 추론되므로
                    #  wait_for와 충돌하지 않음 — 검증되지 않은 지점 임의 수정 금지)
                    raw: str = await asyncio.wait_for(self._ws.recv(), timeout=self._silence_timeout)
                    log_raw_data(raw, source="WEBSOCKET")
                    try:
                        data: Dict[str, Any] = json.loads(raw)
                        trnm = data.get("trnm")

                        if trnm == "PING":
                            await self._ws.send(raw)
                            continue
                        if trnm == "LOGIN":
                            continue
                        if trnm == "REG":
                            logger.debug(f"📡 REG 응답: {data}")
                            continue

                        if trnm == "REAL":
                            items = data.get("data", [])
                            if isinstance(items, list):
                                for item in items:
                                    await self._handle_ws_message(item)
                            else:
                                await self._handle_ws_message(data)
                            continue

                        await self._handle_ws_message(data)

                    except json.JSONDecodeError:
                        log_error("JSON 디코딩 오류", ValueError(f"raw={raw[:200]}"))
                    except Exception as e:
                        log_error("메시지 처리 중 오류", e)
                        debug_tower.capture_snapshot("SYSTEM", e, "WS_PROCESS")
                except TimeoutError:
                    if self._subscribed_items and not self._shutdown_event.is_set():
                        log_event("SILENCE_DETECTED", {"seconds": self._silence_timeout})
                        await self._backfill_missing_data()
                    break
        except websockets.ConnectionClosed:
            log_event("WEBSOCKET_CLOSED", {})
        except Exception as e:
            log_error("수신 루프 오류", e)
            debug_tower.capture_snapshot("SYSTEM", e, "WS_RECEIVER")
        finally:
            self._ws_running = False
            if not self._shutdown_event.is_set():
                await self._reconnect_websocket()

    async def _backfill_missing_data(self) -> None:
        if not self._session:
            return
        top_tickers: List[str] = list(self._subscribed_items.keys())[:5]
        log_event("BACKFILL_START", {"count": len(top_tickers)})
        for ticker in top_tickers:
            try:
                result = await self.request_tr(ticker, "현재가")
                if result and "close" in result:
                    mock_data: Dict[str, Any] = {
                        "ticker": ticker,
                        "price": result["close"],
                        "change_rate": 0.0,
                        "timestamp": datetime.now().isoformat(),
                    }
                    await self._handle_ws_message(mock_data)
                    logger.info(f"📡 [백필] {ticker} 현재가 복구: {result['close']}")
                await asyncio.sleep(0.5)
            except Exception as e:
                log_error(f"백필 실패 ({ticker})", e)
                debug_tower.capture_snapshot(ticker, e, "BACKFILL")

    async def _reconnect_websocket(self) -> None:
        if self._reconnecting:
            return
        self._reconnecting = True
        log_event("RECONNECT_START", {})

        async with self._connect_lock:
            await self._reconnect_websocket_impl()

    async def _reconnect_websocket_impl(self) -> None:
        try:
            if self._ws_task and not self._ws_task.done():
                self._ws_task.cancel()
                try:
                    await self._ws_task
                except asyncio.CancelledError:
                    pass
            self._ws_task = None
            self._ws = None

            for attempt in range(1, 6):
                if self._shutdown_event.is_set():
                    break
                delay = 2**attempt
                logger.info(f"🔄 재연결 시도 {attempt}/5 (대기 {delay}초)")
                log_event("RECONNECT_ATTEMPT", {"attempt": attempt, "delay": delay})
                await asyncio.sleep(delay)
                try:
                    if self._session is not None:
                        try:
                            await self._session.close()
                        except Exception:
                            pass
                        finally:
                            self._session = None

                    if self._connector is not None:
                        try:
                            await self._connector.close()
                        except Exception:
                            pass
                        finally:
                            self._connector = None

                    self._connector = aiohttp.TCPConnector(
                        resolver=ThreadedResolver(), use_dns_cache=False, family=socket.AF_INET, ttl_dns_cache=0
                    )
                    self._session = aiohttp.ClientSession(connector=self._connector)

                    if not self.access_token or time.time() > self.token_expires_at:
                        await self._refresh_token(raise_on_fail=True)

                    await self._connect_websocket()

                    if self._subscribed_items:
                        logger.info(f"📡 저장된 {len(self._subscribed_items)}개 종목 REG 재전송")
                        # 재구독 중 _subscribed_items가 변경될 수 있어 스냅샷으로 순회(크래시 방지)
                        for ticker, types in list(self._subscribed_items.items()):
                            handler = self._realtime_handlers.get(ticker)
                            if handler:
                                success = await self._register_with_retry(ticker, handler, types)
                                if not success:
                                    logger.warning(f"⚠️ {ticker} REG 실패")
                                await asyncio.sleep(0.1)
                        logger.info("✅ REG 재전송 완료")

                    log_event("RECONNECT_SUCCESS", {"attempt": attempt})
                    logger.info("✅ WebSocket 재연결 + 재구독 완료")
                    return
                except Exception as e:
                    log_error(f"재연결 실패 ({attempt}/5)", e)
                    debug_tower.capture_snapshot("SYSTEM", e, f"RECONNECT_{attempt}")
                    self.access_token = None
                    continue

            logger.error("❌ WebSocket 재연결 최종 실패")
            log_event("RECONNECT_FATAL", {"final": True})
            self._is_connected = False
        finally:
            self._reconnecting = False

    async def request_tr(
        self, ticker: str, tr_type: str, callback: Optional[Callable[[Dict[str, Any]], None]] = None
    ) -> Dict[str, Any]:
        debug_tower.log(ticker, "TR_REQUEST", {"tr_type": tr_type})
        if self._session is None:
            return {"error": "Session is None"}

        if tr_type == "일봉":
            api_id = "ka10060"
            url = f"{self.REST_BASE_URL}/api/dostk/chart"
            await self._acquire_rate_limit(api_id)
            if not self.access_token or time.time() > self.token_expires_at:
                await self._refresh_token(raise_on_fail=False)
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json;charset=UTF-8",
                "api-id": api_id,
            }
            yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
            body: Dict[str, Any] = {"dt": yesterday, "stk_cd": ticker, "amt_qty_tp": "1", "trde_tp": "0", "unit_tp": "1"}
            try:
                async with self._session.post(url, headers=headers, json=body, timeout=_POST_TIMEOUT_10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        chart_list = data.get("stk_invsr_orgn_chart", [])
                        if chart_list:
                            record = chart_list[0]
                            result: Dict[str, Any] = {
                                "symbol": ticker,
                                "open": float(record.get("open", 0)),
                                "high": float(record.get("high", 0)),
                                "low": float(record.get("low", 0)),
                                "close": float(record.get("cur_prc", 0)),
                                "volume": int(record.get("vol", 0)),
                                "raw": data,
                            }
                            if callback:
                                callback(result)
                            debug_tower.log(ticker, "TR_SUCCESS", {"tr_type": tr_type, "close": result["close"]})
                            return result
                        return {"error": "no_data"}
                    return {"error": str(resp.status)}
            except Exception as e:
                debug_tower.capture_snapshot(ticker, e, f"TR_{tr_type}")
                return {"error": str(e)}

        elif tr_type == "외국인수급":
            api_id = "ka10008"
            url = f"{self.REST_BASE_URL}/api/dostk/foreign"
            await self._acquire_rate_limit(api_id)
            if not self.access_token or time.time() > self.token_expires_at:
                await self._refresh_token(raise_on_fail=False)
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json;charset=UTF-8",
                "api-id": api_id,
            }
            body = {"stk_cd": ticker}
            try:
                async with self._session.post(url, headers=headers, json=body, timeout=_POST_TIMEOUT_10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        net_buy: Any = data.get("net_buy")
                        if net_buy is None:
                            output = data.get("output", [])
                            if output and isinstance(output, list) and len(output) > 0:
                                net_buy = output[0].get("net_buy", 0)
                            else:
                                net_buy = 0
                                logger.warning(f"⚠️ 외국인 수급 응답 구조 예상과 다름: {list(data.keys())}")
                        result = {"symbol": ticker, "net_buy": net_buy, "raw": data}
                        if callback:
                            callback(result)
                        debug_tower.log(ticker, "TR_SUCCESS", {"tr_type": tr_type, "net_buy": net_buy})
                        return result
                    return {"error": str(resp.status)}
            except Exception as e:
                debug_tower.capture_snapshot(ticker, e, f"TR_{tr_type}")
                return {"error": str(e)}

        elif tr_type == "기관수급":
            api_id = "ka10009"
            url = f"{self.REST_BASE_URL}/api/dostk/inst"
            await self._acquire_rate_limit(api_id)
            if not self.access_token or time.time() > self.token_expires_at:
                await self._refresh_token(raise_on_fail=False)
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json;charset=UTF-8",
                "api-id": api_id,
            }
            body = {"stk_cd": ticker}
            try:
                async with self._session.post(url, headers=headers, json=body, timeout=_POST_TIMEOUT_10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        net_buy = data.get("net_buy")
                        if net_buy is None:
                            output = data.get("output", [])
                            if output and isinstance(output, list) and len(output) > 0:
                                net_buy = output[0].get("net_buy", 0)
                            else:
                                net_buy = 0
                                logger.warning(f"⚠️ 기관 수급 응답 구조 예상과 다름: {list(data.keys())}")
                        result = {"symbol": ticker, "net_buy": net_buy, "raw": data}
                        if callback:
                            callback(result)
                        debug_tower.log(ticker, "TR_SUCCESS", {"tr_type": tr_type, "net_buy": net_buy})
                        return result
                    return {"error": str(resp.status)}
            except Exception as e:
                debug_tower.capture_snapshot(ticker, e, f"TR_{tr_type}")
                return {"error": str(e)}

        else:
            if tr_type not in ("현재가",):
                logger.warning(f"⚠️ 미지원 tr_type='{tr_type}' → '현재가'(ka10004)로 폴백됩니다.")
            api_id = "ka10004"
            url = f"{self.REST_BASE_URL}/api/dostk/mrkcond"
            await self._acquire_rate_limit(api_id)
            if not self.access_token or time.time() > self.token_expires_at:
                await self._refresh_token(raise_on_fail=False)
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json;charset=UTF-8",
                "api-id": api_id,
            }
            body = {"stk_cd": ticker}
            try:
                async with self._session.post(url, headers=headers, json=body, timeout=_POST_TIMEOUT_10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        price = float(data.get("buy_fpr_bid", 0) or data.get("sel_fpr_bid", 0))
                        result = {"symbol": ticker, "close": price, "raw": data}
                        if callback:
                            callback(result)
                        debug_tower.log(ticker, "TR_SUCCESS", {"tr_type": tr_type, "close": price})
                        return result
                    return {"error": str(resp.status)}
            except Exception as e:
                debug_tower.capture_snapshot(ticker, e, f"TR_{tr_type}")
                return {"error": str(e)}

    async def connect(self) -> bool:
        debug_tower.log("SYSTEM", "KIWOOM_CONNECT_START", {})
        async with self._connect_lock:
            return await self._connect_impl()

    async def _connect_impl(self) -> bool:
        log_event("CONNECT_START", {})
        logger.info("🔑 키움 REST API 로그인 시도...")
        if not self.api_key or not self.api_secret:
            log_error("API 키 없음", ValueError(f"key={self.api_key}, secret={bool(self.api_secret)}"))
            debug_tower.capture_snapshot("SYSTEM", ValueError("API 키 없음"), "KIWOOM_CONNECT")
            return False

        if self._session is not None:
            try:
                await self._session.close()
            except Exception:
                pass
            self._session = None
        if self._connector is not None:
            try:
                await self._connector.close()
            except Exception:
                pass
            self._connector = None

        self._connector = aiohttp.TCPConnector(
            resolver=ThreadedResolver(), use_dns_cache=False, family=socket.AF_INET, ttl_dns_cache=0
        )
        self._session = aiohttp.ClientSession(connector=self._connector)

        try:
            await self._refresh_token(raise_on_fail=True)
        except Exception as e:
            log_error("토큰 발급 실패", e)
            debug_tower.capture_snapshot("SYSTEM", e, "KIWOOM_CONNECT")
            logger.error(f"❌ 토큰 발급 실패: {e}")
            return False

        try:
            await self._connect_websocket()
        except Exception as e:
            log_error("WebSocket 연결 실패", e)
            debug_tower.capture_snapshot("SYSTEM", e, "KIWOOM_WS")
            logger.error(f"❌ WebSocket 연결 실패: {e}", exc_info=True)
            self.access_token = None
            return False

        self._is_connected = True
        self._shutdown_event.clear()
        log_event("CONNECT_SUCCESS", {})
        logger.info("✅ 키움 REST API 연결 완료")
        debug_tower.log("SYSTEM", "KIWOOM_CONNECT_SUCCESS", {"token": bool(self.access_token)})
        return True

    async def _connect_websocket(self) -> None:
        if not self.access_token or time.time() > self.token_expires_at:
            await self._refresh_token(raise_on_fail=True)

        if not self.access_token:
            raise RuntimeError("Access Token is None after refresh")

        self._ws = await websockets.connect(self.WS_URL, ping_interval=20, ping_timeout=60, close_timeout=10)
        self._ws_running = True
        self._ws_logged_in = False
        login_packet: Dict[str, str] = {"trnm": "LOGIN", "token": self.access_token}
        await self._ws.send(json.dumps(login_packet))
        logger.info("📡 LOGIN 패킷 전송 완료")
        log_event("LOGIN_SENT", {})
        debug_tower.log("SYSTEM", "WS_LOGIN_SENT", {})
        try:
            if self._ws is None:
                raise RuntimeError("WebSocket is None")
            # 🔧 Session 43: mypy가 실제로 오류를 보고한 유일한 지점.
            # self._ws.recv()가 Coroutine[Any, Any, str | bytes]로 좁혀져
            # wait_for가 기대하는 Awaitable[str]과 불일치 → cast로 타입만 명시.
            # 런타임 동작은 완전히 동일(cast는 순수 정적 타입 힌트, 실행 시 아무 영향 없음).
            raw: str = await asyncio.wait_for(
                cast(Awaitable[str], self._ws.recv()), timeout=20
            )
            auth: Dict[str, Any] = json.loads(raw)
            if auth.get("return_code") == 0:
                self._ws_logged_in = True
                self._next_group_no = 1
                self._group_allocator.clear()
                logger.info("✅ WebSocket LOGIN 성공! 그룹 카운터 초기화 (next_group_no=1)")
                log_event("LOGIN_SUCCESS", {})
                debug_tower.log("SYSTEM", "WS_LOGIN_SUCCESS", {})
            else:
                error_msg = str(auth.get("return_msg", "Unknown"))
                log_error("LOGIN 실패", RuntimeError(f"msg={error_msg}"))
                logger.error(f"❌ LOGIN 실패: {error_msg}")
                debug_tower.log("SYSTEM", "WS_LOGIN_FAIL", {"msg": error_msg})
                self.access_token = None
                raise Exception(f"LOGIN failed: {error_msg}")
        except TimeoutError:
            log_error("LOGIN 타임아웃", TimeoutError("LOGIN 응답 대기 시간 초과 (20초)"))
            logger.error("❌ LOGIN 응답 타임아웃 (20초)")
            debug_tower.capture_snapshot("SYSTEM", TimeoutError("LOGIN timeout"), "WS_LOGIN")
            self.access_token = None
            raise
        except websockets.ConnectionClosedOK as e:
            log_error("WebSocket 연결 종료 (LOGIN 실패)", e)
            logger.error(f"❌ WebSocket 연결 종료 (LOGIN 실패): {e}")
            debug_tower.capture_snapshot("SYSTEM", e, "WS_LOGIN")
            self.access_token = None
            raise
        self._ws_task = asyncio.create_task(self._ws_receiver())
        logger.info("📡 WebSocket 연결 및 인증 완료")

    async def _register_with_retry(self, ticker: str, handler: Callable[..., Any], types: List[str]) -> bool:
        for attempt in range(3):
            try:
                await self.register_realtime(ticker, handler, types)
                return True
            except Exception as e:
                if attempt < 2:
                    logger.warning(f"⚠️ {ticker} REG 실패 ({attempt+1}/3), 2초 후 재시도")
                    await asyncio.sleep(2)
                else:
                    log_error(f"REG 최종 실패 ({ticker})", e)
                    debug_tower.capture_snapshot(ticker, e, "REG")
        return False

    async def register_realtime(
        self, ticker: str, handler: Callable[..., Any], types: Optional[List[str]] = None
    ) -> None:
        if types is None:
            types = ["0B"]
        if not self._ws or not self._ws_running:
            logger.warning(f"⚠️ WebSocket 미연결: {ticker} 구독 실패")
            return
        if not self._ws_logged_in:
            logger.warning(f"⚠️ LOGIN 미완료: {ticker} 구독 보류")
            await asyncio.sleep(2)
            if not self._ws_logged_in:
                return

        try:
            grp_no = self._group_allocator.get(ticker)
            if grp_no is None:
                current_group_count = sum(
                    1 for t, g in self._group_allocator.items() if g == str(self._next_group_no)
                )
                if current_group_count >= self._group_max_size:
                    self._next_group_no += 1
                grp_no = str(self._next_group_no)
                self._group_allocator[ticker] = grp_no

            self._realtime_handlers[ticker] = handler
            self._subscribed_items[ticker] = types

            subscribe_msg: Dict[str, Any] = {
                "trnm": "REG",
                "grp_no": grp_no,
                "refresh": "1",
                "data": [{"item": [ticker], "type": types}],
            }
            await self._ws.send(json.dumps(subscribe_msg))
            log_event("REG_SENT", {"ticker": ticker, "group": grp_no})
            logger.info(f"📡 REG 구독: {ticker}, 그룹: {grp_no}")
            debug_tower.log(ticker, "REG_SENT", {"group": grp_no})
        except Exception as e:
            logger.error(f"❌ {ticker} REG 전송 실패: {e}")
            debug_tower.log(ticker, "REG_FAIL", {"error": str(e)})
            raise

    async def _acquire_rate_limit(self, api_id: str) -> None:
        await self._rate_limiters[api_id].acquire()

    async def _refresh_token(self, raise_on_fail: bool = False) -> None:
        if self._session is None:
            logger.error("❌ 세션이 없어 토큰 갱신 불가")
            if raise_on_fail:
                raise RuntimeError("Session is None")
            return

        logger.info("🔄 Access Token 갱신 중...")
        log_event("TOKEN_REFRESH_START", {})
        debug_tower.log("SYSTEM", "TOKEN_REFRESH_START", {})
        try:
            async with self._session.post(
                f"{self.REST_BASE_URL}/oauth2/token",
                json={"grant_type": "client_credentials", "appkey": self.api_key, "secretkey": self.api_secret},
                timeout=_POST_TIMEOUT_10,
            ) as resp:
                if resp.status == 200:
                    data: Dict[str, Any] = await resp.json()
                    self.access_token = data.get("token")
                    if not self.access_token:
                        log_error("토큰 응답 없음", ValueError(f"response_keys={list(data.keys())}"))
                        self.access_token = None
                        debug_tower.capture_snapshot("SYSTEM", ValueError("토큰 응답 없음"), "TOKEN_REFRESH")
                        if raise_on_fail:
                            raise RuntimeError("Token response missing")
                        return
                    self.token_expires_at = time.time() + 3600
                    logger.info("✅ Token 갱신 완료")
                    log_event("TOKEN_REFRESH_SUCCESS", {})
                    debug_tower.log("SYSTEM", "TOKEN_REFRESH_SUCCESS", {})
                else:
                    error_text = await resp.text()
                    log_error(f"토큰 갱신 실패 (HTTP {resp.status})", RuntimeError(f"body={error_text}"))
                    self.access_token = None
                    debug_tower.capture_snapshot("SYSTEM", Exception(f"HTTP {resp.status}"), "TOKEN_REFRESH")
                    if raise_on_fail:
                        raise RuntimeError(f"Token refresh failed: HTTP {resp.status}")
        except Exception as e:
            log_error("토큰 갱신 예외", e)
            debug_tower.capture_snapshot("SYSTEM", e, "TOKEN_REFRESH")
            self.access_token = None
            if raise_on_fail:
                raise

    async def wait_until_ready(self, timeout: float = 10.0) -> bool:
        logger.info(f"⏳ WebSocket 준비 대기 (최대 {timeout}초)...")
        start = time.perf_counter()
        while time.perf_counter() - start < timeout:
            ws_ok = False
            if self._ws is not None:
                try:
                    if hasattr(self._ws, "closed"):
                        ws_ok = not getattr(self._ws, "closed")
                    elif hasattr(self._ws, "open"):
                        ws_ok = bool(getattr(self._ws, "open"))
                    elif hasattr(self._ws, "state"):
                        try:
                            from websockets.protocol import State
                            ws_ok = getattr(self._ws, "state") == State.OPEN
                        except Exception:
                            ws_ok = True
                    else:
                        ws_ok = True
                except Exception:
                    ws_ok = False
            if self._ws is not None and self._ws_running and self._ws_logged_in and ws_ok:
                logger.info("✅ WebSocket 완전 준비 완료")
                log_event("WS_READY", {"elapsed": time.perf_counter() - start})
                debug_tower.log("SYSTEM", "WS_READY", {"elapsed": time.perf_counter() - start})
                return True
            await asyncio.sleep(0.5)
        log_event("WS_READY_TIMEOUT", {"timeout": timeout})
        logger.warning(f"⚠️ WebSocket 준비 타임아웃 ({timeout}초 초과)")
        debug_tower.log("SYSTEM", "WS_READY_TIMEOUT", {"timeout": timeout})
        return False

    async def disconnect(self) -> None:
        self._shutdown_event.set()
        async with self._connect_lock:
            await self._disconnect_impl()

    async def _disconnect_impl(self) -> None:
        logger.info("🔌 키움 REST API 연결 종료 중...")
        log_event("DISCONNECT_START", {})
        debug_tower.log("SYSTEM", "KIWOOM_DISCONNECT_START", {})
        if self._ws and self._ws_running:
            try:
                await self._ws.close()
            except Exception:
                pass
        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
            self._ws_task = None
        if self._session:
            await self._session.close()
            self._session = None
        if self._connector:
            await self._connector.close()
            self._connector = None
        self._is_connected = False
        self._ws_running = False
        self._ws_logged_in = False
        self._realtime_handlers.clear()
        self._subscribed_items.clear()
        self._group_allocator.clear()
        self._reconnecting = False
        log_event("DISCONNECT_COMPLETE", {})
        logger.info("✅ 키움 REST API 연결 종료 완료")
        debug_tower.log("SYSTEM", "KIWOOM_DISCONNECT_COMPLETE", {})

    def is_connected(self) -> bool:
        return self._is_connected

    def get_realtime_count(self) -> int:
        return len(self._realtime_handlers)
