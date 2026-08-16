import asyncio
import aiohttp
import socket
import os
from dotenv import load_dotenv
from aiohttp.resolver import ThreadedResolver  # 🔥 중요!

load_dotenv()

CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")

async def test_naver_api():
    print("\n🔍 네이버 API HUB 뉴스 검색 테스트 (ThreadedResolver 적용)\n")

    if not CLIENT_ID or not CLIENT_SECRET:
        print("❌ .env 파일에 NAVER_CLIENT_ID 또는 NAVER_CLIENT_SECRET가 없습니다.")
        return

    print(f"✅ Client ID: {CLIENT_ID[:8]}****")
    print(f"✅ Client Secret: {CLIENT_SECRET[:8]}****\n")

    url = "https://naverapihub.apigw.ntruss.com/search/v1/news"

    headers = {
        "X-NCP-APIGW-API-KEY-ID": CLIENT_ID,
        "X-NCP-APIGW-API-KEY": CLIENT_SECRET,
        "Accept": "application/json",
    }

    params = {
        "query": "삼성전자",
        "display": 1,
        "sort": "date",
        "format": "json",
    }

    print("📡 API 호출 중...")

    # 🔥 ThreadedResolver로 DNS 문제 해결
    connector = aiohttp.TCPConnector(
        resolver=ThreadedResolver(),  # ← 이 줄이 핵심!
        use_dns_cache=False,
        family=socket.AF_INET,
        ttl_dns_cache=0
    )

    try:
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(url, headers=headers, params=params, timeout=10) as resp:
                print(f"📡 응답 코드: {resp.status}")
                print(f"📡 Content-Type: {resp.headers.get('Content-Type', 'N/A')}")

                text = await resp.text()
                print(f"\n📄 응답 본문 (앞 300자):\n{text[:300]}...")

                if resp.status == 200:
                    print("\n🎉 테스트 성공! API가 정상적으로 작동합니다.")
                else:
                    print(f"\n❌ API 호출 실패 (HTTP {resp.status})")

    except aiohttp.ClientConnectorError as e:
        print(f"❌ 네트워크 연결 오류: {e}")
    except asyncio.TimeoutError:
        print("❌ 요청 시간 초과 (10초)")
    except Exception as e:
        print(f"❌ 예상치 못한 오류 발생: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_naver_api())