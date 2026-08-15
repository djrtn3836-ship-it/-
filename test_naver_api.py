#!/usr/bin/env python3
"""
test_naver_api.py - 네이버 뉴스 API 테스트 (자동 적응형)
- 여러 엔드포인트와 헤더 조합을 자동으로 시도
- 성공한 조합을 캐시하여 다음 실행 시 재사용
"""

import os
import sys
import json
import asyncio
import aiohttp
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(override=True)

CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")
CACHE_FILE = Path(__file__).parent / "config" / "naver_api_cache.json"

# 🔥 테스트할 엔드포인트와 헤더 조합 (우선순위 순)
ENDPOINTS = [
    # 1. NAVER API HUB (신규) - 올바른 헤더 + Accept
    {
        "url": "https://naverapihub.apigw.ntruss.com/search/v1/news",
        "headers": {
            "X-NCP-APIGW-API-KEY-ID": CLIENT_ID,
            "X-NCP-APIGW-API-KEY": CLIENT_SECRET,
            "Accept": "application/json",
        }
    },
    # 2. NAVER API HUB (신규) - 구형 헤더 (Fallback)
    {
        "url": "https://naverapihub.apigw.ntruss.com/search/v1/news",
        "headers": {
            "X-Naver-Client-Id": CLIENT_ID,
            "X-Naver-Client-Secret": CLIENT_SECRET,
            "Accept": "application/json",
        }
    },
    # 3. 구형 개발자센터 URL - 구형 헤더 (Fallback)
    {
        "url": "https://openapi.naver.com/v1/search/news.json",
        "headers": {
            "X-Naver-Client-Id": CLIENT_ID,
            "X-Naver-Client-Secret": CLIENT_SECRET,
            "Accept": "application/json",
        }
    },
    # 4. 구형 개발자센터 URL - 신형 헤더 (Fallback)
    {
        "url": "https://openapi.naver.com/v1/search/news.json",
        "headers": {
            "X-NCP-APIGW-API-KEY-ID": CLIENT_ID,
            "X-NCP-APIGW-API-KEY": CLIENT_SECRET,
            "Accept": "application/json",
        }
    },
]

def load_cached_config():
    """캐시된 성공 조합 로드"""
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return None

def save_cached_config(config):
    """성공한 조합 캐시 저장"""
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CACHE_FILE, 'w') as f:
            json.dump(config, f, indent=2)
        print(f"💾 성공한 API 조합이 캐시되었습니다: {CACHE_FILE}")
    except Exception as e:
        print(f"⚠️ 캐시 저장 실패: {e}")

async def test_endpoint(session, config, query="삼성전자"):
    """단일 엔드포인트/헤더 조합 테스트"""
    params = {
        "query": query,
        "display": 2,
        "sort": "date",
        "format": "json",  # 🔥 명시적 JSON 요청
    }
    
    try:
        async with session.get(
            config["url"], 
            headers=config["headers"], 
            params=params, 
            timeout=10
        ) as resp:
            if resp.status == 200:
                return {"success": True, "config": config, "status": resp.status}
            else:
                error_text = await resp.text()
                return {"success": False, "config": config, "status": resp.status, "error": error_text[:100]}
    except Exception as e:
        return {"success": False, "config": config, "status": "Exception", "error": str(e)}

async def test_naver_news_api():
    print("\n" + "=" * 60)
    print("📰 네이버 뉴스 API 테스트 (자동 적응형)")
    print("=" * 60)

    if not CLIENT_ID or not CLIENT_SECRET:
        print("❌ .env에 NAVER_CLIENT_ID 또는 NAVER_CLIENT_SECRET이 없습니다.")
        return False

    print(f"✅ Client ID: {CLIENT_ID[:8]}**** (길이: {len(CLIENT_ID)})")
    print(f"✅ Client Secret: {CLIENT_SECRET[:8]}**** (길이: {len(CLIENT_SECRET)})")
    print("")

    # 1. 캐시 확인
    cached = load_cached_config()
    if cached:
        print(f"📦 캐시된 조합 발견: {cached.get('url')}")
        print(f"   헤더: {list(cached.get('headers', {}).keys())}")
        print("   → 캐시된 조합으로 먼저 시도합니다.")
        endpoints_to_try = [cached] + [e for e in ENDPOINTS if e != cached]
    else:
        endpoints_to_try = ENDPOINTS

    print("")
    print("🔍 API 호출 테스트 중... (여러 조합 자동 시도)")
    print("-" * 40)

    async with aiohttp.ClientSession() as session:
        for idx, config in enumerate(endpoints_to_try, 1):
            print(f"\n[{idx}/{len(endpoints_to_try)}] 테스트 중...")
            print(f"   URL: {config['url']}")
            print(f"   헤더: {', '.join(config['headers'].keys())}")
            
            result = await test_endpoint(session, config)
            
            if result["success"]:
                print(f"   📡 응답 코드: {result['status']} ✅")
                print("")
                print("🎉 성공! 올바른 API 조합을 찾았습니다.")
                print("")
                
                # 성공한 조합 캐시 저장
                save_cached_config(config)
                
                # 실제 데이터 출력
                try:
                    async with session.get(
                        config["url"],
                        headers=config["headers"],
                        params={"query": "삼성전자", "display": 3, "sort": "date", "format": "json"},
                        timeout=10
                    ) as resp:
                        # JSON 디코딩 (text/plain 대비)
                        content_type = resp.headers.get('Content-Type', '')
                        if 'json' in content_type:
                            data = await resp.json()
                        else:
                            text = await resp.text()
                            try:
                                data = json.loads(text)
                            except json.JSONDecodeError:
                                print(f"   ⚠️ JSON 디코딩 실패, 원본: {text[:100]}")
                                return True  # 인증은 성공한 상태

                        items = data.get("items", [])
                        print("📰 상위 3개 뉴스 제목:")
                        for i, item in enumerate(items, 1):
                            title = item.get("title", "").replace("<b>", "").replace("</b>", "")
                            print(f"   {i}. {title[:60]}...")
                            print(f"      출처: {item.get('originallink', 'N/A')}")
                except Exception as e:
                    print(f"   데이터 출력 중 오류: {e}")
                
                print("")
                print("✅ API 키가 정상 작동합니다!")
                return True
            else:
                if result["status"] == 401:
                    print(f"   📡 응답 코드: 401 ❌ (인증 실패)")
                elif result["status"] == 404:
                    print(f"   📡 응답 코드: 404 ❌ (URL 없음)")
                else:
                    print(f"   📡 응답 코드: {result['status']} ❌")

    # 모든 조합 실패
    print("")
    print("=" * 60)
    print("❌ 모든 API 조합이 실패했습니다.")
    print("")
    print("🔍 확인해볼 사항:")
    print("   1. NAVER API HUB에서 Application이 '사용' 상태인지 확인")
    print("   2. '뉴스' API가 활성화되어 있는지 확인")
    print("   3. Client Secret을 재발급하여 .env에 다시 입력")
    print("   4. NAVER API HUB 등록 후 5~10분 정도 대기")
    print("=" * 60)
    return False

if __name__ == "__main__":
    result = asyncio.run(test_naver_news_api())
    sys.exit(0 if result else 1)