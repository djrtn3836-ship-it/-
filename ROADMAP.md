# 🗺️ 자율 AI 퀀트 시스템 - 장기 개선 로드맵

> **작성일**: 2026-08-25 | **최종 갱신**: 2026-08-26 Session 6  
> **기준 버전**: V10 DDD 아키텍처  
> **철학**: 끊임없는 자기 비판과 자율 개선을 통한 초지능형 퀀트 시스템 구축

---

## 🎯 비전 (Ultimate Goal)

**"완전 자율 운용 AI 퀀트 시스템"**
- 사람의 개입 없이 24/7 시장을 관찰하고 학습
- 실패에서 즉각 학습하여 스스로 전략을 진화
- 시스템 장애를 자가 진단·치유
- 설명 가능한 AI (XAI) - 모든 결정에 근거 제시

---

## 📅 Phase 1: 기반 안정화 ✅ 완료

### 1-1. 코드 품질 기반 완성
- [x] pyflakes 0개 경고
- [x] 16/16 단위 테스트 통과 → **228/228 통과**
- [x] **테스트 커버리지 확대**: V10 신규 모듈 테스트 (domain, application, observability)
- [ ] **타입 힌트 100%**: mypy strict 모드 통과 목표
- [ ] **독스트링 표준화**: Google Style DocString 전면 적용

### 1-2. 핵심 버그 및 스텁 완성
- [x] Bug1: asyncio.sleep() 제거
- [x] Bug2: portfolio_manager.start() 추가
- [x] Bug3: 배치 커밋 완성
- [x] **`_get_daily_returns()` 실제 구현** (outcome 기반 수익률 계산)
- [x] **trailing_stops 종료 전 DB 저장** (프로세스 재시작 시 복구 가능)
- [x] **`app/bootstrap.py` 전면 재작성** (V10 진입점 통합, 1100+ lines)
- [x] **`app/main.py` 유일한 진입점** (scanner_main.py Deprecated)

### 1-3. 의존성 & 설정 정리
- [x] `requirements.txt` pydantic, cachetools 추가
- [x] `config/schema.py` Pydantic 설정 전면 마이그레이션
- [x] Windows 인코딩 강제 설정 (UTF-8)

---

## 📅 Phase 2: 지능화 강화 ✅ 완료

### 2-1. Signal Pipeline V10 완성
- [x] `application/analysis/signal_pipeline.py` 전략 앙상블 완성 (v2.0)
  - [x] 신뢰도 기반 동적 가중치: strategy.weight × confidence
  - [x] 다수결 판정: consensus 기반 최종 Action
  - [x] Signal Quality Index (SQI): confidence × consensus
  - [x] SQI < 0.45 시 HOLD 강제
  - [x] Bollinger Band, MACD 기술지표 추가
- [x] ATR 기반 동적 손절/익절 자동 조정 (AtrService)
- [x] 신호 품질 지수 고도화 (SQI → SQI v2) ← Session 7 완료

### 2-2. 도메인 전략 강화 (v2.0)
- [x] TrendStrategy: MACD 크로스오버 + Regime 보정
- [x] ReversalStrategy: Stochastic %K/%D + Bollinger %B + 다중신호 합의 보너스
- [x] BreakoutStrategy: 52주 신고가 근접(5%) + 볼린저 스퀴즈 + 거래량 차등

### 2-3. 강화학습 기반 전략 진화
- [x] **Multi-Armed Bandit**: Thompson Sampling 전략 선택기 구현
  - 순수 Python (numpy 불필요), decay=0.99, bulk_update / select_top_k API
- [x] **Thompson Sampling 실시간 피드백 연동** (BanditFeedbackBridge v1.0)
  - 보상 배분: 주도 전략 return_1d 전액 / 보조(점수≥0.5) 비율×0.3
  - 보상 클리핑 [-10%,+10%], Throttle 60초
  - PerformanceTracker v3.0 attach/detach_bandit_bridge 훅
- [ ] **Bayesian Optimization**: 전략 파라미터 자동 튜닝

### 2-4. 리스크 관리 고도화
- [x] **CVaR (Conditional VaR)** — `risk/var_calculator.py` v2.0
  - Gaussian CVaR: `ES = -μ + σ·φ(z_α)/α` (부호 버그 수정 포함)
  - Cornish-Fisher 수정 CVaR (팻테일 보정)
  - Historical CVaR (비모수)
