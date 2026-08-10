"""
data/kiwoom_connector.py - v5.3.1 INSTITUTIONAL (호가잔량 구독 확장)
- WebSocket 실시간 연결 (LOGIN/REG/PING 자동 처리)
- REST API TR 요청 (ka10060: 일봉 종가, ka10004: 현재가 호가)
- [신규] ka10008: 외국인 수급 조회
- [신규] ka10009: 기관 수급 조회
- [신규] register_realtime(types=...) 파라미터 추가 (호가 0A 구독 지원)
- Async Rate Limiter (초당 5회) 내장
- 자동 토큰 갱신 및 재연결 백오프
- 모든 API 호출 예외 처리 및 안전장치 포함 (PDF 생성 중단 방지)
"""

import asyncio
import os
import json
import time
import logging
from typing import Dict, Optional, Callable, Any, List
from datetime import datetime, timedelta

import aiohttp
import websockets
from dotenv import load_dotenv

from core.logger import setup_logger

logger = setup_logger("kiwoom_rest")


# ============================================================
# Async Rate Limiter (토큰 버킷 알고리즘)
# ============================================================
class AsyncRateLimiter:
    """
    비동기 토큰 버킷(Token Bucket) Rate Limiter
    - rate: 초당 허용 요청 수 (예: 5)
    - per: 시간 기준 (기본 1.0초)
    """
    def __init__(self, rate: float, per: float = 1.0):
        self.rate = rate
        self.per = per
        self.tokens = rate
        self.last_refill = time.perf_counter()
        self._lock = asyncio.Lock()

    async def acquire(self):
        """토큰을 획득할 때까지 대기 (Blocking 방식)"""
        async with self._lock:
            now = time.perf_counter()
            elapsed = now - self.last_refill
            refill_amount = elapsed * (self.rate / self.per)
            self.tokens = min(self.rate, self.tokens + refill_amount)
            self.last_refill = now

            if self.tokens < 1:
                wait_time = (1 - self.tokens) / (self.rate / self.per)
                logger.debug(f"⏳ Rate Limit 대기 중... ({wait_time:.3f}초 후 실행)")
                await asyncio.sleep(wait_time)

                now = time.perf_counter()
                elapsed = now - self.last_refill
                self.tokens = min(self.rate, self.tokens + elapsed * (self.rate / self.per))
                self.last_refill = now
                self.tokens -= 1
            else:
                self.tokens -= 1


