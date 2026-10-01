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
- 🔴 **OOS 검증 결과(중요)**: 기존 앙상블(Trend/Reversal/Breakout)은 견고한 엣지 없음
  (IS 중앙 Sharpe +2.158 → OOS **-0.192**, 2개 분할 붕괴) → **프로덕션 파라미터 반영 보류**
  - `Profit Factor`는 손실 분모가 작을 때 폭주(IS 79~142) → **단독 순위 지표로 사용 금지**
- 🎯 **횡단면 모멘텀은 유효**: 고정 파라미터(lb=120/k=20/hold=20)가 **4/4 연도구간 플러스**,
  거래비용 0.3% 반영 후 5년 Sharpe +0.98 → **관찰 리포트로 배선**(`scheduler/momentum_report.py`, 평일 08:30)
  - ✅ **생존편향 정량화 완료(P2-13)**: `--stress` 스트레스(상폐 시 -60% 가정, 데이터 소멸 종목도 손실로 계상)
    | 연간 상폐율 | 총수익 | CAGR | Sharpe | MDD |
    | :--- | :--- | :--- | :--- | :--- |
    | 0%(기준) | +180.9% | +27.3% | **+0.98** | 18.0% |
    | 2%(현실) | +167.0% | +25.8% | **+0.94** | 18.7% |
    | 5% | +147.5% | +23.5% | **+0.87** | 20.2% |
    | 10%(극단) | +118.0% | +19.9% | **+0.77** | 22.4% |
    OOS(2024-09-30~): 0% +1.42 / 5% +1.36 / 10% +1.30 → **엣지가 편향에 견고**
  - ✅ **운영 방식 결정(2026-10-01, 옵션 B 승인)**: **참고 신호(모의) 병행**
    주문/포지션 없음. `momentum_paper` 테이블에 일자별 픽을 기록하고 20거래일 경과 후
    실현 수익률을 자동 확정해 리포트에 누적 성과(승률·평균·중앙)로 표시
  - ⚠️ 남은 한계: OOS 2년 구간이 강세장 → 베타 기여분 미분리. **실거래 승격은 보류**

### 🔬 P5 알파/베타 분해 — 모멘텀 "엣지"는 베타였음 (2026-10-01)
`--alpha-beta`(동일가중 유니버스 회귀) 로 검증한 결과:

| 구간 | α(연) | β | R² | t | 판정 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 전구간(5년) | **−4.8%** | 1.22 | 0.81 | −0.74 | 비유의 |
| OOS | **−18.0%** | 1.49 | 0.89 | −1.20 | 비유의 |
| IS | −2.2% | 1.16 | 0.73 | −0.29 | 비유의 |

**벤치마크(유니버스 전체 동일가중 보유)가 모든 구간에서 전략을 압도**:
| 구간 | 전략 Sharpe | 벤치 Sharpe | 전략 CAGR | 벤치 CAGR |
| :--- | :--- | :--- | :--- | :--- |
| 전구간 | +0.94 | **+1.23** | +25.8% | +26.9% |
| OOS | +1.40 | **+1.89** | +76.3% | +71.9% |
| IS | +0.53 | **+0.74** | +9.4% | +10.8% |

→ **결론: 모멘텀은 시장 베타(β 1.2~1.5)를 사는 전략이며 초과수익(α)이 없다.**
→ **② 모멘텀 실거래 승격 = 부결.** 리포트에 α/β 사실을 명시(문구 정정).
→ 남은 함정: 벤치마크도 같은 생존편향 유니버스라 절대수치는 낙관적이나,
   **상대비교는 동일 편향이므로 유효**(전략이 단순 보유보다 나쁘다는 결론은 유지).

