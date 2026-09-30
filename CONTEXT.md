# 🔬 프로젝트 상태 저장소 (Full Context) — V10

> 📌 목적: 새 대화/타 세션 인수인계 시 10분 내 시스템 상태를 복원하는 명세서.
> 📅 최종 갱신: 2026-09-30 | 브랜치: `genspark_ai_developer` | HEAD: `9c0b0a0`
> ⚠️ 이전 버전(v7.x/v8.0) CONTEXT는 스테일이었음. 본 문서는 **현재 코드 기준**입니다.

---

## 1. 프로젝트 기본 정보

| 항목 | 값 |
| :--- | :--- |
| 프로젝트명 | stock_analyzer |
| 아키텍처 | V10 (DDD/계층 분리, Strangler Fig 마이그레이션 진행 중) |
| Python | 3.12.9 (Windows) |
| 운영 모드 | Phase 1 Shadow Mode (실시간 감시 + Telegram 알림, 자동매매 없음) |
| 공식 진입점 | `python app/main.py` → `app/bootstrap.py` |
| 레거시 진입점 | `scanner_main.py`(v8.0, DeprecationWarning), `main.py`(v5.1.2, 롤백용) |
| 리포 | https://github.com/djrtn3836-ship-it/-.git |
| 테스트 | pytest (`pytest.ini`, asyncio_mode=auto). `tests/`(root 6) + `tests/unit`(42) + integration + performance |

---

## 2. 부트스트랩 배선 현황 (`app/bootstrap.py`, 약 1206줄)

기동 순서(요약): env 로드 → validate → PID → 전역 예외 핸들러 → Supervisor → Telegram →
거시 데이터 수집 → **AppContainer(DI)** → Kiwoom 연결 → RealtimeMonitor → RegimeManager →
DeepAnalyzer → HyperparameterTuner → SentimentPipeline → 데이터소스 → **PerformanceTracker** →
AB Framework → OrderExecutor(Paper) → Telegram 명령어 → Scheduler → Workers → SafetyGuard → 메인 루프.

자동 배선되는 주요 기능:
- **SafetyGuard** (`risk/safety_guard.py`) — 메인 루프 주기 점검, 위기 시 진입 차단
- **PhaseTransitionValidator** (`monitor/phase_transition_validator.py`) — 스케줄 등록
- **AlertVerifier** (`analytics/alert_verifier.py`) — 알림 누락 검증 스케줄
- **DailyReport / WeeklyPDF** — 리포트 생성
- **FeedbackLearner** — ML 피드백 학습
- **observability/health_score** — 헬스 스코어

---

## 3. 디렉터리 구조 (V10 혼재 → 정리 대상)

```text
[V10 신규]
  app/            main.py(진입점), bootstrap.py, __init__.py
  domain/         models/, strategies/
  application/    analysis/(signal_pipeline, atr_service, hyperparameter_tuner, ...)
  infrastructure/ cache/, dart/, database/, market_data/, news/
  observability/  tracer, trace_id, trace_config, auto_trace, health_score, ...
[기존 공존]
  core/ data/ scanner/ analytics/ risk/ filters/ report/ scheduler/ monitor/
  decision/ collector/ regime/ execution/ feedback/ orchestrator/ validation/
[레거시/정리대상]
  main.py, scanner_main.py
[문서/스크립트]
  CONTEXT.md, ROADMAP.md, DEVELOPMENT_LOG.md, docs/, scripts/
```

---

## 4. 현재 상태 확인 결과 (2026-09-30)

### 이전 피드백 이슈 → 현재 상태
| 이슈 (v8.0 피드백) | 현재 상태 |
| :--- | :--- |
| hybrid_decider 한글 문자열 액션 불일치 | ✅ 해소 (`ActionType` enum) |
| ML 피처 불일치 (1개 학습/6개 예측) | ✅ 해소 (`FEATURE_COLS` 6개로 학습) |
| krx_universe.csv 손상(HTML) | ✅ 대체 (`infrastructure/market_data/universe_provider.py`) |
| SafetyGuard/PhaseTransition 고아 모듈 | ✅ 배선됨 (bootstrap) |
| 105115 구독 초과 | ✅ `max_subscriptions=195` |
| 구문 오류 | ✅ 179개 파일 실제 SyntaxError 0건 |

