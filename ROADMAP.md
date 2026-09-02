\# 🗺️ 자율 AI 퀀트 시스템 - 장기 개선 로드맵



> \*\*작성일\*\*: 2026-08-25

> \*\*최종 갱신\*\*: 2026-09-02 (Session 23 완료, 문서 전면 정리)

> \*\*기준 버전\*\*: V10 DDD 아키텍처

> \*\*현재 진입점\*\*: `python app/main.py` (scanner\_main.py, 루트 main.py는 Deprecated)

> \*\*테스트 상태\*\*: 마지막 실측(pytest 실행) 검증 958/958 (Session 15 기준).

>   이후 Session 16(+68 뉴스감성), 19(+10 DB스위칭), 20\~21(+5 mypy strict) 등

>   추가 테스트가 작성되었으나 전체 스위트 재실행 검증은 아직 수행되지 않음.

>   → \*\*다음 세션 시작 시 `pytest tests/unit/ -v` 전체 실행으로 정확한 수치 갱신 필요\*\*

>   (Session 14 이후 확립된 원칙: 실제 실행 결과만 정직하게 기록)

> \*\*철학\*\*: 끊임없는 자기 비판과 자율 개선을 통한 초지능형 퀀트 시스템 구축



\---



\## 🎯 비전 (Ultimate Goal)



\*\*"완전 자율 운용 AI 퀀트 시스템"\*\*

\- 사람의 개입 없이 24/7 시장을 관찰하고 학습

\- 실패에서 즉각 학습하여 스스로 전략을 진화

\- 시스템 장애를 자가 진단·치유 (Self-Healing)

\- 설명 가능한 AI (XAI) — 모든 결정에 근거 제시



\---



\## 📁 현재 프로젝트 구조 (V10 DDD)



