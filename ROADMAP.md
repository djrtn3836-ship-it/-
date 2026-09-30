# ROADMAP — stock_analyzer (V10)

> 한국 주식(KOSPI/KOSDAQ) 실시간 감시 + Telegram 알림 보조 시스템. 자동매매 아님.
> 최종 갱신: 2026-09-30 | 브랜치: `genspark_ai_developer` | 공식 진입점: `python app/main.py`

## 운영 원칙
- 승인 기반 개발: [분석·계획 보고 → 승인 → 수행]
- 기존 기능 보존: 삭제가 필요하면 영향도를 명시하고 승인 후 `_archive/`로 이동
- 모든 소스 파일은 **UTF-8 (BOM 없음)** 유지
- 각 작업 완료 시 `DEVELOPMENT_LOG.md` 최신화

---

## 단계 요약

| 단계 | 목표 | 상태 |
| :--- | :--- | :--- |
| P0 | 현재 상태 고정 (문서/인코딩/파일 정리) | 🔄 진행 중 |
| P1 | 핵심 경로 신뢰성 (알림 실제 도착 보장) | ⏳ 대기 |
| P2 | 관측성/디버깅 통합 (나비효과 추적) | ⏳ 대기 |
| P3 | 아키텍처 정리 (분해/정본화/계층 규칙) | ⏳ 대기 |
| P4 | 검증 인프라 (통합 테스트 + CI) | ⏳ 대기 |
| P5 | Paper Trading 안전 진입 | ⏳ 대기 |
| P6 | 고도화 (유니버스/거시/DB/대시보드) | ⏳ 대기 |

---

## P0. 현재 상태 고정
- [x] V10 기준 문서 재작성 (CONTEXT.md)
- [x] ROADMAP.md / DEVELOPMENT_LOG.md 신설
- [x] BOM(U+FEFF) 56개 파일 제거
- [x] A등급 스테일 산출물 정리
- [x] B등급 중복/고아 모듈 정본 확정 (의존성 0건 아카이브)
- [x] `python app/main.py` 부팅 검증 (실 자격증명 + TEST_MODE 양쪽 성공)
- 완료 기준: `git status` clean, 구문 오류 0, BOM 0, 문서-코드 일치
- 잔여: 테스트 결합 고아 모듈(`event_bus`/`feature_store`/`pipeline_manager`/관측성 5종) 배선 여부 결정

## P1. 핵심 경로 신뢰성
- [x] 신호 → Telegram 알림 E2E 실측 (부팅 알림 수신 확인)
- [x] `validation/backtester.py` 실구현 + Walk-Forward (배선: `backtest_runner` + `strategy_backtest`)
- [x] `analytics/performance_tracker.py` 실측값 검증 (임시 DB 시딩 테스트)
- [x] OHLCV 적재 경로 확보 (`scheduler/ohlcv_backfill.py`, 190종목 92,104행)
- [ ] ML 피처 학습/예측 정합성 재검증 (`feedback/feedback_learner.py`)
- [ ] 알림 누락 검증기(`analytics/alert_verifier.py`) 결과 신뢰성 확인
- 완료 기준: 하루치 신호가 알림 건수와 일치, 백테스트가 실제 DB 데이터로 산출

## P2. 관측성/디버깅 통합
- [ ] `observability/tracer` + `scripts/trace_ctl.py` ON/OFF 완성
- [ ] 파일별 독립 로그(콘솔 분리) 표준화
- [ ] 신규 파일 자동 계측(`TracedService`/`auto_trace_module`) 표준 확립
- [ ] 미배선 관측 모듈(explainer_v2/root_cause_analyzer/anomaly_detector/model_drift_detector) 배선 또는 보관 결정
- 완료 기준: 특정 오류 발생 시 어느 파일·줄에서 시작됐는지 로그로 추적 가능

## P3. 아키텍처 정리
- [ ] `scanner/deep_analyzer.py` 책임 분해 (신호/ATR/트레일링/합의/스코어링)
- [ ] 고아 모듈 정본화 및 계층 재배치
- [ ] 설정 단일화 (`config/schema.py` 기준)
- [ ] 계층 의존 규칙 자동 검증(import-linter) 도입
- 완료 기준: 500줄 초과 God 파일 0, 순환 참조 0

## P4. 검증 인프라
- [ ] `tests/run_all.py` 단일 실행기 (unit/integration/e2e)
- [ ] CI: mypy --strict + ruff + pytest
- 완료 기준: `python tests/run_all.py` 단일 명령으로 전체 통과

## P5. Paper Trading 안전 진입
- [ ] `execution/order_executor.py` 3중 안전장치(포지션/일일손실/중복주문) 검증
- [ ] Calibration(슬리피지 보정) 실측 연동
- [ ] Phase 전환 자동 검증 조건 확정
- ⛔ **게이트: OOS 검증 통과 필요** — 2026-09-30 검증 결과 현 파라미터/전략은 IS→OOS에서 성과 붕괴(과최적화)로 **보류**
- 전제: P1~P4 완료, OOS 샤프/승률/MDD 기준 충족

## P6. 고도화
- [ ] 유니버스 파이프라인(`infrastructure/market_data/universe_provider.py`) 안정화
- [ ] 거시 지표 확장 (해외지수/금리/원자재)
- [ ] PostgreSQL 마이그레이션 검토
- [ ] 대시보드/리포트 고도화
