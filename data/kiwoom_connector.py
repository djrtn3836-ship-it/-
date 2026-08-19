"""
data/kiwoom_connector.py - v6.1.4 FINAL (register_realtime 중복 제거)
- 기존 v6.1.3 + register_realtime 재시도 로직 정리
"""

import asyncio
import os
import json
import time
import socket
from typing import Dict, Optional, Callable, Any, List
from datetime import datetime, timedelta
from collections import defaultdict
import aiohttp
import websockets
from dotenv import load_dotenv
from pathlib import Path
from aiohttp.resolver import ThreadedResolver

from core.logger import setup_logger
from core.config import get_config
from core.blackbox_logger import log_raw_data, log_event, log_error
from core.debug_tower import debug_tower

logger = setup_logger("kiwoom_rest")
config = get_config()

DISCOVERED_KEYS_FILE = Path(__file__).parent.parent / "config" / "discovered_keys.json"

class AsyncRateLimiter:
    def __init__(self, rate: float, per: float = 1.0):
        self.rate = rate
        self.per = per
        self.tokens = rate
        self.last_refill = time.perf_counter()
        self._lock = asyncio.Lock()

    async def acquire(self):
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

class KiwoomConnectorV512:
    REST_BASE_URL = "https://api.kiwoom.com"
    WS_URL = "wss://api.kiwoom.com:10000/api/dostk/websocket"

    def __init__(self, rate_limit: float = 5.0):
        load_dotenv()
        self.api_key = os.getenv("KIWOOM_APP_KEY")
        self.api_secret = os.getenv("KIWOOM_APP_SECRET")
        self.access_token = None
        self.token_expires_at = 0

        self._rate_limiters: Dict[str, AsyncRateLimiter] = defaultdict(lambda: AsyncRateLimiter(rate=rate_limit, per=1.0))
        self._session: Optional[aiohttp.ClientSession] = None
        self._connector: Optional[aiohttp.TCPConnector] = None

        self._connect_lock = asyncio.Lock()

        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._ws_task: Optional[asyncio.Task] = None
        self._realtime_handlers: Dict[str, Callable] = {}
        self._shutdown_event = asyncio.Event()
        self._reconnecting = False

        self._subscribed_items: Dict[str, List[str]] = {}
        self._group_allocator: Dict[str, str] = {}
        self._next_group_no = 1
        self._group_max_size = 100

        self._is_connected = False
        self._ws_running = False
        self._ws_logged_in = False
        self._silence_timeout = config.get_int("ws_silence_timeout", 60)

        self._priority_keys = ['ticker', 'symbol', 'item', 'stk_cd', 'code', 'item_cd']
        self._discovered_keys = self._load_discovered_keys()

        log_event("KIWOOM_INIT", {"version": "v6.1.4", "rate_limit": rate_limit})

    # ============================================================
    # 자가 적응 파서 (Dynamic Key Learning)
    # ============================================================
    def _load_discovered_keys(self) -> List[str]:
        if DISCOVERED_KEYS_FILE.exists():
            try:
                with open(DISCOVERED_KEYS_FILE, 'r') as f:
                    data = json.load(f)
                    return data.get('keys', [])
            except:
                return []
        return []

    def _save_discovered_keys(self):
        try:
            DISCOVERED_KEYS_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(DISCOVERED_KEYS_FILE, 'w') as f:
                json.dump({'keys': self._discovered_keys}, f, indent=2)
        except Exception as e:
            log_error("키 저장 실패", e)

    def _extract_ticker(self, data: dict) -> Optional[str]:
        for key in self._priority_keys:
            if key in data:
                return str(data[key])
        for key in self._discovered_keys:
            if key in data:
                return str(data[key])
        for key, value in data.items():
            if isinstance(value, str) and len(value) >= 6 and value.isdigit():
                lower_key = key.lower()
                if 'cd' in lower_key or 'code' in lower_key or 'ticker' in lower_key or 'sym' in lower_key:
                    if key not in self._priority_keys and key not in self._discovered_keys:
                        self._discovered_keys.append(key)
                        self._save_discovered_keys()
                        log_event("NEW_KEY_DISCOVERED", {"key": key, "value": value})
                    return value
        return None

    async def _handle_ws_message(self, data: dict):
        ticker = self._extract_ticker(data)
        if not ticker:
            keys = list(data.keys())
            if not (set(keys) - {'price', 'timestamp', 'time'}):
                return
            log_error(f"파싱실패 - 인식불가 키", {"keys": keys, "sample": str(data)[:200]})
            return

        data['ticker'] = ticker
        debug_tower.log(
            ticker,
            "WS_RECV",
            {"price": data.get('price'), "keys": list(data.keys())},
            trace_id=f"T-{ticker}-{int(time.time()*1000)}"
        )

        if ticker in self._realtime_handlers:
            try:
                self._realtime_handlers[ticker](data)
            except Exception as e:
                log_error(f"핸들러 오류 ({ticker})", e)
                debug_tower.capture_snapshot(ticker, e, f"WS_HANDLER_{ticker}")
        else:
            logger.debug(f"📩 미등록 종목 데이터: {ticker}")

    # ============================================================
    # WebSocket 수신 루프
    # ============================================================
    async def _ws_receiver(self):
        logger.info(f"📡 WebSocket 수신 시작 (침묵 감지: {self._silence_timeout}초)")
        log_event("WS_RECEIVER_START", {"timeout": self._silence_timeout})
        try:
            while True:
                try:
                    raw = await asyncio.wait_for(self._ws.recv(), timeout=self._silence_timeout)
                    log_raw_data(raw, source="WEBSOCKET")
                    try:
                        data = json.loads(raw)
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
                        log_error("JSON 디코딩 오류", {"raw": raw[:200]})
                    except Exception as e:
                        log_error("메시지 처리 중 오류", e)
                        debug_tower.capture_snapshot("SYSTEM", e, "WS_PROCESS")
                except asyncio.TimeoutError:
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

    async def _backfill_missing_data(self):
        if not self._session:
            return
        top_tickers = list(self._subscribed_items.keys())[:5]
        log_event("BACKFILL_START", {"count": len(top_tickers)})
        for ticker in top_tickers:
            try:
                result = await self.request_tr(ticker, "현재가")
                if result and 'close' in result:
                    mock_data = {
                        "ticker": ticker,
                        "price": result['close'],
                        "change_rate": 0.0,
                        "timestamp": datetime.now().isoformat()
                    }
                    await self._handle_ws_message(mock_data)
                    logger.info(f"📡 [백필] {ticker} 현재가 복구: {result['close']}")
                await asyncio.sleep(0.5)
            except Exception as e:
                log_error(f"백필 실패 ({ticker})", e)
                debug_tower.capture_snapshot(ticker, e, "BACKFILL")

    # ============================================================
    # 재연결 로직 (락 적용)
    # ============================================================
    async def _reconnect_websocket(self):
        if self._reconnecting:
            return
        self._reconnecting = True
        log_event("RECONNECT_START", {})

        async with self._connect_lock:
            await self._reconnect_websocket_impl()

    async def _reconnect_websocket_impl(self):
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
                delay = 2 ** attempt
                logger.info(f"🔄 재연결 시도 {attempt}/5 (대기 {delay}초)")
                log_event("RECONNECT_ATTEMPT", {"attempt": attempt, "delay": delay})
                await asyncio.sleep(delay)
                try:
                    if self._session is not None:
                        await self._session.close()
                        self._session = None
                    if self._connector is not None:
                        await self._connector.close()
                        self._connector = None
                    self._connector = aiohttp.TCPConnector(
                        resolver=ThreadedResolver(),
                        use_dns_cache=False,
                        family=socket.AF_INET,
                        ttl_dns_cache=0
                    )
                    self._session = aiohttp.ClientSession(connector=self._connector)

                    if not self.access_token or time.time() > self.token_expires_at:
                        await self._refresh_token(raise_on_fail=True)

                    await self._connect_websocket()

                    if self._subscribed_items:
                        logger.info(f"📡 저장된 {len(self._subscribed_items)}개 종목 REG 재전송")
                        for ticker, types in self._subscribed_items.items():
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

    # ============================================================
    # request_tr
    # ============================================================
    async def request_tr(self, ticker: str, tr_type: str, callback: Optional[Callable] = None) -> Dict:
        debug_tower.log(ticker, "TR_REQUEST", {"tr_type": tr_type})
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
            body = {"dt": yesterday, "stk_cd": ticker, "amt_qty_tp": "1", "trde_tp": "0", "unit_tp": "1"}
            try:
                async with self._session.post(url, headers=headers, json=body, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        chart_list = data.get('stk_invsr_orgn_chart', [])
                        if chart_list:
                            record = chart_list[0]
                            result = {
                                "symbol": ticker,
                                "open": float(record.get('open', 0)),
                                "high": float(record.get('high', 0)),
                                "low": float(record.get('low', 0)),
                                "close": float(record.get('cur_prc', 0)),
                                "volume": int(record.get('vol', 0)),
                                "raw": data
                            }
                            if callback:
                                callback(result)
                            debug_tower.log(ticker, "TR_SUCCESS", {"tr_type": tr_type, "close": result['close']})
                            return result
                        return {"error": "no_data"}
                    return {"error": resp.status}
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
                async with self._session.post(url, headers=headers, json=body, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        net_buy = data.get('net_buy')
                        if net_buy is None:
                            output = data.get('output', [])
                            if output and isinstance(output, list) and len(output) > 0:
                                net_buy = output[0].get('net_buy', 0)
                            else:
                                net_buy = 0
                                logger.warning(f"⚠️ 외국인 수급 응답 구조 예상과 다름: {list(data.keys())}")
                        result = {"symbol": ticker, "net_buy": net_buy, "raw": data}
                        if callback:
                            callback(result)
                        debug_tower.log(ticker, "TR_SUCCESS", {"tr_type": tr_type, "net_buy": net_buy})
                        return result
                    return {"error": resp.status}
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
                async with self._session.post(url, headers=headers, json=body, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        net_buy = data.get('net_buy')
                        if net_buy is None:
                            output = data.get('output', [])
                            if output and isinstance(output, list) and len(output) > 0:
                                net_buy = output[0].get('net_buy', 0)
                            else:
                                net_buy = 0
                                logger.warning(f"⚠️ 기관 수급 응답 구조 예상과 다름: {list(data.keys())}")
                        result = {"symbol": ticker, "net_buy": net_buy, "raw": data}
                        if callback:
                            callback(result)
                        debug_tower.log(ticker, "TR_SUCCESS", {"tr_type": tr_type, "net_buy": net_buy})
                        return result
                    return {"error": resp.status}
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
                async with self._session.post(url, headers=headers, json=body, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        price = float(data.get('buy_fpr_bid', 0) or data.get('sel_fpr_bid', 0))
                        result = {"symbol": ticker, "close": price, "raw": data}
                        if callback:
                            callback(result)
                        debug_tower.log(ticker, "TR_SUCCESS", {"tr_type": tr_type, "close": price})
                        return result
                    return {"error": resp.status}
            except Exception as e:
                debug_tower.capture_snapshot(ticker, e, f"TR_{tr_type}")
                return {"error": str(e)}

    # ============================================================
    # connect()
    # ============================================================
    async def connect(self) -> bool:
        debug_tower.log("SYSTEM", "KIWOOM_CONNECT_START", {})
        async with self._connect_lock:
            return await self._connect_impl()

    async def _connect_impl(self) -> bool:
        log_event("CONNECT_START", {})
        logger.info("🔑 키움 REST API 로그인 시도...")
        if not self.api_key or not self.api_secret:
            log_error("API 키 없음", {"key": self.api_key, "secret": bool(self.api_secret)})
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
            resolver=ThreadedResolver(),
            use_dns_cache=False,
            family=socket.AF_INET,
            ttl_dns_cache=0
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

    # ============================================================
    # _connect_websocket()
    # ============================================================
    async def _connect_websocket(self):
        if not self.access_token or time.time() > self.token_expires_at:
            await self._refresh_token(raise_on_fail=True)

        if not self.access_token:
            raise RuntimeError("Access Token is None after refresh")

        self._ws = await websockets.connect(self.WS_URL, ping_interval=20, ping_timeout=60, close_timeout=10)
        self._ws_running = True
        self._ws_logged_in = False
        login_packet = {"trnm": "LOGIN", "token": self.access_token}
        await self._ws.send(json.dumps(login_packet))
        logger.info("📡 LOGIN 패킷 전송 완료")
        log_event("LOGIN_SENT", {})
        debug_tower.log("SYSTEM", "WS_LOGIN_SENT", {})
        try:
            raw = await asyncio.wait_for(self._ws.recv(), timeout=20)
            auth = json.loads(raw)
            if auth.get("return_code") == 0:
                self._ws_logged_in = True
                self._next_group_no = 1
                self._group_allocator.clear()
                logger.info("✅ WebSocket LOGIN 성공! 그룹 카운터 초기화 (next_group_no=1)")
                log_event("LOGIN_SUCCESS", {})
                debug_tower.log("SYSTEM", "WS_LOGIN_SUCCESS", {})
            else:
                error_msg = auth.get("return_msg", "Unknown")
                log_error("LOGIN 실패", {"msg": error_msg})
                logger.error(f"❌ LOGIN 실패: {error_msg}")
                debug_tower.log("SYSTEM", "WS_LOGIN_FAIL", {"msg": error_msg})
                self.access_token = None
                raise Exception(f"LOGIN failed: {error_msg}")
        except asyncio.TimeoutError:
            log_error("LOGIN 타임아웃", {})
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

    # ============================================================
    # register_realtime (재시도 로직 포함, 중복 제거)
    # ============================================================
    async def _register_with_retry(self, ticker: str, handler: Callable, types: List[str]) -> bool:
        for attempt in range(2):
            try:
                await self.register_realtime(ticker, handler, types)
                return True
            except Exception as e:
                if attempt == 0:
                    logger.warning(f"⚠️ {ticker} REG 실패 (1차), 1초 후 재시도")
                    await asyncio.sleep(1)
                else:
                    log_error(f"REG 최종 실패 ({ticker})", e)
                    debug_tower.capture_snapshot(ticker, e, "REG")
        return False

    async def register_realtime(self, ticker: str, handler: Callable, types: List[str] = None):
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

        # 🔥 재시도 루프 (최대 2회)
        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                grp_no = self._group_allocator.get(ticker)
                if grp_no is None:
                    current_group_count = sum(1 for t, g in self._group_allocator.items() if g == str(self._next_group_no))
                    if current_group_count >= self._group_max_size:
                        self._next_group_no += 1
                    grp_no = str(self._next_group_no)
                    self._group_allocator[ticker] = grp_no

                self._realtime_handlers[ticker] = handler
                self._subscribed_items[ticker] = types

                subscribe_msg = {
                    "trnm": "REG",
                    "grp_no": grp_no,
                    "refresh": "1",
                    "data": [{"item": [ticker], "type": types}]
                }
                await self._ws.send(json.dumps(subscribe_msg))
                log_event("REG_SENT", {"ticker": ticker, "group": grp_no})
                logger.info(f"📡 REG 구독: {ticker}, 그룹: {grp_no}")
                debug_tower.log(ticker, "REG_SENT", {"group": grp_no})
                return  # 성공 시 종료

            except Exception as e:
                if attempt < max_retries:
                    logger.warning(f"⚠️ {ticker} REG 실패 ({attempt+1}/{max_retries+1}), 1초 후 재시도")
                    await asyncio.sleep(1)
                else:
                    logger.error(f"❌ {ticker} REG 최종 실패: {e}")
                    debug_tower.log(ticker, "REG_FAIL", {"error": str(e)})
                    return

    async def _acquire_rate_limit(self, api_id: str):
        await self._rate_limiters[api_id].acquire()

    # ============================================================
    # _refresh_token()
    # ============================================================
    async def _refresh_token(self, raise_on_fail: bool = False):
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
                timeout=10
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    self.access_token = data.get("token")
                    if not self.access_token:
                        log_error("토큰 응답 없음", data)
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
                    log_error(f"토큰 갱신 실패 (HTTP {resp.status})", {"body": error_text})
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
                    if hasattr(self._ws, 'closed'):
                        ws_ok = not self._ws.closed
                    elif hasattr(self._ws, 'open'):
                        ws_ok = self._ws.open
                    elif hasattr(self._ws, 'state'):
                        try:
                            from websockets.protocol import State
                            ws_ok = (self._ws.state == State.OPEN)
                        except:
                            ws_ok = True
                    else:
                        ws_ok = True
                except:
                    ws_ok = False
            if (self._ws is not None and self._ws_running and self._ws_logged_in and ws_ok):
                logger.info("✅ WebSocket 완전 준비 완료")
                log_event("WS_READY", {"elapsed": time.perf_counter() - start})
                debug_tower.log("SYSTEM", "WS_READY", {"elapsed": time.perf_counter() - start})
                return True
            await asyncio.sleep(0.5)
        log_event("WS_READY_TIMEOUT", {"timeout": timeout})
        logger.warning(f"⚠️ WebSocket 준비 타임아웃 ({timeout}초 초과)")
        debug_tower.log("SYSTEM", "WS_READY_TIMEOUT", {"timeout": timeout})
        return False

    # ============================================================
    # disconnect()
    # ============================================================
    async def disconnect(self):
        self._shutdown_event.set()
        async with self._connect_lock:
            await self._disconnect_impl()

    async def _disconnect_impl(self):
        logger.info("🔌 키움 REST API 연결 종료 중...")
        log_event("DISCONNECT_START", {})
        debug_tower.log("SYSTEM", "KIWOOM_DISCONNECT_START", {})
        if self._ws and self._ws_running:
            try:
                await self._ws.close()
            except:
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