# ============================================================
# 키움 REST API 커넥터 (메인 클래스)
# ============================================================
class KiwoomConnectorV512:
    """키움 REST API + WebSocket 통합 커넥터 (64비트 호환)"""

    # ============================================================
    # API 기본 설정 (공식 문서 기준)
    # ============================================================
    REST_BASE_URL = "https://api.kiwoom.com"  # 실전
    # 모의투자: REST_BASE_URL = "https://mockapi.kiwoom.com"
    
    WS_URL = "wss://api.kiwoom.com:10000/api/dostk/websocket"
    # 모의투자: WS_URL = "wss://mockapi.kiwoom.com:10000/api/dostk/websocket"

    def __init__(self, rate_limit: float = 5.0):
        """
        :param rate_limit: 초당 최대 TR 요청 횟수 (실전: 5, 모의: 1)
        """
        load_dotenv()

        self.api_key = os.getenv("KIWOOM_APP_KEY")
        self.api_secret = os.getenv("KIWOOM_APP_SECRET")
        self.access_token = None
        self.token_expires_at = 0

        # ⭐ Rate Limiter 초기화
        self._rate_limiter = AsyncRateLimiter(rate=rate_limit, per=1.0)
        logger.info(f"🔒 Rate Limiter 활성화: 초당 {rate_limit}회 (TR 요청)")

        # HTTP 세션
        self._session: Optional[aiohttp.ClientSession] = None

        # WebSocket 관련
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._ws_task: Optional[asyncio.Task] = None
        self._realtime_handlers: Dict[str, Callable] = {}
        self._shutdown_event = asyncio.Event()

        # 상태 플래그
        self._is_connected = False
        self._ws_running = False
        self._ws_logged_in = False

    # ============================================================
    # 1. 연결 및 인증 (OAuth2)
    # ============================================================
    async def connect(self) -> bool:
        """REST API 로그인 및 WebSocket 연결"""
        logger.info("🔑 키움 REST API 로그인 시도...")

        if not self.api_key or not self.api_secret:
            logger.error("❌ API Key/Secret이 .env에 없습니다.")
            return False

        if self._session is None:
            self._session = aiohttp.ClientSession()

        # 1) Access Token 발급
        try:
            async with self._session.post(
                f"{self.REST_BASE_URL}/oauth2/token",
                json={
                    "grant_type": "client_credentials",
                    "client_id": self.api_key,
                    "client_secret": self.api_secret,
                },
                timeout=10
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    self.access_token = data.get("access_token")
                    expires_in = data.get("expires_in", 3600)
                    self.token_expires_at = time.time() + expires_in
                    logger.info("✅ Access Token 발급 성공")
                else:
                    logger.error(f"❌ Token 발급 실패: {resp.status} - {await resp.text()}")
                    return False
        except Exception as e:
            logger.error(f"❌ Token 요청 오류: {e}")
            return False

        # 2) WebSocket 연결 + 로그인 패킷 전송
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
    # 2. WebSocket (실시간 데이터 수신)
    # ============================================================
    async def _connect_websocket(self):
        """WebSocket 연결 및 백그라운드 수신 시작"""
        if self._ws_task and not self._ws_task.done():
            return

        # 헤더에 토큰 포함하여 연결
        try:
            headers = {"Authorization": f"Bearer {self.access_token}"}
            self._ws = await websockets.connect(
                self.WS_URL,
                extra_headers=headers,
                ping_interval=20,
                ping_timeout=60
            )
        except TypeError:
            self._ws = await websockets.connect(
                self.WS_URL,
                additional_headers={"Authorization": f"Bearer {self.access_token}"},
                ping_interval=20,
                ping_timeout=60
            )

        self._ws_running = True
        self._ws_logged_in = False

        # 🔥 WebSocket 연결 직후, 로그인 패킷(LOGIN) 전송
        login_packet = {"trnm": "LOGIN", "token": self.access_token}
        await self._ws.send(json.dumps(login_packet))
        logger.info("📡 WebSocket LOGIN 패킷 전송 완료 (서버 응답 대기 중)")

        # 백그라운드 수신 태스크 시작
        self._ws_task = asyncio.create_task(self._ws_receiver())
        logger.info("📡 WebSocket 연결 성공")

    async def _ws_receiver(self):
        """WebSocket 메시지 수신 루프"""
        logger.info("📡 WebSocket 수신 시작...")
        try:
            async for message in self._ws:
                try:
                    data = json.loads(message)
                    
                    # 🔥 LOGIN 응답 처리
                    if data.get("trnm") == "LOGIN":
                        if data.get("return_code") == 0:
                            self._ws_logged_in = True
                            logger.info("✅ WebSocket 로그인 성공!")
                        else:
                            logger.error(f"❌ WebSocket 로그인 실패: {data.get('return_msg')}")
                    
                    # 🔥 PING 응답 처리 (PING은 그대로 다시 보내기)
                    elif data.get("trnm") == "PING":
                        await self._ws.send(json.dumps(data))
                        logger.debug("📡 PING 응답 전송")
                    
                    else:
                        # 일반 데이터는 핸들러로 라우팅
                        await self._handle_ws_message(data)
                        
                except json.JSONDecodeError:
                    logger.warning(f"⚠️ 잘못된 WebSocket 메시지: {message}")
                except Exception as e:
                    logger.error(f"⚠️ WebSocket 처리 오류: {e}")
        except websockets.ConnectionClosed:
            logger.warning("⚠️ WebSocket 연결 종료됨")
            self._ws_running = False
            if not self._shutdown_event.is_set():
                await self._reconnect_websocket()
        except Exception as e:
            logger.error(f"⚠️ WebSocket 수신 오류: {e}")
            self._ws_running = False

    async def _reconnect_websocket(self):
        """WebSocket 재연결 (백오프 적용)"""
        for attempt in range(1, 6):
            if self._shutdown_event.is_set():
                break
            delay = 2 ** attempt
            logger.info(f"🔄 WebSocket 재연결 시도 {attempt}/5 (대기 {delay}초)")
            await asyncio.sleep(delay)
            try:
                await self._connect_websocket()
                logger.info("✅ WebSocket 재연결 성공")
                return
            except Exception:
                continue
        logger.error("❌ WebSocket 재연결 실패")

    async def _handle_ws_message(self, data: dict):
        """WebSocket 메시지 라우팅 (실제 데이터 처리)"""
        ticker = data.get("ticker") or data.get("symbol") or data.get("item")
        if ticker and ticker in self._realtime_handlers:
            try:
                self._realtime_handlers[ticker](data)
            except Exception as e:
                logger.error(f"실시간 핸들러 오류 ({ticker}): {e}")
        else:
            logger.debug(f"📩 수신 데이터: {data}")

    # ============================================================
    # 3. 🔥 실시간 구독 (REG) - 호가(0A) 구독 지원 추가
    # ============================================================
    async def register_realtime(self, ticker: str, handler: Callable, types: List[str] = None):
        """
        실시간 데이터 구독 요청 (키움 REG 패킷 형식)
        
        Args:
            ticker: 종목코드 (예: "005930")
            handler: 데이터 수신 시 호출할 콜백 함수
            types: 구독할 데이터 타입 리스트 (예: ["0B"] 체결가, ["0A"] 호가, ["0B","0A"] 둘 다)
                   기본값: ["0B"] (기존 동작 유지)
        """
        if types is None:
            types = ["0B"]  # 기본값: 체결가만 구독 (하위 호환성 보장)
        
        if not self._ws or not self._ws_running:
            logger.warning(f"⚠️ WebSocket 미연결: {ticker} 구독 실패")
            return

        if not self._ws_logged_in:
            logger.warning(f"⚠️ WebSocket 로그인 아직 완료되지 않음: {ticker} 구독 보류")
            await asyncio.sleep(2)
            if not self._ws_logged_in:
                logger.error(f"❌ 로그인 실패로 {ticker} 구독 취소")
                return

        self._realtime_handlers[ticker] = handler

        # 🔥 [수정] types를 동적으로 설정하여 호가(0A)도 구독 가능
        subscribe_msg = {
            "trnm": "REG",
            "grp_no": "1",
            "refresh": "1",
            "data": [
                {
                    "item": [ticker],
                    "type": types  # ["0B"] 또는 ["0B", "0A"] 또는 ["0A"]
                }
            ]
        }
        await self._ws.send(json.dumps(subscribe_msg))
        logger.info(f"📡 실시간 구독 등록 요청 (REG): {ticker}, 타입: {types}")

    async def unregister_realtime(self, ticker: str):
        """실시간 구독 해제"""
        if ticker in self._realtime_handlers:
            del self._realtime_handlers[ticker]
            logger.info(f"📡 실시간 구독 해제: {ticker}")

    # ============================================================
    # 4. TR 요청 (REST API) - 🔥 v5.3.0 수급 TR 포함 완성
    # ============================================================
    async def request_tr(self, ticker: str, tr_type: str, callback: Optional[Callable] = None) -> Dict:
        """
        TR 요청 (REST API)
        - tr_type == "일봉" : ka10060 (종목별투자자기관별차트) → 종가(Close) 획득
        - tr_type == "현재가" 또는 기타 : ka10004 (주식호가) → 매수/매도 최우선 호가 획득
        - tr_type == "외국인수급" : ka10008 (주식외국인종목별매매동향) → 외국인 순매수/매도
        - tr_type == "기관수급" : ka10009 (주식기관요청) → 기관 순매수/매도
        """
        # 1. Rate Limit 대기
        await self._rate_limiter.acquire()
        
        # 2. 토큰 갱신 체크
        if not self.access_token or time.time() > self.token_expires_at:
            await self._refresh_token()

        # 3. 공통 헤더
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json;charset=UTF-8"
        }

        # ================================================
        # CASE 1: 일봉 종가 조회 (ka10060)
        # ================================================
        if tr_type == "일봉":
            yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
            headers["api-id"] = "ka10060"
            
            body = {
                "dt": yesterday,
                "stk_cd": ticker,
                "amt_qty_tp": "1",   # 금액
                "trde_tp": "0",      # 순매수
                "unit_tp": "1"       # 단주
            }
            url = f"{self.REST_BASE_URL}/api/dostk/chart"

            try:
                logger.info(f"📊 [ka10060] 종가 조회: {ticker} ({yesterday})")
                async with self._session.post(url, headers=headers, json=body, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        chart_list = data.get('stk_invsr_orgn_chart', [])
                        if chart_list and len(chart_list) > 0:
                            close_price = float(chart_list[0].get('cur_prc', 0))
                        else:
                            logger.warning(f"⚠️ {ticker} 차트 데이터 없음 (비거래일 가능)")
                            close_price = 0

                        result = {"symbol": ticker, "close": close_price, "date": yesterday, "raw": data}
                        if callback:
                            callback(result)
                        return result
                    else:
                        error_text = await resp.text()
                        logger.error(f"❌ ka10060 실패 ({ticker}): {resp.status} - {error_text[:100]}")
                        return {"error": resp.status, "message": error_text, "symbol": ticker}
            except Exception as e:
                logger.error(f"❌ ka10060 예외: {e}")
                return {"error": str(e), "symbol": ticker}

        # ================================================
        # CASE 2: 현재가 호가 조회 (ka10004)
        # ================================================
        elif tr_type == "현재가":
            headers["api-id"] = "ka10004"
            body = {"stk_cd": ticker}
            url = f"{self.REST_BASE_URL}/api/dostk/mrkcond"

            try:
                logger.info(f"📊 [ka10004] 현재가 조회: {ticker}")
                async with self._session.post(url, headers=headers, json=body, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        # 매수 최우선 호가 → 없으면 매도 최우선 호가
                        current_price = data.get('buy_fpr_bid')
                        if current_price:
                            current_price = float(current_price)
                        else:
                            current_price = float(data.get('sel_fpr_bid', 0))

                        result = {
                            "symbol": ticker,
                            "close": current_price,  # FeedbackLearner 호환성 유지
                            "buy_price": data.get('buy_fpr_bid'),
                            "sell_price": data.get('sel_fpr_bid'),
                            "raw": data
                        }
                        if callback:
                            callback(result)
                        return result
                    else:
                        error_text = await resp.text()
                        logger.error(f"❌ ka10004 실패 ({ticker}): {resp.status} - {error_text[:100]}")
                        return {"error": resp.status, "message": error_text, "symbol": ticker}
            except Exception as e:
                logger.error(f"❌ ka10004 예외: {e}")
                return {"error": str(e), "symbol": ticker}

        # ================================================
        # CASE 3: 🔥 신규 외국인 수급 조회 (ka10008)
        # ================================================
        elif tr_type == "외국인수급":
            headers["api-id"] = "ka10008"
            body = {"stk_cd": ticker}
            # 공식 문서 기준 URL (실제 키움 API 엔드포인트 확인 필요)
            url = f"{self.REST_BASE_URL}/api/dostk/foreign"

            try:
                logger.info(f"📊 [ka10008] 외국인 수급 조회: {ticker}")
                async with self._session.post(url, headers=headers, json=body, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        result = {
                            "symbol": ticker,
                            "net_buy": data.get('net_buy', 0),
                            "net_sell": data.get('net_sell', 0),
                            "total_foreign": data.get('total_foreign', 0),
                            "raw": data
                        }
                        if callback:
                            callback(result)
                        return result
                    else:
                        error_text = await resp.text()
                        logger.warning(f"⚠️ ka10008 실패 ({ticker}): {resp.status} - {error_text[:100]}")
                        return {"error": resp.status, "message": error_text, "symbol": ticker}
            except Exception as e:
                logger.warning(f"⚠️ ka10008 예외 ({ticker}): {e} → 수급 데이터 제외")
                return {"error": str(e), "symbol": ticker}

        # ================================================
        # CASE 4: 🔥 신규 기관 수급 조회 (ka10009)
        # ================================================
        elif tr_type == "기관수급":
            headers["api-id"] = "ka10009"
            body = {"stk_cd": ticker}
            url = f"{self.REST_BASE_URL}/api/dostk/inst"

            try:
                logger.info(f"📊 [ka10009] 기관 수급 조회: {ticker}")
                async with self._session.post(url, headers=headers, json=body, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        result = {
                            "symbol": ticker,
                            "net_buy": data.get('net_buy', 0),
                            "net_sell": data.get('net_sell', 0),
                            "total_inst": data.get('total_inst', 0),
                            "raw": data
                        }
                        if callback:
                            callback(result)
                        return result
                    else:
                        error_text = await resp.text()
                        logger.warning(f"⚠️ ka10009 실패 ({ticker}): {resp.status} - {error_text[:100]}")
                        return {"error": resp.status, "message": error_text, "symbol": ticker}
            except Exception as e:
                logger.warning(f"⚠️ ka10009 예외 ({ticker}): {e} → 수급 데이터 제외")
                return {"error": str(e), "symbol": ticker}

        # ================================================
        # CASE Fallback: 알 수 없는 tr_type
        # ================================================
        else:
            logger.warning(f"⚠️ 알 수 없는 tr_type: {tr_type} ({ticker}) → 현재가(ka10004)로 폴백")
            return await self.request_tr(ticker, "현재가", callback)

    # ============================================================
    # 5. 토큰 갱신
    # ============================================================
    async def _refresh_token(self):
        """Access Token 갱신"""
        logger.info("🔄 Access Token 갱신 중...")
        try:
            async with self._session.post(
                f"{self.REST_BASE_URL}/oauth2/token",
                json={
                    "grant_type": "client_credentials",
                    "client_id": self.api_key,
                    "client_secret": self.api_secret,
                }
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    self.access_token = data.get("access_token")
                    self.token_expires_at = time.time() + data.get("expires_in", 3600)
                    logger.info("✅ Token 갱신 완료")
                else:
                    logger.error("❌ Token 갱신 실패")
        except Exception as e:
            logger.error(f"❌ Token 갱신 예외: {e}")

    # ============================================================
    # 6. 연결 종료
    # ============================================================
    async def disconnect(self):
        """연결 종료"""
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
        logger.info("✅ 키움 REST API 연결 종료 완료")

    # ============================================================
    # 7. 상태 조회
    # ============================================================
    def is_connected(self) -> bool:
        return self._is_connected

    def get_realtime_count(self) -> int:
        return len(self._realtime_handlers)