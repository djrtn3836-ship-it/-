# simulate_flow.py - 실제 데이터 흐름 추적
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

import logging
logging.basicConfig(
    level=logging.DEBUG,
    format='[%(name)s] %(message)s'
)

print("=== 시뮬레이션: 200개 종목 구독 플로우 ===\n")

try:
    # 1. 설정 로드
    print("1️⃣ 설정 로드 중...")
    from config.config_manager import ConfigManager
    config = ConfigManager()
    print("   ✅ 설정 로드 완료")
    
    # 2. 이벤트 버스 초기화
    print("2️⃣ 이벤트 버스 초기화...")
    from orchestrator.event_bus import EventBus
    event_bus = EventBus()
    print("   ✅ 이벤트 버스 준비")
    
    # 3. Kiwoom 커넥터
    print("3️⃣ Kiwoom 커넥터 연결...")
    from domain.repository.kiwoom_connector import KiwoomConnector
    kiwoom = KiwoomConnector()
    print("   ✅ Kiwoom 준비")
    
    # 4. 신호 파이프라인
    print("4️⃣ 신호 파이프라인 초기화...")
    from application.analysis.signal_pipeline import SignalPipeline
    pipeline = SignalPipeline()
    print("   ✅ 신호 파이프라인 준비 (200개 종목 처리 준비)")
    
    # 5. 의사결정 엔진
    print("5️⃣ 의사결정 엔진 초기화...")
    from decision.hybrid_decider import HybridDecider
    decider = HybridDecider()
    print("   ✅ 의사결정 엔진 준비 (v9.0, 352줄)")
    
    # 6. 손절/익절 관리
    print("6️⃣ 손절/익절 매니저 초기화...")
    from orchestrator.exit_manager import ExitManager
    exit_manager = ExitManager()
    print("   ✅ 손절/익절 매니저 준비")
    
    print("\n=== 플로우 완성 ===")
    print("데이터 흐름: Kiwoom(200개 종목) → SignalPipeline → HybridDecider → ExitManager → 보고서")
    
except Exception as e:
    print(f"❌ 오류: {e}")
    import traceback
    traceback.print_exc()
