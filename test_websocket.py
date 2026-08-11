#!/usr/bin/env python3
"""
test_websocket.py - 키움 WebSocket 연결 테스트 (실전투자용) v5.6.3
- 응답 필드명 수정: access_token → token (키움 실제 응답 규격)
- 토큰 만료 시간 출력 (디버깅)
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

# 프로젝트 루트 경로 설정
PROJECT_ROOT = Path(__file__).parent.absolute()
sys.path.insert(0, str(PROJECT_ROOT))

# ============================================================
# .env 파일 명시적 로드
# ============================================================
from dotenv import load_dotenv

env_path = PROJECT_ROOT / ".env"
if env_path.exists():
    load_dotenv(env_path)
    print(f"✅ .env 파일 로드 완료: {env_path}")
else:
    print(f"⚠️ .env 파일 없음: {env_path}")

try:
    from config.secure_config import load_encrypted_env
    load_encrypted_env()
    print("✅ secure_config 로드 완료")
except ImportError:
    pass
except Exception as e:
    print(f"⚠️ secure_config 오류: {e}")

# ============================================================
# 1. 설정
# ============================================================
WS_URL = "wss://api.kiwoom.com:10000/api/dostk/websocket"
# 모의투자: WS_URL = "wss://mockapi.kiwoom.com:10000/api/dostk/websocket"


# ============================================================
# 2. 🔥 수정: 토큰 발급 함수 (token 필드 추출)
# ============================================================
async def get_access_token():
    """키움 REST API로 Access Token 발급 (공식 규격)"""
    import aiohttp
    
    api_key = os.getenv("KIWOOM_APP_KEY")
    api_secret = os.getenv("KIWOOM_APP_SECRET")
    
    print(f"🔑 API Key 존재 여부: {'✅' if api_key else '❌'} (길이: {len(api_key) if api_key else 0})")
    print(f"🔐 API Secret 존재 여부: {'✅' if api_secret else '❌'} (길이: {len(api_secret) if api_secret else 0})")
    
    if not api_key or not api_secret:
        print("❌ .env에 KIWOOM_APP_KEY와 KIWOOM_APP_SECRET이 필요합니다.")
        return None
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                "https://api.kiwoom.com/oauth2/token",
                json={
                    "grant_type": "client_credentials",
                    "appkey": api_key,
                    "secretkey": api_secret,
                },
                timeout=10
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    # 🔥 [수정] access_token → token
                    token = data.get("token")
                    if token:
                        expires_dt = data.get("expires_dt", "알 수 없음")
                        print(f"✅ Access Token 발급 성공: {token[:30]}...")
                        print(f"⏰ 만료 시간: {expires_dt}")
                        return token
                    else:
                        print(f"❌ 응답에 토큰 없음: {data}")
                        return None
                else:
                    error_text = await resp.text()
                    print(f"❌ Token 발급 실패 (HTTP {resp.status}): {error_text[:200]}")
                    return None
        except aiohttp.ClientError as e:
            print(f"❌ 네트워크 오류: {e}")
            return None
        except Exception as e:
            print(f"❌ Token 요청 오류: {e}")
            return None


# ============================================================
# 3. WebSocket 연결 테스트 (핵심)
# ============================================================
async def test_websocket_connection(token: str):
    if not token:
        print("❌ Access Token이 없습니다.")
        return False

    print("=" * 60)
    print("📡 키움 WebSocket 연결 테스트 (실전투자)")
    print(f"🔗 URL: {WS_URL}")
    print("=" * 60)

    try:
        # ---------- STEP 1: WebSocket 연결 ----------
        print("\n⏳ 1. WebSocket 연결 시도 중...")
        import websockets
        ws = await websockets.connect(
            WS_URL,
            ping_interval=20,
            ping_timeout=60,
            close_timeout=10
        )
        print("✅ WebSocket 연결 성공!")

        # ---------- STEP 2: LOGIN 패킷 ----------
        print("\n⏳ 2. LOGIN 패킷 전송 중...")
        login_packet = {"trnm": "LOGIN", "token": token}
        await ws.send(json.dumps(login_packet))
        print("📤 LOGIN 패킷 전송 완료")

        # ---------- STEP 3: LOGIN 응답 ----------
        print("\n⏳ 3. LOGIN 응답 대기 중... (10초)")
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

        # ---------- STEP 4: REG 구독 ----------
        print("\n⏳ 4. REG 구독 등록 중...")
        reg_packet = {
            "trnm": "REG",
            "grp_no": "1",
            "refresh": "1",
            "data": [
                {
                    "item": ["005930"],
                    "type": ["0B"]
                }
            ]
        }
        await ws.send(json.dumps(reg_packet))
        print("📤 REG 패킷 전송 완료")

        # ---------- STEP 5: REG 응답 ----------
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

        # ---------- STEP 6: 실시간 데이터 수신 (30초) ----------
        print("\n⏳ 6. 실시간 데이터 수신 대기 중... (30초)")
        
        start_time = time.time()
        message_count = 0
        
        try:
            while time.time() - start_time < 30:
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                data = json.loads(raw)
                trnm = data.get("trnm")
                message_count += 1
                
                if trnm == "PING":
                    await ws.send(raw)
                    print(f"📡 PING Echo ({message_count}회)")
                    continue
                
                print(f"📩 수신 데이터 ({message_count}회): {trnm} - {str(data)[:100]}...")
                
        except asyncio.TimeoutError:
            print("⏱️ 5초간 메시지 없음 (장중이 아니면 데이터 없음)")

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
    print("🔑 키움 WebSocket 연결 테스트 (v5.6.3)")
    print("=" * 60)
    
    token = await get_access_token()
    
    if not token:
        print("\n❌ 토큰 발급 실패.")
        print("   📌 확인 사항:")
        print("   1. .env 파일에 KIWOOM_APP_KEY와 KIWOOM_APP_SECRET 정확히 입력")
        print("   2. 키움 개발자센터에서 앱 활성화 상태 확인")
        return
    
    await test_websocket_connection(token)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 사용자 중단")