# 자율 AI 퀀트 시스템 - 장기 개선 로드맵

> 최종 갱신: Session 45
> 시스템 정체성: 텔레그램 알림 보조 집사 (자동매매 아님)
>   - 실시간 매수가/매도가/ATR 스탑로스를 텔레그램으로 알림
>   - 최종 매매 판단과 실행은 항상 사용자 본인
>   - OrderExecutor mode="paper" 하드코딩 (실제 주문 없음)

---

## 📌 외부 프로젝트 참고 자산 (세션 간 기억용, Session 44에서 확립)

### [최우선] SNOWBALL-master/src/market_analyzer.py
  위치: C:\Users\hdw38\Desktop\SNOWBALL-master\src\market_analyzer.py
  목적: 텔레그램 알림에 "리스크 스코어 72/100" 직관적 지표 추가
  핵심:
    @dataclass class MarketSignal:
        risk_score: float   # 0~100 종합
        atr_score: float    # 0~30
        rsi_score: float    # 0~25
        bb_score: float     # 0~25
        volume_score: float # 0~20
  이식: telegram_sender.py _format_signal_entry()에 삽입
  주의: ATR 계산 기준이 다를 수 있음 -> kiwoom 기준으로 스케일링 재검토

### [최우선] SNOWBALL-master/src/risk_manager.py
  위치: C:\Users\hdw38\Desktop\SNOWBALL-master\src\risk_manager.py
  목적: core/regime_manager.py 국면 전환 휩쏘 방지
  핵심: 확정 카운터 패턴 (raw 신호 N회 연속 동일해야 공식 전환)
    confirm_count: int = 0 / CONFIRM_THRESHOLD: int = 3
  이식: regime_manager.py 레짐 전환 로직에 "연속 N회 확인 후 전환" 조건 추가
  주의: 우리 시스템은 일봉 기반이라 N=1~2로 낮춰도 됨

### [선택] TradingAgents-main/tradingagents/agents/utils/rating.py
  위치: C:\Users\hdw38\Desktop\TradingAgents-main\tradingagents\agents\utils\rating.py
  목적: telegram_sender.py _infer_advice_action() 유니코드 견고성 강화
  핵심: unicodedata.normalize("NFKC", text) 전처리 한 줄만 추가해도 충분

### [선택] vibe-investing/kiwoom_sdk/python/kiwoom_sdk/skill/__init__.py
  위치: C:\Users\hdw38\Desktop\vibe-investing\kiwoom_sdk\python\kiwoom_sdk\skill\__init__.py
  목적: telegram_commands.py 자연어 인텐트 매핑 강화
  핵심: INTENT_KEYWORDS 정규식 패턴 (account_query / stock_search / place_order)
  주의: place_order는 우리가 자동매매 안 하므로 "신호 조회" 인텐트로 의미 변경 필요

### [체크리스트용] ZeroQuant/scripts/ml/feature_engineering.py
  위치: C:\Users\hdw38\Desktop\zeroquant-main\scripts\ml\feature_engineering.py
  목적: signal_pipeline.py 피처 커버리지 점검 (22개 피처 목록)
  주의: 도입 아님, 비교 기준으로만 사용

---

## Session 45 결과

- risk/var_calculator.py: v2.0 -> v2.1, strict 8개 오류 전부 해결
  - dict -> Dict[str, Any] 6건 (131/133/192/516/526/566번 줄)
  - no-any-return 1건 (519번, to_dict() 반환 타입 확정으로 자동 해소)
  - unused-ignore 1건 (43번 줄) — ⚠️ 중요 정정 사항:
    43번 줄은 numpy가 아니라 scipy 재할당 줄(_scipy_norm = None)임을
    원본 코드 직접 대조로 확인. scipy 줄의 ignore만 제거하고,
    numpy 줄(36번)의 ignore는 원본 그대로 유지해야 정확한 수정임
    (numpy는 실제 설치된 타입 스텁 패키지라 재할당 시 진짜 오류 발생,
     scipy는 미설치 시 ignore_missing_imports=true로 이미 Any 추론되어
     재할당에 ignore 불필요 → "unused"로 지적됨)
- risk/portfolio_var.py: v2.0 -> v2.1, strict 12개 오류 전부 해결
  (Dict[str,List[float]]/Dict[str,float] 파라미터 타입 명시,
   _get_var_calculator -> Any 반환 타입, _fallback_individual_var 반환 타입 명시,
   Dict[str,Any] 반환 타입 6건)
- orchestrator/portfolio_manager.py: 전이 오류 20개 자동 해소 -> Success

## 다음 우선순위 (Session 46~)

### 1순위: report/telegram_commands.py (난이도 높음)
  처리 명령어:
    python pack_project.py report/telegram_commands.py
    mypy report/telegram_commands.py --strict 2>&1
  예상 오류 패턴: python-telegram-bot의 Optional[Message]/Optional[Chat]
    타입 관련 union-attr 오류 다수

### 2순위: app/bootstrap.py 전체 오류 재집계
  Remove-Item -Recurse -Force .mypy_cache -ErrorAction SilentlyContinue
  mypy app/ --strict 2>&1 | Select-String "error:" | Measure-Object |
    Select-Object -ExpandProperty Count
  (Session 43 기준 273개 -> 이번 세션 수정 반영 시 추가 감소 예상)

### 3순위: README.md/CONTEXT.md 정체성 정리
  "Phase 2 Paper Trading", "실계좌 연동" 항목 삭제
  -> "텔레그램 알림 보조 집사" 정의로 교체

## 세션 운영 규칙

- 파일은 한 번에 하나~두 개씩만 요청/처리
- 외부 저장소는 20줄 헤더 스크리닝만 (README 전문 붙여넣기 지양)
- 수정본은 항상 Set-Content 파워셸 스크립트로 제공
- mypy 로그는 Select-String "error:"로 필터링해서 첨부
- 오류 개수 분해는 반드시 error: 줄만 직접 세어 검증 (note: 줄 제외)
- ⚠️ Session 45 교훈: 줄 번호 매핑 주장은 반드시 원본 코드 직접 카운트로
  재검증할 것. AI가 제시한 줄 번호 표를 그대로 믿지 말고, 최소 2~3개
  지점을 샘플 검증해 방법론 신뢰도를 확인한 후 전체 적용

이 로드맵은 살아있는 문서입니다.