### 🔎 P5 신규 전략 발굴 스캔 (`--scan`, α 기준)
| 전략 | Sharpe | CAGR | α(연) | β | R² | t | MDD |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| benchmark | +1.23 | +26.9% | – | – | – | – | 16.0% |
| 모멘텀(120d) | +0.94 | +25.8% | −4.8% | 1.22 | 0.81 | −0.74 | 18.7% |
| 단기반전(20d) | +1.03 | +24.6% | −2.2% | 1.04 | 0.84 | −0.45 | 16.5% |
| **저변동성(120d)** | +0.98 | +17.0% | **−0.5%** | **0.68** | 0.67 | −0.09 | **14.2%** |
| 52주고가근접 | +0.76 | +16.1% | −8.0% | 0.98 | 0.83 | −1.68 | 18.9% |

OOS에서는 **저변동성이 유일하게 양의 알파**(α +10.4%/년, β 0.55, MDD 9.9%, t +0.81).
→ 유의기준(t>2) 통과 전략은 **아직 없음**. **저변동성을 차기 검증 1순위로 지정.**

### 🧪 P6 롱숏(시장중립) 진단 — 베타 제거 시 알파도 사라짐 (2026-10-01)
롱온리 전략은 모두 β≈1이라 베타가 수익을 지배한다. 베타를 상쇄한 롱숏(상위 k 매수 − 하위 k 매도,
양방향 비용 반영)으로 **순수 알파만** 측정:

| 구간 | 전략 | Sharpe | CAGR | α(연) | β | t |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 전구간 | 모멘텀(120d) | +0.01 | −2.2% | −9.0% | 0.37 | −0.84 |
| 전구간 | 단기반전(20d) | −0.10 | −3.4% | +0.0% | −0.07 | +0.00 |
| 전구간 | 저변동성(120d) | −0.72 | −18.9% | −3.3% | −0.55 | −0.30 |
| 전구간 | 52주고가근접 | −0.57 | −10.9% | −8.4% | −0.05 | −0.95 |
| OOS | 모멘텀(120d) | +0.41 | +8.6% | −26.1% | 0.73 | −1.14 |
| OOS | 저변동성(120d) | −0.63 | −27.0% | +31.5% | −0.86 | +1.04 |

→ **결론: 어떤 가격기반 팩터도 통계적으로 유의한 알파가 없다.**
   (롱숏 Sharpe −0.72~+0.41, |t| < 2 전부)
→ 저변동성의 OOS α +31.5%(t=1.04)는 표본 18회의 잡음 — Sharpe는 오히려 −0.63.
→ **시사점**: 이 시스템의 가치는 "알파 생성"이 아니라 **감시·리스크 알림**에 있다.
   가격 외 정보(감성/공시/ML)를 횡단면 팩터로 넣는 것이 차기 유일한 미개척 경로.
→ 한계: 표본 54회(전구간)/18회(OOS)로 검정력 낮음 · 벤치마크도 생존편향 유니버스.
### ✅ P3-1 고아 모듈 결정 (2026-10-01 완료)
| 모듈 | 결정 | 근거 |
| :--- | :--- | :--- |
| `observability/anomaly_detector.py` | ✅ **배선** | `OpsMonitor` 통해 `SignalPipeline.process()` 실시간 관측 |
| `observability/model_drift_detector.py` | ✅ **배선** | `OpsMonitor.record_outcome()` (결과 피드는 Phase 2) |
| `observability/root_cause_analyzer.py` | ✅ **배선** | 이상/드리프트 → RCA → 텔레그램(30분 쿨다운) |
| `observability/explainer_v2.py` | 🟡 보관(예약) | 모델+피처 계약 필요 → DeepAnalyzer ML 설명 계층에서 후속 |
| `observability/trace_propagation.py` | ✅ 이미 배선 | `data/db_manager.py`, `report/telegram_sender.py` (문서 표기 오류 정정) |
| `orchestrator/event_bus.py` | 🟡 보관 | `scripts/dev/simulate_flow.py` 개발도구 전용 |
| `orchestrator/feature_store.py` | 🟡 보관(예약) | `DailyMonitor`(자체 미배선) 전용 → 리포트 계층 후속 |
| `orchestrator/pipeline_manager.py` | 🟡 보관 | 레거시 진입점 `main.py`(롤백용) 전용 |
| `risk/circuit_breaker.py` | 🔴 **미배선(신규 발견)** | `CircuitBreakerManager` 프로덕션 참조 0건. `OpsMonitor`에 `cb_provider` 훅 준비됨 → 주문 실행 계층 배선 필요 |