```text

project\_root/

├── app/

│   ├── main.py                        ✅ 유일한 진입점

│   └── bootstrap.py (v2.5.0)          ✅ V10 부트스트래퍼

├── application/analysis/

│   ├── signal\_pipeline.py (v2.2)      ✅ 앙상블 + SQI v2 + 하이퍼파라미터 동적화

│   ├── strategy\_bandit.py             ✅ Thompson Sampling MAB

│   ├── ab\_framework.py                ✅ A/B Testing (Welch t-test)

│   ├── hyperparameter\_tuner.py        ✅ Optuna + 스케줄러 연동

│   └── tuning\_executor.py             ✅ 주간 자동 튜닝 실행기

├── domain/

│   ├── models/signal.py               ✅ mypy strict 통과

│   └── strategies/{base,trend,reversal,breakout}.py (v2.0.1) ✅ mypy strict 통과

├── risk/

│   ├── portfolio\_var.py               ✅ VaR + Kelly

│   └── safety\_guard.py (v5.2.0)       ✅ 방향성 비교 재설계 + 이상치 필터

├── orchestrator/

│   ├── portfolio\_manager.py (v1.3)    ✅ OrderExecutor 콜백 연결

│   ├── sentiment\_pipeline.py (v1.0.3) ✅ 뉴스 감성 분석

│   └── event\_bus.py (v2.0)            ✅ EventStore, DLQ

├── infrastructure/database/

│   └── postgres\_manager.py (v1.1.0)   ✅ TimescaleDB 하이퍼테이블 (Session 23)

├── data/

│   ├── db\_manager.py (v6.2.0)         ✅ SQLite (현재 기본 DB)

│   └── news\_sentiment.py (v1.0)       ✅ 한국어 키워드 앙상블 감성 분석

├── core/container.py (v1.3)           ✅ DATABASE\_URL 기반 DB 스위칭 + 폴백

├── scripts/migrate\_sqlite\_to\_postgres.py ✅ 마이그레이션 스크립트

├── docker-compose.yml                 ✅ TimescaleDB(PG16) 로컬 인프라

├── scanner\_main.py                    ⚠️ DEPRECATED

└── main.py (루트)                     ⚠️ DEPRECATED

Copy

📅 Phase 1: 기반 안정화 ✅ 완료

&#x20;pyflakes 0개 경고, app/main.py 유일한 진입점 확립

&#x20;Pydantic 설정 전면 마이그레이션, Windows UTF-8 강제 설정

&#x20;타입 힌트 100% (mypy strict) — domain 계층 5개 파일 완료, application/risk 계층 진행 중

&#x20;Google Style Docstring 전면 적용

📅 Phase 2: 지능화 강화 ✅ 완료

&#x20;Signal Pipeline V10 앙상블, 도메인 전략 v2.0 (Trend/Reversal/Breakout)

&#x20;Multi-Armed Bandit + BanditFeedbackBridge 실시간 피드백

&#x20;CVaR v2.0, Kelly Criterion, Portfolio VaR + OrderExecutor 콜백 연결

(ROADMAP에는 과거 "완료"로 기재되어 있었으나 Session 22 검증 시 실제 연결 코드 부재 확인 → 수정)

&#x20;뉴스 감성 분석 실시간 반영 (한국어 키워드 앙상블, NAVER 키 미설정 시 중립 폴백)

📅 Phase 3: 관측성 및 자가 치유 ✅ 완료

&#x20;@trace.traced 전면 적용, Trace ID 전파, Health Score 대시보드

&#x20;Anomaly Detection, Root Cause Analysis, Explainability(Shapley 근사), Trace Tree

&#x20;A/B Testing Framework + CalibrationTracker 연동

&#x20;Automated Hyperparameter Tuning (Optuna, 매주 일 03:00)

&#x20;Model Drift Detection (PSI/KS)

&#x20;성능 병목 자동 감지 → 슬로우 쿼리 알림

📅 Phase 4: 인프라 현대화 🔄 진행 중

&#x20;Event-Driven Architecture (EventBus v2.0, DLQ)

&#x20;Docker PostgreSQL 로컬 인프라 + PostgresManager 초석

&#x20;SQLite → PostgreSQL 마이그레이션 스크립트

&#x20;container.py DATABASE\_URL 기반 투명 스위칭 + 실패 시 SQLite 자동 폴백

&#x20;decisions/ohlcv PK 재설계 + TimescaleDB 하이퍼테이블 전환 (Session 23)

&#x20;Redis 실시간 캐시 레이어 도입

&#x20;CQRS 패턴 (읽기/쓰기 DB 분리)

&#x20;프로세스 분리 (Scanner/Decision Engine/Risk Monitor 마이크로서비스화)

📅 Phase 5: 초지능화 (대기)

&#x20;LLM 통합 (GPT/Claude, DART 공시 파싱, 뉴스 요약)

&#x20;메타러닝 (MAML, Continual Learning)

&#x20;시장 시뮬레이션 (Agent-Based, Stress Testing)

📋 세션별 완료 이력

Sessions 1\~9: Signal Pipeline V10, Multi-Armed Bandit, 도메인 전략 v2.0, CVaR/Kelly, BanditFeedbackBridge, A/B Testing Framework, Observability 전면 적용, Anomaly Detection.



Session 10: Model Drift Detection(PSI/KS) + Root Cause Analysis(규칙 엔진)

Session 11: Explainability(Shapley 근사) + Decision Trace Tree

Session 12: Feature Store v2.0 + OHLCV 파이프라인

Session 13: Backtester v9.0 + Walk-Forward (Rolling/Anchored)

Session 14: EventBus v2.0 (EventStore, DLQ, 백그라운드 재시도)

Session 15: HyperparameterTuner 스케줄러 실연동, \_strategy\_weights 분리 관리 (Strategy.weight 읽기전용 문제 해결). 958/958 실측 확인

Session 16: 뉴스 감성 분석(한국어 키워드 앙상블) + SignalPipeline 통합 (+68 테스트)

Session 17: 운영 버그 다수 수정 — set\_telegram\_sender/set\_realtime\_price\_provider AttributeError, SafetyGuard 방향 무시 버그(v5.2.0), macro\_collector 원본값 유출(v2.3)

Session 18: NewsCrawler 실제 스키마 확정(title/summary/link/pub\_date/source)

Session 19: Docker PostgreSQL 인프라 + container.py DB 스위칭(+10 테스트)

Session 20: Unclosed session(DartConnector) 수정 + domain mypy strict 2개 파일

Session 21: domain/strategies 전략 3종 mypy strict 적용, pyproject.toml 정리

Session 22: SQLite→PostgreSQL 마이그레이션 스크립트, PortfolioVaR→OrderExecutor 콜백 연결 완성(문서상 "완료" 표기와 실제 구현 간 단절을 발견·수정)

Session 23: decisions/ohlcv PK 재설계 + TimescaleDB 하이퍼테이블 전환 (본 세션)

🚀 다음 우선순위 (Session 24\~)

application/analysis/signal\_pipeline.py mypy strict — 하위 필터 6개 파일 확인 필요

전체 pytest 재실행 — 현재 표기된 "958" 이후 누적분 정확한 검증

Redis 실시간 캐시 레이어 (Feature Store 연동)

CQRS 패턴 초석

이 로드맵은 살아있는 문서입니다. 코드베이스 분석 결과에 따라 지속 갱신됩니다.