- [x] **Kelly Criterion** 포지션 사이징
  - `f* = (b·p - q)/b`, Fractional Kelly×0.5, max 30%
  - VaR-Kelly 결합 position_limit
- [x] **Portfolio VaR v2.0 Kelly 통합** (`risk/portfolio_var.py`)
  - `position_limit = min(risk_adj_factor, kelly_position_limit)`
  - Monte Carlo 10,000회 + Cholesky 분해 상관관계 반영
- [x] **Correlation Matrix** 실시간 갱신 → 포트폴리오 분산 최적화 ← Session 9 완료
- [x] Circuit Breaker 강화: 연속 손실 / 변동성 급등 / 유동성 위기 대응 ← Session 9 완료

### 2-5. 관측성 (Observability)
- [x] **`@trace.traced` 핵심 메서드 전면 적용** (Session 5+6 완료)
  - `PerformanceTracker`: start/stop/_update_metrics/_get_daily_returns
  - `VaRCalculator`: calculate_metrics/calculate/calculate_kelly
  - `SafetyGuard`: check/get_threshold_basis/get_trigger_log/_is_triggered
  - `PortfolioVaR`: calculate/_fallback_individual_var
  - `SignalPipeline`: TracedService 상속 (전 public 메서드 자동)
  - `BanditFeedbackBridge`: on_performance_updated/force_feedback/_compute_strategy_rewards/get_status ← Session 6 추가
  - `CalibrationTracker`: record/get_calibration/record_ab_result ← Session 6 추가
  - `DailyMonitor`: run/_generate_report ← Session 6 추가

### 2-6. 데이터 파이프라인
- [ ] **Feature Store 완성** (`orchestrator/feature_store.py`)
- [ ] OHLCV → 기술적 지표 자동 계산 파이프라인
- [ ] 뉴스 감성 분석 실시간 반영 (VADER + KoNLP 앙상블)

---

## 📅 Phase 3: 관측성 및 자가 치유 🔄 진행 중

### 3-1. 분산 트레이싱 완성 (observability/)
- [x] **`@trace.traced` 데코레이터** — ModuleTracer, ENTER/EXIT/EXCEPTION 로깅
- [x] **TraceConfigManager** — Hot-reload, 모듈별 ON/OFF, trace_config.json
- [x] **TracedService** 기반 클래스 — `__init_subclass__` 자동 계측
- [x] **Trace ID 전파**: HTTP→DB→Telegram 전체 체인 (trace_propagation.py) ← Session 7 완료
- [ ] 성능 병목 자동 감지 → 슬로우 쿼리 알림
- [ ] 의사결정 경로 시각화 (Trace Tree)

### 3-2. 자가 진단 시스템
- [x] **Health Score 대시보드**: 컴포넌트별 0~100점 + /health 통합 ← Session 7 완료
- [x] **Anomaly Detection**: 비정상 패턴 자동 탐지 (Isolation Forest) ← Session 9 완료
- [ ] **Root Cause Analysis**: 장애 원인 자동 추론
- [x] Shadow Mode: 신규 전략을 실거래 없이 실시간 평가

### 3-3. 자기 최적화 루프
- [x] **A/B Testing Framework v1.0** (`application/analysis/ab_framework.py`)
  - 순수 Python Welch t-test (scipy 불필요)
  - Bonferroni 보정 + Cohen's d 효과 크기
  - ABTestManager 싱글톤 — create_test/assign_variant/record_result/get_winner
  - bootstrap.py startup 통합 (strategy_selection / entry_timing 기본 실험)
- [x] **CalibrationTracker ↔ ABTest 연동** — regime별 캘리브레이션 A/B 비교 ✅
  - `record_ab_result()`: ECE → ab_metric (1.0 - ECE) 변환 → ABTest record_result 피드백
  - `calibration_quality` A/B 실험: bootstrap.start_ab_framework() 자동 등록
  - regime별 독립 피드백 (trend / reversal / sideways 변형)