**배선 신설**: `observability/ops_monitor.py` — 이상탐지 → 근본원인 → 스로틀 알림 파사드.
부수 수정: `_IsolationTree.fit`이 상수 특징에서 뿌리 리프가 되어 **모든 점을 0.5로 판정하던 버그** 수정(특징 폴백).

### ✅ P4-2 정적 검사 도입 + 🔴 뭉개진 파일 3개 복원 (2026-10-01)
- **ruff F 게이트**: CI에 `ruff check . --select F` 추가. F 규칙 82건 → **0건**
  (미사용 import 63, f-string 8, 미사용 변수 6, 재정의 5)
- 🔴 **`ast.parse` 거짓 통과 사고**: 파일이 **한 줄로 뭉개지면** 첫 `#` 주석이 전체를
  삼켜 '빈 모듈'로 파싱 성공 → 검사기(BOM/Syntax)가 **무음 통과**시키고 있었다.
  | 파일 | 상태 | 영향 |
  | :--- | :--- | :--- |
  | `data/dart_connector.py` | 539줄(04964c0) 복원 | 레거시 `scanner_main.py` import 실패 |
  | `tests/test_chaos_injection.py` | 281줄(97c90ba) 복원 | **테스트 3건 무음 미수집** |
  | `tests/test_telegram_events.py` | 194줄(97c90ba) 복원 | **테스트 1건 무음 미수집** |
  → 검사기에 **뭉개짐 탐지**(줄바꿈 0 + 200자 초과, 파싱 결과 빈 모듈) 추가해 재발 차단.
- **부수 버그 수정**: `StrategyRouter.route()`가 캐시 객체를 그대로 반환·변형해
  **이전 호출자의 결과가 나중에 바뀌던 별칭(alias) 버그** → 얕은 복사 반환으로 수정.
- **공허한 테스트 2종 재작성**: `test_stock_filter.py`·`test_strategy_router.py`가
  대상 코드를 호출조차 하지 않고 지역 변수만 검사하고 있었다(ruff F841이 적발) → 실제 API 검증으로 교체.
- 🟠 `DB_TYPE`은 코드 미사용 (DB 선택은 `DATABASE_URL` 유무로 결정) — 정리 대상
- 🔎 성과추적기 실측값은 DB에 결정이 쌓여야 의미 있음(현재 decisions 0건)

### 신규 도구 (2026-09-30)
| 도구 | 용도 |
| :--- | :--- |
| `core/runtime_mode.py` | 안전모드(TEST_MODE/DRY_RUN/MOCK_DATA_ENABLED/TELEGRAM_ENABLED) 실동작 |
| `scheduler/ohlcv_backfill.py` | yfinance 일봉 백필 (5년 230,297행 적재) |
| `scheduler/universe_fetcher.py` | 네이버 금융 API 동적 유니버스(515종목) |
| `scheduler/momentum_report.py` | 모멘텀 관찰 리포트(매매 아님) |
| `validation/backtest_runner.py` | DB OHLCV → Walk-Forward (ma_cross/ensemble) |
| `validation/strategy_backtest.py` | 프로덕션 앙상블 재생 + 진입 필터 |
| `validation/backtest_sweep.py` | 다종목 스윕 · OOS · 롤링 OOS |
| `validation/momentum_backtest.py` | 횡단면 모멘텀 + 거래비용 + OOS |
| `tests/run_all.py` + `.github/workflows/ci.yml` | 통합 테스트 실행기 + CI |

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
