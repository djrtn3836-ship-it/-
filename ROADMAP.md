\# 자율 AI 퀀트 시스템 - 장기 개선 로드맵



> 작성일: 2026-08-25

> 최종 갱신: 2026-09-04 (Session 33 완료)

> 기준 버전: V10 DDD 아키텍처

> 현재 진입점: python app/main.py (scanner\_main.py, 루트 main.py는 Deprecated)

> 테스트 상태: 1095/1095 passed (Session 33 기준 실측)

> 철학: 끊임없는 자기 비판과 자율 개선을 통한 초지능형 퀀트 시스템 구축



\---



\## 비전 (Ultimate Goal)



완전 자율 운용 AI 퀀트 시스템

\- 사람의 개입 없이 24/7 시장을 관찰하고 학습

\- 실패에서 즉각 학습하여 스스로 전략을 진화

\- 시스템 장애를 자가 진단, 치유 (Self-Healing)

\- 설명 가능한 AI (XAI) - 모든 결정에 근거 제시



\---



\## 현재 프로젝트 구조 (V10 DDD)



```text

project\_root/

├── app/

│   ├── main.py                         유일한 진입점

│   └── bootstrap.py (v2.5.0)           V10 부트스트래퍼

├── application/analysis/

│   ├── signal\_pipeline.py (v2.3)       앙상블 + SQI v2 + mypy strict 완료

│   ├── strategy\_bandit.py              Thompson Sampling MAB

│   ├── ab\_framework.py                 A/B Testing (Welch t-test)

│   ├── hyperparameter\_tuner.py         Optuna + 스케줄러 연동

│   └── tuning\_executor.py              주간 자동 튜닝 실행기

├── domain/

│   ├── models/signal.py                mypy strict 완료

│   └── strategies/{base,trend,reversal,breakout}.py (v2.0.1)  mypy strict 완료

├── risk/

│   ├── portfolio\_var.py                VaR + Kelly

│   └── safety\_guard.py (v5.2.0)        방향성 비교 재설계 + 이상치 필터

├── orchestrator/

│   ├── portfolio\_manager.py (v1.3)     OrderExecutor 콜백 연결

│   ├── sentiment\_pipeline.py (v1.0.3)  뉴스 감성 분석

│   └── event\_bus.py (v2.0)             EventStore, DLQ

├── infrastructure/

│   ├── database/postgres\_manager.py (v1.1.0)  TimescaleDB 하이퍼테이블

│   └── cache/

│       ├── redis\_cache.py              Redis 비동기 클라이언트

│       └── cached\_db\_manager.py        DatabaseManager 캐시 래퍼

├── data/

│   ├── db\_manager.py (v7.0.1)          SQLite CQRS + mypy strict 완료

│   └── news\_sentiment.py (v1.0)        한국어 키워드 앙상블 감성 분석

├── core/

│   ├── container.py (v1.4)             DATABASE\_URL + Redis 스위칭

│   ├── logger.py (v7.1.3)              mypy strict 완료

│   └── debug\_tower.py (v2.2.2)         mypy strict 완료

├── collector/collector\_status.py (v1.1)  mypy strict 완료

├── observability/

│   ├── tracer.py (v1.1)                mypy strict 완료

│   ├── trace\_id.py (v1.1)              mypy strict 완료

│   ├── trace\_config.py (v1.1)          mypy strict 완료

│   ├── trace\_propagation.py (v1.1)     mypy strict 완료

│   └── auto\_trace.py (v1.1)            mypy strict 완료

├── config/schema.py (v1.2.0)           mypy strict 완료 (pydantic.mypy 플러그인 적용)

├── scripts/migrate\_sqlite\_to\_postgres.py  마이그레이션 스크립트

├── docker-compose.yml                  TimescaleDB(PG16) + Redis 로컬 인프라

├── scanner\_main.py                     DEPRECATED

└── main.py (루트)                      DEPRECATED