- [x] **PortfolioVaR.position_limit → OrderExecutor 연결** — 동적 주문 크기 ✅
  - `OrderExecutor.update_position_limit()`: VaR+Kelly 통합 한도 실시간 적용
  - `_position_size_check()` 개선: 절대 한도 1000주 × position_limit 비율
  - 고위험(VaR≥5%) → 500주 이하, 저위험(<1.5%) → 1000주 허용
- [ ] **Automated Hyperparameter Tuning**: Optuna 기반
- [ ] **Model Drift Detection**: 모델 성능 저하 자동 감지 → 재학습 트리거
- [ ] **Explainability Module**: SHAP 값으로 각 결정 요인 설명

---

## 📅 Phase 4: 인프라 현대화 (2개월 ~ 3개월)

### 4-1. 아키텍처 완성
- [ ] **Event-Driven Architecture**: 전면 이벤트 버스로 결합도 제거
- [ ] **CQRS 패턴**: 읽기/쓰기 DB 분리
- [ ] **Domain Event Sourcing**: 모든 상태 변화를 이벤트로 기록

### 4-2. 데이터베이스 현대화
- [ ] **SQLite → PostgreSQL 마이그레이션**
- [ ] **TimescaleDB** 시계열 데이터 최적화
- [ ] **Redis** 실시간 캐시 레이어 도입
- [ ] 자동 백업 및 Point-in-Time Recovery

### 4-3. 프로세스 분리 (Microservice 전환)
- [ ] Scanner / Decision Engine / Risk Monitor 프로세스 분리
- [ ] gRPC 또는 NATS 기반 프로세스 간 통신

---

## 📅 Phase 5: 초지능화 (3개월 ~ 분기)

### 5-1. LLM 통합
- [ ] GPT/Claude API 연동, DART 공시 자동 파싱, 뉴스 요약 + 임팩트 스코어
- [ ] 멀티모달: 차트 이미지 → Vision LLM 패턴 인식

### 5-2. 메타러닝 (학습하는 학습)
- [ ] MAML, Few-Shot Learning, Continual Learning

### 5-3. 시장 시뮬레이션
- [ ] Agent-Based Market Simulation, Stress Testing, Counterfactual Analysis

---

## 🔄 자율 개선 루프

```
매 개발 사이클:
1. 코드 분석 → pyflakes / mypy / 복잡도 측정
2. 성능 측정 → 승률 / 샤프지수 / 최대낙폭 추적
3. 병목 감지 → 느린 쿼리 / 메모리 누수 / CPU 과부하
4. 자동 수정 → 경고 제거 / 최적화 / 리팩토링
5. 테스트 검증 → 단위/통합/회귀 테스트
6. 커밋 & 배포
7. goto 1
```

---

## 📊 품질 지표 추적

| 지표 | 현재 | Phase1 목표 | Phase2 목표 | Phase3 목표 |
|------|------|-------------|-------------|-------------|
| pyflakes 경고 | **0** ✅ | 0 | 0 | 0 |
| 단위 테스트 통과율 | **655/655** ✅ | 130/130 | 200/200 | 300/300 |
| 타입 힌트 커버리지 | ~70% | 80% | 95% | 100% |
| 함수 평균 복잡도 | ~4.2 | <6 | <5 | <4 |
| 코드 중복도 | ~8% | <10% | <5% | <3% |
| 테스트 커버리지 | ~65% | 60% ✅ | 80% | 90% |
| 백테스트 승률 | ? | 55% | 60% | 65%+ |
| 평균 샤프지수 | ? | 1.0+ | 1.5+ | 2.0+ |

---

## 🚀 현재 다음 작업 (Phase 3 잔여 + Phase 4 준비)

### Session 6 완료 항목 (2026-08-26)
- ✅ `@trace.traced` BanditFeedbackBridge (on_performance_updated / force_feedback / _compute_strategy_rewards / get_status)
- ✅ CalibrationTracker v5.2.0: record_ab_result() ABTest ECE 피드백 연동
- ✅ OrderExecutor v2.0: update_position_limit() position_limit 동적 반영
- ✅ bootstrap.start_ab_framework() `calibration_quality` 실험 추가 등록
- ✅ E2E 통합 테스트 (test_e2e_bootstrap_dry_run.py 19개)
- ✅ CalibrationTracker v5.2.0 단위 테스트 (21개)
- ✅ OrderExecutor position_limit 단위 테스트 (20개)
- ✅ 전체 655/655 테스트 통과 (+60개)

