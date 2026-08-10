"""
Kiwoom REST API Connector v5.1.2 — 64비트 지원, 비동기 HTTP/WebSocket
🔒 Rate Limiter 내장: 초당 5회 (실전) / 1회 (모의) 엄격 준수
"""

import asyncio
import os
import json
import time
import logging
from typing import Dict, Optional, Callable, Any

import aiohttp
import websockets
from dotenv import load_dotenv

from core.logger import setup_logger

logger = setup_logger("kiwoom_rest")


class AsyncRateLimiter:
    """
    비동기 토큰 버킷(Token Bucket) Rate Limiter
    - rate: 초당 허용 요청 수 (예: 5)
    - per: 시간 기준 (기본 1.0초)
    """
    def __init__(self, rate: float, per: float = 1.0):
        self.rate = rate                  # 초당 최대 요청 수
        self.per = per                    # 기준 시간 (초)
        self.tokens = rate                # 현재 보유 토큰 수 (처음은 최대로 채움)
        self.last_refill = time.perf_counter()
        self._lock = asyncio.Lock()

    async def acquire(self):
        """
        토큰을 획득할 때까지 대기합니다.
        - 토큰이 부족하면 충전될 때까지 asyncio.sleep으로 대기합니다.
        """
        async with self._lock:
            now = time.perf_counter()
            elapsed = now - self.last_refill

            # 경과 시간만큼 토큰 충전 (최대 용량 초과 불가)
            refill_amount = elapsed * (self.rate / self.per)
            self.tokens = min(self.rate, self.tokens + refill_amount)
            self.last_refill = now

            # 토큰이 1개 미만이면 사용 가능할 때까지 대기
            if self.tokens < 1:
                # 1개 토큰이 충전되는 데 필요한 시간 계산
                wait_time = (1 - self.tokens) / (self.rate / self.per)
                logger.debug(f"⏳ Rate Limit 대기 중... ({wait_time:.3f}초 후 실행)")
                await asyncio.sleep(wait_time)

                # 대기 후 다시 시간 계산 및 토큰 차감
                now = time.perf_counter()
                elapsed = now - self.last_refill
                self.tokens = min(self.rate, self.tokens + elapsed * (self.rate / self.per))
                self.last_refill = now
                self.tokens -= 1
            else:
                # 토큰 1개 사용
                self.tokens -= 1


class KiwoomConnectorV512:
    """키움 REST API 기반 커넥터 (64비트 호환) - Rate Limiter 내장"""

    # ============================================================
    # API 기본 설정 (🔥 2026-08-10 최종 수정)
    # ============================================================
    REST_BASE_URL = "https://api.kiwoom.com"
    # 실전 WebSocket (포트 10000 포함)
    WS_URL = "wss://api.kiwoom.com:10000/api/dostk/websocket"
    # 모의투자일 경우: WS_URL = "wss://mockapi.kiwoom.com:10000/api/dostk/websocket"

    def __init__(self, rate_limit: float = 5.0):
        """
        :param rate_limit: 초당 최대 TR 요청 횟수 (실전: 5, 모의: 1)
        """
        load_dotenv()

        self.api_key = os.getenv("KIWOOM_APP_KEY")
        self.api_secret = os.getenv("KIWOOM_APP_SECRET")
        self.access_token = None
        self.token_expires_at = 0

        # ⭐ Rate Limiter 초기화 (기본 실전 5회/초)
        self._rate_limiter = AsyncRateLimiter(rate=rate_limit, per=1.0)
        logger.info(f"🔒 Rate Limiter 활성화: 초당 {rate_limit}회 (TR 요청)")

        # HTTP 세션
        self._session: Optional[aiohttp.ClientSession] = None

        # WebSocket 관련
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._ws_task: Optional[asyncio.Task] = None
        self._realtime_handlers: Dict[str, Callable] = {}
        self._shutdown_event = asyncio.Event()

        # TR 요청 콜백
        self._tr_callbacks: Dict[str, Callable] = {}

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
        login_packet = {
            "trnm": "LOGIN",
            "token": self.access_token
        }
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
    # 3. 실시간 구독 (REG)
    # ============================================================

    async def register_realtime(self, ticker: str, handler: Callable, fid_list: str = ""):
        """실시간 데이터 구독 요청 (키움 REG 패킷 형식)"""
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

        # 공식 문서에 명시된 REG 패킷 형식
        subscribe_msg = {
            "trnm": "REG",
            "grp_no": "1",
            "refresh": "1",
            "data": [
                {
                    "item": [ticker],
                    "type": ["0B"]  # 0B: 현재가, 1B: 주문체결 등
                }
            ]
        }
        await self._ws.send(json.dumps(subscribe_msg))
        logger.info(f"📡 실시간 구독 등록 요청 (REG): {ticker}")

    async def unregister_realtime(self, ticker: str):
        """실시간 구독 해제"""
        if ticker in self._realtime_handlers:
            del self._realtime_handlers[ticker]
            logger.info(f"📡 실시간 구독 해제: {ticker}")

    # ============================================================
    # 4. TR 요청 (REST API) - ⭐ Rate Limiter 적용 완료
    # ============================================================

    async def request_tr(self, ticker: str, tr_type: str, callback: Optional[Callable] = None) -> Dict:
        """
        TR 요청 (REST API)
        - 이 메서드는 호출되기 전에 반드시 Rate Limiter를 통과합니다.
        - 초당 설정된 횟수를 초과할 경우 내부적으로 대기(Sleep) 후 실행됩니다.
        """
        # ⭐⭐⭐ Rate Limiter 획득 (서버에 요청을 보내기 전에 대기)
        await self._rate_limiter.acquire()
        
        # 토큰 갱신 체크
        if not self.access_token or time.time() > self.token_expires_at:
            await self._refresh_token()

        # ---------- 실제 API 호출 영역 ----------
        # 현재는 엔드포인트 미확정으로 더미 데이터 반환 (Rate Limiter는 정상 동작 중)
        # 실제 엔드포인트 확인 후 아래 주석을 해제하고 사용하세요.
        
        logger.info(f"📊 TR 요청 실행 (Rate Limit 통과): {tr_type} {ticker}")
        
        # TODO: 실제 키움 REST API 엔드포인트로 교체 필요
        # headers = {"Authorization": f"Bearer {self.access_token}"}
        # url = f"{self.REST_BASE_URL}/api/dostk/..."  # 정확한 경로 필요
        # async with self._session.get(url, params={"symbol": ticker}, headers=headers) as resp:
        #     ...
        
        # 임시 더미 응답 (Rate Limiter 테스트용)
        return {
            "symbol": ticker,
            "tr_type": tr_type,
            "price": 0,
            "message": "REST API 엔드포인트 확인 필요 (Rate Limiter는 정상 작동 중)"
        }

    # ============================================================
    # 5. 토큰 갱신
    # ============================================================

    async def _refresh_token(self):
        """Access Token 갱신"""
        logger.info("🔄 Access Token 갱신 중...")
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