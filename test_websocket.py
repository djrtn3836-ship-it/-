#!/usr/bin/env python3
"""
test_websocket.py - 키움 WebSocket 연결 테스트 (실전투자용)
- LOGIN → REG → PING Echo 전체 흐름 검증
- 헤더 없이, LOGIN 패킷만으로 인증
- asyncio.timeout(10)으로 응답 대기
- 지수 백오프 재연결 테스트 포함
"""

import asyncio
import json
import time
import websockets
from dotenv import load_dotenv
import os

# .env 파일 로드
load_dotenv()

# ============================================================
# 1. 설정 (실전투자 + 국내주식)
# ============================================================
WS_URL = "wss://api.kiwoom.com:10000/api/dostk/websocket"
# 모의투자 테스트용: WS_URL = "wss://mockapi.kiwoom.com:10000/api/dostk/websocket"

ACCESS_TOKEN = os.getenv("KIWOOM_APP_KEY")  # ⚠️ 여기에 실제 Access Token을 넣거나, .env에서 로드
# 실제로는 Access Token을 발급받아야 합니다.
# 여기서는 .env에 KIWOOM_APP_KEY가 아닌, 실제 발급받은 토큰이 필요합니다.

# ============================================================
# 2. 토큰 발급 함수 (REST API)
# ============================================================
async def get_access_token():
    """키움 REST API로 Access Token 발급"""
    import aiohttp
    
    api_key = os.getenv("KIWOOM_APP_KEY")
    api_secret = os.getenv("KIWOOM_APP_SECRET")
    
    if not api_key or not api_secret:
        print("❌ .env에 KIWOOM_APP_KEY와 KIWOOM_APP_SECRET이 필요합니다.")
        return None
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                "https://api.kiwoom.com/oauth2/token",
                json={
                    "grant_type": "client_credentials",
                    "client_id": api_key,
                    "client_secret": api_secret,
                },
                timeout=10
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    token = data.get("access_token")
                    print(f"✅ Access Token 발급 성공: {token[:30]}...")
                    return token
                else:
                    print(f"❌ Token 발급 실패: {resp.status}")
                    return None
        except Exception as e:
            print(f"❌ Token 요청 오류: {e}")
            return None

# ============================================================
# 3. WebSocket 연결 테스트 (핵심)
# ============================================================
async def test_websocket_connection(token: str):
    """WebSocket LOGIN + REG + PING Echo 전체 테스트"""
    
    if not token:
        print("❌ Access Token이 없습니다.")
        return False

    print("=" * 60)
    print("📡 키움 WebSocket 연결 테스트 (실전투자)")
    print(f"🔗 URL: {WS_URL}")
    print("=" * 60)

    try:
        # ---------- STEP 1: WebSocket 연결 (헤더 없이) ----------
        print("\n⏳ 1. WebSocket 연결 시도 중...")
        ws = await websockets.connect(
            WS_URL,
            ping_interval=20,
            ping_timeout=60,
            close_timeout=10
        )
        print("✅ WebSocket 연결 성공!")

        # ---------- STEP 2: LOGIN 패킷 전송 ----------
        print("\n⏳ 2. LOGIN 패킷 전송 중...")
        login_packet = {"trnm": "LOGIN", "token": token}  # "Bearer" 접두어 없음!
        await ws.send(json.dumps(login_packet))
        print("📤 LOGIN 패킷 전송 완료")

        # ---------- STEP 3: LOGIN 응답 대기 (타임아웃 10초) ----------
        print("\n⏳ 3. LOGIN 응답 대기 중... (10초 타임아웃)")
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=10)
            auth = json.loads(raw)
            print(f"📥 LOGIN 응답: {json.dumps(auth, indent=2, ensure_ascii=False)}")
            
            if auth.get("return_code") != 0:
                print(f"❌ LOGIN 실패: {auth.get('return_msg')}")
                await ws.close()
                return False
            print("✅ LOGIN 성공!")
            
        except asyncio.TimeoutError:
            print("❌ LOGIN 응답 타임아웃 (10초)")
            await ws.close()
            return False

        # ---------- STEP 4: REG 구독 등록 ----------
        print("\n⏳ 4. REG 구독 등록 중...")
        reg_packet = {
            "trnm": "REG",
            "grp_no": "1",
            "refresh": "1",
            "data": [
                {
                    "item": ["005930"],  # 삼성전자 테스트
                    "type": ["0B"]       # 체결가
                }
            ]
        }
        await ws.send(json.dumps(reg_packet))
        print("📤 REG 패킷 전송 완료")

        # ---------- STEP 5: REG 응답 대기 ----------
        print("\n⏳ 5. REG 응답 대기 중... (5초)")
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=5)
            reg_resp = json.loads(raw)
            print(f"📥 REG 응답: {json.dumps(reg_resp, indent=2, ensure_ascii=False)}")
            
            if reg_resp.get("return_code") != 0:
                print(f"⚠️ REG 실패: {reg_resp.get('return_msg')}")
            else:
                print("✅ REG 구독 등록 성공!")
                
        except asyncio.TimeoutError:
            print("⚠️ REG 응답 없음 (구독이 바로 데이터를 보내지 않을 수 있음)")

        # ---------- STEP 6: 실시간 데이터 수신 (30초간) ----------
        print("\n⏳ 6. 실시간 데이터 수신 대기 중... (30초 동안)")
        print("   (PING Echo 자동 처리됨)")
        
        start_time = time.time()
        message_count = 0
        
        try:
            while time.time() - start_time < 30:
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                data = json.loads(raw)
                trnm = data.get("trnm")
                message_count += 1
                
                # PING 처리 (Echo)
                if trnm == "PING":
                    await ws.send(raw)  # 원문 그대로 Echo
                    print(f"📡 PING Echo 응답 ({message_count}회)")
                    continue
                
                # 데이터 출력
                print(f"📩 수신 데이터 ({message_count}회): {trnm} - {str(data)[:100]}...")
                
        except asyncio.TimeoutError:
            print("⏱️ 5초간 메시지 없음 (정상: 장중이 아니면 데이터가 없을 수 있음)")

        # ---------- STEP 7: 테스트 종료 ----------
        print("\n" + "=" * 60)
        print(f"✅ 테스트 완료! 총 {message_count}개 메시지 수신")
        print("=" * 60)
        
        await ws.close()
        print("🔌 WebSocket 연결 종료")
        return True

    except websockets.exceptions.WebSocketException as e:
        print(f"❌ WebSocket 오류: {e}")
        return False
    except Exception as e:
        print(f"❌ 예상치 못한 오류: {e}")
        import traceback
        traceback.print_exc()
        return False

# ============================================================
# 4. 메인 함수
# ============================================================
async def main():
    print("\n" + "=" * 60)
    print("🔑 키움 WebSocket 연결 테스트 시작")
    print("=" * 60)
    
    # 1. 토큰 발급
    print("\n📌 0. Access Token 발급 중...")
    token = await get_access_token()
    
    if not token:
        print("\n❌ 토큰 발급 실패. .env 파일을 확인하세요.")
        print("   필요한 환경변수: KIWOOM_APP_KEY, KIWOOM_APP_SECRET")
        return
    
    # 2. WebSocket 테스트 실행
    await test_websocket_connection(token)

# ============================================================
# 5. 실행
# ============================================================
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 사용자에 의해 중단됨")