### 다음 우선순위
1. **SQI v2** — 신호 품질 지수 고도화 (모멘텀·거래량·변동성 복합 스코어)
2. **Trace ID 전파** — HTTP 요청 → DB → 알림 전체 추적
3. **Health Score 대시보드** — 컴포넌트별 건강도 0~100점
4. **Shadow Mode** — 신규 전략 실거래 없이 실시간 평가
5. **Automated Hyperparameter Tuning** — Optuna 기반 전략 파라미터 자동 튜닝

## ✅ 완료된 모든 항목 (Sessions 1-5)

- [x] `app/main.py` 유일한 진입점 확립, scanner_main.py Deprecated
- [x] Signal Pipeline V10 앙상블 (SQI, 신뢰도 가중치)
- [x] Multi-Armed Bandit (Thompson Sampling, decay, bulk_update)
- [x] 도메인 전략 v2.0 (MACD, Stochastic, Bollinger Squeeze)
- [x] CVaR v2.0 (Gaussian/Cornish-Fisher/Historical, 부호 버그 수정)
- [x] Kelly Criterion (Fractional Kelly, VaR 결합 position_limit)
- [x] BanditFeedbackBridge — PerformanceTracker → StrategyBandit 실시간 피드백
- [x] PerformanceTracker v3.0 (attach/detach_bandit_bridge, Bandit 훅)
- [x] DBManager.get_strategy_outcomes() (decisions + outcomes JOIN)
- [x] observability @trace.traced 핵심 메서드 전면 적용 (5개 파일)
- [x] Portfolio VaR v2.0 (Kelly 통합, position_limit = min(var_adj, kelly))
- [x] A/B Testing Framework v1.0 (Welch t-test, Bonferroni, ABTestManager)
- [x] 단위 테스트 228개 전량 통과 (pyflakes 0)

---

*이 로드맵은 살아있는 문서입니다. 코드베이스 분석 결과에 따라 지속 갱신됩니다.*


---

## 🎯 비전 (Ultimate Goal)

**"완전 자율 운용 AI 퀀트 시스템"**
- 사람의 개입 없이 24/7 시장을 관찰하고 학습
- 실패에서 즉각 학습하여 스스로 전략을 진화
- 시스템 장애를 자가 진단·치유
- 설명 가능한 AI (XAI) - 모든 결정에 근거 제시

---

## 📅 Phase 1: 기반 안정화 (현재 ~ 2주)

### 1-1. 코드 품질 기반 완성
- [x] pyflakes 0개 경고
- [x] 16/16 단위 테스트 통과 → **95/95 통과**
- [x] **테스트 커버리지 확대**: V10 신규 모듈 테스트 (domain, application, observability)
- [ ] **타입 힌트 100%**: mypy strict 모드 통과 목표
- [ ] **독스트링 표준화**: Google Style DocString 전면 적용

### 1-2. 핵심 버그 및 스텁 완성
- [x] Bug1: asyncio.sleep() 제거
- [x] Bug2: portfolio_manager.start() 추가
- [x] Bug3: 배치 커밋 완성
- [x] **`_get_daily_returns()` 실제 구현** (outcome 기반 수익률 계산)
- [x] **trailing_stops 종료 전 DB 저장** (프로세스 재시작 시 복구 가능)
- [x] **`app/bootstrap.py` 전면 재작성** (V10 진입점 통합)
- [x] **`app/main.py` 유일한 진입점** (scanner_main.py Deprecated)

### 1-3. 의존성 & 설정 정리
- [x] `requirements.txt` pydantic, cachetools 추가
- [x] `config/schema.py` Pydantic 설정 전면 마이그레이션
- [x] Windows 인코딩 강제 설정 (UTF-8)

---

## 📅 Phase 2: 지능화 강화 (2주 ~ 1개월)

### 2-1. Signal Pipeline V10 완성
- [x] `application/analysis/signal_pipeline.py` 전략 앙상블 완성 (v2.0)
  - [x] 신뢰도 기반 동적 가중치: strategy.weight × confidence
  - [x] 다수결 판정: consensus 기반 최종 Action
  - [x] Signal Quality Index (SQI): confidence × consensus
  - [x] SQI < 0.45 시 HOLD 강제
  - [x] Bollinger Band, MACD 기술지표 추가
