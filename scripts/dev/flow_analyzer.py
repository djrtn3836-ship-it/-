# flow_analyzer.py
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent

# 핵심 흐름: scanner_main.py → app/main.py → orchestrator
flows = [
    ('scanner_main.py', '스캐너 진입점'),
    ('app/main.py', '메인 오케스트레이터'),
    ('application/analysis/signal_pipeline.py', '신호 생성 (872줄)'),
    ('decision/hybrid_decider.py', '의사결정 엔진 (352줄)'),
    ('orchestrator/event_bus.py', '이벤트 시스템 (164줄)'),
    ('orchestrator/exit_manager.py', '손절/익절 관리'),
    ('data/', '데이터 수집 계층'),
    ('core/', '핵심 유틸 계층'),
]

print("=== GenSpark V10 DDD 데이터 플로우 ===\n")
for file, desc in flows:
    path = PROJECT_ROOT / file
    if path.exists():
        if path.is_file():
            lines = len(open(path, encoding='utf-8', errors='ignore').readlines())
            print(f"✅ {desc:30} | {file:50} | {lines:4} 줄")
        else:
            files_count = len(list(path.glob('**/*.py')))
            print(f"📁 {desc:30} | {file:50} | {files_count:4} 파일")
    else:
        print(f"❌ {desc:30} | {file:50} | NOT FOUND")
