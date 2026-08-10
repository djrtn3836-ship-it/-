"""
test_dart_financials.py - DART 재무제표 조회 테스트 (문자열->숫자 변환 처리)
"""
from data.dart_connector import DartConnector
from dotenv import load_dotenv
import os

load_dotenv()
api_key = os.getenv('DART_API_KEY')

if not api_key:
    print("❌ DART_API_KEY가 .env에 없습니다.")
    exit()

print("=" * 60)
print("📊 DART 재무제표 조회 테스트")
print("=" * 60)

dart = DartConnector(api_key)
corp_code = "00126380"  # 삼성전자 고유번호

print(f"\n📌 [테스트] 삼성전자({corp_code}) 2024년 재무제표 조회 중...\n")

fs = dart.get_financials_sync(corp_code, "2024")

if fs and fs.get('status') == '000':
    print("✅ 재무제표 조회 성공!")
    
    target_accounts = ['매출액', '영업이익', '당기순이익', '자산총계', '부채총계', '자본총계']
    items = fs.get('list', [])
    
    found_count = 0
    for item in items:
        account_name = item.get('account_nm')
        if account_name in target_accounts:
            raw_amount = item.get('thstrm_amount', 'N/A')
            unit = item.get('unit', '')
            
            # 🔥 숫자로 변환 (콤마 제거 후 float 변환)
            if raw_amount != 'N/A' and raw_amount:
                try:
                    # 콤마(,) 제거하고 숫자로 변환
                    clean_amount = raw_amount.replace(',', '')
                    amount_num = float(clean_amount)
                    print(f"   • {account_name}: {amount_num:,.0f} {unit}")
                    found_count += 1
                except (ValueError, TypeError):
                    print(f"   • {account_name}: {raw_amount} {unit} (숫자 변환 실패)")
            else:
                print(f"   • {account_name}: 데이터 없음")
    
    if found_count == 0:
        print("   ⚠️ 주요 재무 항목을 찾을 수 없습니다. (항목명이 다를 수 있음)")
        # 디버깅: 실제 항목명 상위 5개 출력
        print("   📋 실제 수신된 항목명 예시:")
        for item in items[:5]:
            print(f"      - {item.get('account_nm', 'N/A')}")
else:
    print("❌ 재무제표 조회 실패")
    if fs:
        print(f"   • 오류 코드: {fs.get('status')}")
        print(f"   • 오류 메시지: {fs.get('message', '알 수 없음')}")

print("\n" + "=" * 60)
print("🎉 테스트 완료!")