- [x] ATR 기반 동적 손절/익절 자동 조정 (AtrService)
- [x] 신호 품질 지수 고도화 (SQI → SQI v2) ← Session 7 완료

### 2-2. 도메인 전략 강화 (v2.0)
- [x] TrendStrategy: MACD 크로스오버 + Regime 보정
- [x] ReversalStrategy: Stochastic %K/%D + Bollinger %B + 다중신호 합의 보너스
- [x] BreakoutStrategy: 52주 신고가 근접(5%) + 볼린저 스퀴즈 + 거래량 차등

### 2-3. 강화학습 기반 전략 진화
- [x] **Multi-Armed Bandit**: Thompson Sampling 전략 선택기 구현
  - 순수 Python (numpy 불필요)
  - decay=0.99 망각 인자
  - bulk_update, select_top_k API
- [ ] **Bayesian Optimization**: 전략 파라미터 자동 튜닝
- [ ] Thompson Sampling 실시간 성과 피드백 연동

### 2-3. 리스크 관리 고도화
- [ ] **CVaR (Conditional VaR)** 구현 → 극단 손실 대비
- [x] **Correlation Matrix** 실시간 갱신 → 포트폴리오 분산 최적화 ← Session 9 완료
- [ ] **Kelly Criterion** 포지션 사이징 → 기대값 기반 최적 투자 비율
- [x] Circuit Breaker 강화: 연속 손실 / 변동성 급등 / 유동성 위기 대응 ← Session 9 완료

### 2-4. 데이터 파이프라인 강화
- [ ] **Feature Store 완성** (`orchestrator/feature_store.py`)
  - TTL 기반 피처 캐싱
  - 피처 중요도 추적
- [ ] OHLCV → 기술적 지표 자동 계산 파이프라인
- [ ] 뉴스 감성 분석 실시간 반영 (VADER + KoNLP 앙상블)

---

## 📅 Phase 3: 관측성 및 자가 치유 (1개월 ~ 2개월)

### 3-1. 분산 트레이싱 완성 (observability/)
- [ ] **Span 자동 생성**: `@auto_trace` 데코레이터 전면 적용
- [x] **Trace ID 전파**: HTTP→DB→Telegram 전체 체인 (trace_propagation.py) ← Session 7 완료
- [ ] 성능 병목 자동 감지 → 슬로우 쿼리 알림
- [ ] 의사결정 경로 시각화 (Trace Tree)

### 3-2. 자가 진단 시스템
- [x] **Health Score 대시보드**: 컴포넌트별 0~100점 + /health 통합 ← Session 7 완료
- [x] **Anomaly Detection**: 비정상 패턴 자동 탐지 (Isolation Forest) ← Session 9 완료
- [ ] **Root Cause Analysis**: 장애 원인 자동 추론
- [x] Shadow Mode: 신규 전략을 실거래 없이 실시간 평가

### 3-3. 자기 최적화 루프
- [ ] **A/B Testing Framework**: 전략 버전 비교 실험
- [ ] **Automated Hyperparameter Tuning**: Optuna 기반
- [ ] **Model Drift Detection**: 모델 성능 저하 자동 감지 → 재학습 트리거
- [ ] **Explainability Module**: SHAP 값으로 각 결정 요인 설명

---

## 📅 Phase 4: 인프라 현대화 (2개월 ~ 3개월)

### 4-1. 아키텍처 완성
- [ ] **Event-Driven Architecture**: 전면 이벤트 버스로 결합도 제거
  - `orchestrator/event_bus.py` 고도화
  - Command / Event / Query 명확한 분리
- [ ] **CQRS 패턴**: 읽기/쓰기 DB 분리
- [ ] **Domain Event Sourcing**: 모든 상태 변화를 이벤트로 기록

### 4-2. 데이터베이스 현대화
- [ ] **SQLite → PostgreSQL 마이그레이션** (`data/postgres_db.py` 완성)
  - 연결 풀링 (asyncpg)
  - Read Replica 지원
- [ ] **TimescaleDB** 시계열 데이터 최적화
- [ ] **Redis** 실시간 캐시 레이어 도입
- [ ] 자동 백업 및 Point-in-Time Recovery

