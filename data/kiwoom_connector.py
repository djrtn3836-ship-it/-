"""
data/kiwoom_connector.py - v5.6.7 FINAL (침묵 감지 + REG 재시도)
"""

import asyncio
import os
import json
import time
from typing import Dict, Optional, Callable, Any, List
from datetime import datetime, timedelta
from collections import defaultdict

import aiohttp
import websockets
from dotenv import load_dotenv

from core.logger import setup_logger
from core.config import get_config

logger = setup_logger("kiwoom_rest")
config = get_config()


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

        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._ws_task: Optional[asyncio.Task] = None
        self._realtime_handlers: Dict[str, Callable] = {}
        self._shutdown_event = asyncio.Event()

        self._subscribed_items: Dict[str, List[str]] = {}
        self._group_allocator: Dict[str, str] = {}
        self._next_group_no = 1
        self._group_max_size = 100

        self._is_connected = False
        self._ws_running = False
        self._ws_logged_in = False

        # 🔥 침묵 감지 설정 (60초)
        self._silence_timeout = config.get_int("ws_silence_timeout", 60)

    # ============================================================
    # 1. 연결 및 인증
    # ============================================================
    async def connect(self) -> bool:
        logger.info("🔑 키움 REST API 로그인 시도...")
        if not self.api_key or not self.api_secret:
            logger.error("❌ API Key/Secret이 .env에 없습니다.")
            return False

        if self._session is None:
            self._session = aiohttp.ClientSession()

        # 1) Token 발급
        try:
            async with self._session.post(
                f"{self.REST_BASE_URL}/oauth2/token",
                json={
                    "grant_type": "client_credentials",
                    "appkey": self.api_key,
                    "secretkey": self.api_secret,
                },
                timeout=10
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    self.access_token = data.get("token")
                    if not self.access_token:
                        logger.error(f"❌ 응답에 토큰 없음: {data}")
                        return False
                    self.token_expires_at = time.time() + 3600
                    logger.info("✅ Access Token 발급 성공")
                else:
                    logger.error(f"❌ Token 발급 실패: {resp.status}")
                    return False
        except Exception as e:
            logger.error(f"❌ Token 요청 오류: {e}")
            return False

        # 2) WebSocket 연결
        try:
            await self._connect_websocket()
        except Exception as e:
            logger.error(f"❌ WebSocket 연결 실패: {e}")
            return False

        self._is_connected = True
        self._shutdown_event.clear()
        logger.info("✅ 키움 REST API 연결 완료")
        return True

    # ============================================================
    # 2. WebSocket 연결 (인증 + 침묵 감지)
    # ============================================================
    async def _connect_websocket(self):
        if self._ws_task and not self._ws_task.done():
            return

        # 🔥 토큰 만료 시 갱신
        if not self.access_token or time.time() > self.token_expires_at:
            await self._refresh_token()

        # WebSocket 연결 (헤더 없이)
        self._ws = await websockets.connect(
            self.WS_URL,
            ping_interval=20,
            ping_timeout=60,
            close_timeout=10
        )

        self._ws_running = True
        self._ws_logged_in = False

        # STEP 1: LOGIN
        login_packet = {"trnm": "LOGIN", "token": self.access_token}
        await self._ws.send(json.dumps(login_packet))
        logger.info("📡 LOGIN 패킷 전송 완료")

        # STEP 2: LOGIN 응답 확인
        try:
            raw = await asyncio.wait_for(self._ws.recv(), timeout=10)
            auth = json.loads(raw)
            if auth.get("return_code") == 0:
                self._ws_logged_in = True
                logger.info("✅ WebSocket LOGIN 성공!")
            else:
                error_msg = auth.get("return_msg", "Unknown")
                logger.error(f"❌ LOGIN 실패: {error_msg}")
                raise Exception(f"LOGIN failed: {error_msg}")
        except asyncio.TimeoutError:
            logger.error("❌ LOGIN 응답 타임아웃")
            raise

        # STEP 3: 수신 루프 시작
        self._ws_task = asyncio.create_task(self._ws_receiver())
        logger.info("📡 WebSocket 연결 및 인증 완료")

    # ============================================================
    # 3. 🔥 수신 루프 + 침묵 감지 (60초)
    # ============================================================
    async def _ws_receiver(self):
        logger.info(f"📡 WebSocket 수신 시작 (침묵 감지: {self._silence_timeout}초)")
        try:
            while True:
                try:
                    raw = await asyncio.wait_for(self._ws.recv(), timeout=self._silence_timeout)
                    try:
                        data = json.loads(raw)
                        if data.get("trnm") == "PING":
                            await self._ws.send(raw)
                            logger.debug("📡 PING Echo")
                            continue
                        if data.get("trnm") == "LOGIN":
                            continue
                        if data.get("trnm") == "REG":
                            logger.debug(f"📡 REG 응답: {data}")
                            continue
                        await self._handle_ws_message(data)
                    except json.JSONDecodeError:
                        logger.warning(f"⚠️ 잘못된 메시지: {raw[:100]}")
                    except Exception as e:
                        logger.error(f"⚠️ 처리 오류: {e}")
                except asyncio.TimeoutError:
                    logger.warning(f"⚠️ {self._silence_timeout}초간 메시지 없음 → 연결 종료 및 재접속")
                    break
        except websockets.ConnectionClosed:
            logger.warning("⚠️ WebSocket 연결 종료됨")
        except Exception as e:
            logger.error(f"⚠️ 수신 오류: {e}")
        finally:
            self._ws_running = False
            if not self._shutdown_event.is_set():
                await self._reconnect_websocket()

    # ============================================================
    # 4. 🔥 재연결 + REG 재전송 (REG 실패 시 1회 재시도)
    # ============================================================
    async def _reconnect_websocket(self):
        for attempt in range(1, 6):
            if self._shutdown_event.is_set():
                break
            delay = 2 ** attempt
            logger.info(f"🔄 재연결 시도 {attempt}/5 (대기 {delay}초)")
            await asyncio.sleep(delay)
            try:
                await self._connect_websocket()

                if self._subscribed_items:
                    logger.info(f"📡 저장된 {len(self._subscribed_items)}개 종목 REG 재전송")
                    for ticker, types in self._subscribed_items.items():
                        handler = self._realtime_handlers.get(ticker)
                        if handler:
                            # 🔥 REG 재시도 (최대 1회)
                            success = await self._register_with_retry(ticker, handler, types)
                            if not success:
                                logger.warning(f"⚠️ {ticker} REG 실패 (재시도 후)")
                            await asyncio.sleep(0.1)
                    logger.info("✅ REG 재전송 완료")

                logger.info("✅ WebSocket 재연결 + 재구독 완료")
                return
            except Exception as e:
                logger.warning(f"⚠️ 재연결 실패 ({attempt}/5): {e}")
                continue
        logger.error("❌ WebSocket 재연결 최종 실패")

    # ============================================================
    # 5. 🔥 REG 재시도 래퍼
    # ============================================================
    async def _register_with_retry(self, ticker: str, handler: Callable, types: List[str]) -> bool:
        """REG 전송 및 1회 재시도"""
        for attempt in range(2):
            try:
                await self.register_realtime(ticker, handler, types)
                return True
            except Exception as e:
                if attempt == 0:
                    logger.warning(f"⚠️ {ticker} REG 실패 (1차), 1초 후 재시도")
                    await asyncio.sleep(1)
                else:
                    logger.error(f"❌ {ticker} REG 최종 실패: {e}")
        return False

    # ============================================================
    # 6. 메시지 핸들링
    # ============================================================
    async def _handle_ws_message(self, data: dict):
        ticker = data.get("ticker") or data.get("symbol") or data.get("item")
        if not ticker:
            logger.debug(f"📩 식별자 없는 데이터: {data}")
            return
        if ticker in self._realtime_handlers:
            try:
                self._realtime_handlers[ticker](data)
            except Exception as e:
                logger.error(f"실시간 핸들러 오류 ({ticker}): {e}")
        else:
            logger.debug(f"📩 미등록 종목 데이터: {ticker}")

    # ============================================================
    # 7. 실시간 구독 (REG)
    # ============================================================
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
                logger.error(f"❌ LOGIN 실패로 {ticker} 구독 취소")
                return

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
        logger.info(f"📡 REG 구독: {ticker}, 그룹: {grp_no}")

    async def unregister_realtime(self, ticker: str):
        if ticker in self._realtime_handlers:
            del self._realtime_handlers[ticker]
            self._subscribed_items.pop(ticker, None)
            self._group_allocator.pop(ticker, None)
            logger.info(f"📡 구독 해제: {ticker}")

    # ============================================================
    # 8. TR 요청 (REST API)
    # ============================================================
    async def _acquire_rate_limit(self, api_id: str):
        await self._rate_limiters[api_id].acquire()

    async def request_tr(self, ticker: str, tr_type: str, callback: Optional[Callable] = None) -> Dict:
        api_id_map = {
            "일봉": "ka10060",
            "현재가": "ka10004",
            "외국인수급": "ka10008",
            "기관수급": "ka10009",
        }
        api_id = api_id_map.get(tr_type, "ka10004")
        await self._acquire_rate_limit(api_id)

        if not self.access_token or time.time() > self.token_expires_at:
            await self._refresh_token()

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json;charset=UTF-8"
        }

        # 일봉 (ka10060)
        if tr_type == "일봉":
            yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
            headers["api-id"] = "ka10060"
            body = {"dt": yesterday, "stk_cd": ticker, "amt_qty_tp": "1", "trde_tp": "0", "unit_tp": "1"}
            url = f"{self.REST_BASE_URL}/api/dostk/chart"
            try:
                async with self._session.post(url, headers=headers, json=body, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        chart_list = data.get('stk_invsr_orgn_chart', [])
                        close_price = float(chart_list[0].get('cur_prc', 0)) if chart_list else 0
                        result = {"symbol": ticker, "close": close_price, "raw": data}
                        if callback:
                            callback(result)
                        return result
                    return {"error": resp.status}
            except Exception as e:
                return {"error": str(e)}

        # 현재가 (ka10004)
        elif tr_type == "현재가":
            headers["api-id"] = "ka10004"
            body = {"stk_cd": ticker}
            url = f"{self.REST_BASE_URL}/api/dostk/mrkcond"
            try:
                async with self._session.post(url, headers=headers, json=body, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        price = float(data.get('buy_fpr_bid', 0) or data.get('sel_fpr_bid', 0))
                        result = {"symbol": ticker, "close": price, "raw": data}
                        if callback:
                            callback(result)
                        return result
                    return {"error": resp.status}
            except Exception as e:
                return {"error": str(e)}

        # 외국인 (ka10008)
        elif tr_type == "외국인수급":
            headers["api-id"] = "ka10008"
            body = {"stk_cd": ticker}
            url = f"{self.REST_BASE_URL}/api/dostk/foreign"
            try:
                async with self._session.post(url, headers=headers, json=body, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        result = {"symbol": ticker, "net_buy": data.get('net_buy', 0), "raw": data}
                        if callback:
                            callback(result)
                        return result
                    return {"error": resp.status}
            except Exception as e:
                return {"error": str(e)}

        # 기관 (ka10009)
        elif tr_type == "기관수급":
            headers["api-id"] = "ka10009"
            body = {"stk_cd": ticker}
            url = f"{self.REST_BASE_URL}/api/dostk/inst"
            try:
                async with self._session.post(url, headers=headers, json=body, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        result = {"symbol": ticker, "net_buy": data.get('net_buy', 0), "raw": data}
                        if callback:
                            callback(result)
                        return result
                    return {"error": resp.status}
            except Exception as e:
                return {"error": str(e)}

        else:
            return await self.request_tr(ticker, "현재가", callback)

    # ============================================================
    # 9. 토큰 갱신
    # ============================================================
    async def _refresh_token(self):
        logger.info("🔄 Access Token 갱신 중...")
        try:
            async with self._session.post(
                f"{self.REST_BASE_URL}/oauth2/token",
                json={
                    "grant_type": "client_credentials",
                    "appkey": self.api_key,
                    "secretkey": self.api_secret,
                }
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    self.access_token = data.get("token")
                    self.token_expires_at = time.time() + 3600
                    logger.info("✅ Token 갱신 완료")
                else:
                    logger.error("❌ Token 갱신 실패")
        except Exception as e:
            logger.error(f"❌ Token 갱신 예외: {e}")

    # ============================================================
    # 10. 연결 종료
    # ============================================================
    async def disconnect(self):
        logger.info("🔌 키움 REST API 연결 종료 중...")
        self._shutdown_event.set()
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
        if self._session:
            await self._session.close()
            self._session = None
        self._is_connected = False
        self._ws_running = False
        self._ws_logged_in = False
        self._realtime_handlers.clear()
        self._subscribed_items.clear()
        self._group_allocator.clear()
        logger.info("✅ 키움 REST API 연결 종료 완료")

    # ============================================================
    # 11. 상태 조회
    # ============================================================
    def is_connected(self) -> bool:
        return self._is_connected

    def get_realtime_count(self) -> int:
        return len(self._realtime_handlers)