### 미해결/확인 필요
- ✅ UTF-8 **BOM 56개 파일 제거 완료** (2026-09-30, BOM 0)
- ✅ env 변수명 불일치 완화 (별칭 정규화 + `.env.example` 표준화) — 실제 자격증명 입력/실부팅 성공
- ✅ **실 부팅 성공 + Telegram 알림 수신 확인** (2026-09-30, Kiwoom 195종목 구독)
- ✅ 유니버스 **단일 소스화**(V10 provider) + 폴백 CRITICAL 알림 배선
- ⚠️ 중복 파일/고아 모듈 일부 정리, 테스트 결합분은 배선 필요
- ✅ **동적 유니버스 소스 부재**: `data/krx_universe.csv` 없음 → 하드코딩 238종목 폴백. 단일 소스화 + 폴백 CRITICAL 알림은 완료, 실제 KRX API/pykrx 도입은 후속
- ✅ **Kiwoom 무한 재시도 수정**: 최대 5회 + 지수 백오프 후 명확 종료 (`app/bootstrap.py:connect_kiwoom`)
- ✅ aiohttp `Unclosed client session` 정리 (news_crawler/dart_connector를 shutdown에서 disconnect)
- ✅ **단일 인스턴스 가드**: PID 실프로세스 검증 + 텔레그램 프리플라이트 Conflict 감지 (중복 실행 시 폴링 미시작)
- ✅ **테스트 스위트 정상화**: `pytest tests/` → 1147 passed / 0 failed (pyproject 마커 손상·예외 import 오류 복구)
- ✅ 백테스터/성과추적기 실측 검증 완료 (테스트 24개 신규)
- ✅ **백테스터 배선 완료**: `validation/backtest_runner.py` (DB OHLCV → MA교차 시뮬레이션 → Walk-Forward, CLI 제공)
- ✅ **OHLCV 적재 경로 확보**: `scheduler/ohlcv_backfill.py`(yfinance 백필) — **190/195종목 · 92,104행** 적재
- ✅ **백테스트 프로덕션 앙상블 연결**: `validation/strategy_backtest.py` (Trend/Reversal/Breakout 투표 기반 결합)
  - CLI: `python -m validation.backtest_runner <ticker> <start> <end> [--compare]`
- ✅ **다종목 스윕 완료**: `validation/backtest_sweep.py` — 185종목·48조합 4초
  - 지표 캡/중앙값 정렬/pooled Sharpe 로 통계 왜곡 교정 완료
- 🔴 **OOS 검증 결과(중요)**: P2-8의 최적 파라미터는 **과최적화** — IS 중앙 Sharpe +2.158 → OOS **-0.192** (2개 분할 모두 붕괴)
  - **프로덕션(`signal_pipeline`) 반영 보류** 결정. 향후 데이터 5년 확대 + 롤링 OOS 재검증 필요
  - `Profit Factor`는 손실 분모가 작을 때 폭주(IS 79~142) → **단독 순위 지표로 사용 금지**
- ✅ **안전모드 구현**: `.env`의 `TEST_MODE`/`DRY_RUN`/`MOCK_DATA_ENABLED`/`TELEGRAM_ENABLED` 실동작화
  (`core/runtime_mode.py` + `infrastructure/market_data/mock_kiwoom_connector.py`, 자격증명 없이 부팅 검증됨)
- 🟠 `DB_TYPE`은 여전히 코드 미사용 (DB 선택은 `DATABASE_URL` 유무로 결정) — 정리 대상
- 🟠 중복 인스턴스 시 Telegram `getUpdates Conflict` — 단일 인스턴스 가드 필요
- 🔎 `backtester` 실구현/Walk-Forward, `performance_tracker` 실측값 — 재검증 필요

---

## 5. 중요 설정값

| 설정 | 값 | 파일 |
| :--- | :--- | :--- |
| 최대 구독 종목 | 195 | `scanner/realtime_monitor.py` |
| REG 등록 간격 | 0.3초 | `scanner/realtime_monitor.py` |
| 변동률 임계값 | 2% | `scanner/realtime_monitor.py` |
| VaR 갱신 간격 | 300초 | `config/risk_config.yaml` |
| Supervisor 감시 간격 | 30초 | `core/supervisor.py` |
| Phase 전환 검증 | 스케줄 등록 | `app/bootstrap.py` |

---

## 6. 규칙 및 주의사항
- 승인 기반 개발, 기존 기능 보존(삭제 시 승인 + `_archive/`), **UTF-8 (BOM 없음)** 엄수.
- 피드백 문서(`달콩/피드백/*.txt`)는 v8.0 시점 → 코드로 재검증 후 판단.
- 작업 완료 시 `DEVELOPMENT_LOG.md`, 계획 변경 시 `ROADMAP.md` 최신화.