### 4-3. 프로세스 분리 (Microservice 전환)
- [ ] **Scanner 프로세스** 분리 (독립 실행)
- [ ] **Decision Engine 프로세스** 분리
- [ ] **Risk Monitor 프로세스** 분리
- [ ] gRPC 또는 NATS 기반 프로세스 간 통신

---

## 📅 Phase 5: 초지능화 (3개월 ~ 분기)

### 5-1. LLM 통합
- [ ] **GPT/Claude API 연동**: 시장 상황 자연어 분석
- [ ] **DART 공시 자동 파싱**: LLM으로 의미 추출
- [ ] **뉴스 요약 + 임팩트 스코어**: 실시간 LLM 분석
- [ ] 멀티모달: 차트 이미지 → Vision LLM 패턴 인식

### 5-2. 메타러닝 (학습하는 학습)
- [ ] **MAML (Model-Agnostic Meta-Learning)**: 새로운 종목에 빠른 적응
- [ ] **Few-Shot Learning**: 소수 데이터로 신규 패턴 학습
- [ ] **Continual Learning**: 과거를 잊지 않고 새 지식 습득

### 5-3. 시장 시뮬레이션
- [ ] **Agent-Based Market Simulation**: 시장 참가자 시뮬레이션
- [ ] **Stress Testing**: 블랙 스완 시나리오 자동 생성
- [ ] **Counterfactual Analysis**: "만약에" 시나리오 학습

---

## 🔄 자율 개선 루프 (Autonomous Improvement Loop)

```
매 개발 사이클:
1. 코드 분석 → pyflakes / mypy / 복잡도 측정
2. 성능 측정 → 승률 / 샤프지수 / 최대낙폭 추적
3. 병목 감지 → 느린 쿼리 / 메모리 누수 / CPU 과부하
4. 자동 수정 → 경고 제거 / 최적화 / 리팩토링
5. 테스트 검증 → 단위/통합/회귀 테스트
6. 커밋 & 배포
7. goto 1
```

---

## 📊 품질 지표 추적

| 지표 | 현재 | Phase1 목표 | Phase2 목표 | Phase3 목표 |
|------|------|-------------|-------------|-------------|
| pyflakes 경고 | 0 | 0 | 0 | 0 |
| 단위 테스트 통과율 | **95/95** | 130/130 | 200/200 | 300/300 |
| 타입 힌트 커버리지 | ~60% | 80% | 95% | 100% |
| 함수 평균 복잡도 | ~7 | <6 | <5 | <4 |
| 코드 중복도 | ~12% | <10% | <5% | <3% |
| 테스트 커버리지 | ~35% | 60% | 80% | 90% |
| 백테스트 승률 | ? | 55% | 60% | 65%+ |
| 평균 샤프지수 | ? | 1.0+ | 1.5+ | 2.0+ |

---

## 🚨 즉시 실행 (다음 사이클)

1. **CVaR + Kelly Criterion** 리스크 관리 고도화 (`risk/` 계층)
2. **Multi-Armed Bandit 실시간 피드백 연동** (StrategyBandit ↔ PerformanceTracker)
3. **Observability @auto_trace** 핵심 모듈 전면 적용
4. **A/B Testing Framework** 전략 버전 비교 실험
5. 단위 테스트 95 → 130개 (CVaR, Kelly, Bandit 테스트 추가)

## ✅ 완료된 즉시 실행 항목

1. [x] `_get_daily_returns()` 실제 구현 (outcome 기반)
2. [x] `trailing_stops` DB 저장 로직 추가
3. [x] V10 신규 모듈 테스트 추가 (domain, observability, signal_pipeline)
4. [x] `requirements.txt` 업데이트
5. [x] Windows 인코딩 강제 설정
6. [x] **`app/main.py` 유일한 진입점 확립** (scanner_main.py Deprecated)
7. [x] **Signal Pipeline V10 앙상블 완성** (SQI, 신뢰도 가중치)
8. [x] **Multi-Armed Bandit 전략 선택기** (Thompson Sampling)
9. [x] **도메인 전략 v2.0** (MACD, Stochastic, Bollinger Squeeze)

---

*이 로드맵은 살아있는 문서입니다. 코드베이스 분석 결과에 따라 지속 갱신됩니다.*
