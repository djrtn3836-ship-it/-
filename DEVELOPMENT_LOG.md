# DEVELOPMENT_LOG — 세션 인수인계 기록

> 타 세션/일반 채팅으로 전환되어도 작업 연속성을 유지하기 위한 기록.
> 매 작업 완료 시 [진행 사항 / 변경 파일 / 다음 작업 / 주의사항]을 최신화한다.
> 최신 항목을 맨 위에 추가한다.

---

## [2026-09-30] Session A1 — 프로젝트 정밀 파악 및 P0 정리 착수

### 진행 사항
- 전 프로젝트 전수 분석 (구문/참조/중복/고아/인코딩)
- 실제 상태 확인: V10 DDD, 공식 진입점 `app/main.py → app/bootstrap.py`
- 피드백 문서(v8.0 시점) 다수 이슈가 V10에서 이미 해소됨을 확인
  (hybrid_decider ActionType, feedback_learner FEATURE_COLS, universe_provider, SafetyGuard/PhaseTransition 배선)
- 발견: UTF-8 BOM 파일 56개 (사용자 규칙 위반) → **전량 제거 완료 (BOM 0)**
- V10 기준 문서 재작성 및 ROADMAP/DEVELOPMENT_LOG 신설
- A등급 스테일 산출물 정리 완료

### 변경 파일
- `ROADMAP.md` (재작성), `DEVELOPMENT_LOG.md` (신설), `CONTEXT.md` (V10 재작성)
- BOM 제거: 56개 파일 (바이트 레벨 BOM만 제거, 내용 무변경)
- A등급 이동:
  - `_archive/stale_20260930/` ← mypy_*.txt(5), pytest_config.txt, config.yaml.bak, project_packed.txt, project_structure.txt, dependency_map.json, app_session49*.log
  - `docs/sessions/` ← .SESSION_46_*, .SESSION_49_REPORT.md, SESSION_57_METHOD_ANALYSIS.md, TEST_REPORT.md
  - `scripts/dev/` ← make_light_context.py, pack_project.py, analyze_project.py, flow_analyzer.py, simulate_flow.py
- `.gitignore` ← `_archive/` 등 추가
- `scripts/dev/run_masked.py` (신규) — 시크릿 마스킹 실행기
- `app/bootstrap.py`, `data/kiwoom_connector.py` — env 별칭 정규화
- `.env.example` — 표준 변수명으로 재작성
- 검증: py 179개 BOM 0 / SyntaxError 0

### 🔴 P0 부팅 검증 (2026-09-30)
- 실행: `python app/main.py` → `validate_env()`에서 **CRITICAL: 필수 환경변수 누락: KIWOOM_APP_KEY, KIWOOM_APP_SECRET** → `sys.exit(1)`
- 종료 처리는 정상(exit code 0, "System shutdown complete")
- 근본 원인 (2건):
  1. `.env`의 키 이름이 코드 기대값과 불일치: `.env`=`KIWOOM_API_KEY`/`KIWOOM_SECRET_KEY` ↔ 코드=`KIWOOM_APP_KEY`/`KIWOOM_APP_SECRET`
  2. `.env`의 모든 자격증명이 **placeholder** (KIWOOM/DART/TELEGRAM/NAVER 값 = `your_..._here` 형태) → 실제 연결/알림 불가
- env 정합성 분석 (코드가 읽는 키 15개):
  - 누락: `DATABASE_URL`, `DATABASE_URL_READ`, `KIWOOM_APP_KEY`, `KIWOOM_APP_SECRET`, `LOG_DIR`, `REDIS_URL`, `STRUCTURED_LOGGING`
  - `.env`에 있으나 코드 미사용: 96개 (타 템플릿 잔재)
- `.env.example`은 또 다른 네이밍(`APP_NAME`, `MARKET_OPEN`, `KIWOOM_ACCOUNT_ID`…) → **3중 불일치(.env ↔ .env.example ↔ 코드)**

### ✅ P0 부팅 스모크 테스트 (2026-09-30, Task 2+3 반영)
- Task 3(코드): `bootstrap.load_env()`에 **env 별칭 정규화**(`KIWOOM_API_KEY`→`KIWOOM_APP_KEY` 등) 추가, `kiwoom_connector` getenv 폴백, `.env.example` 표준명으로 재작성
- Task 2(.env): 키 2개 정정(`KIWOOM_APP_KEY`/`KIWOOM_APP_SECRET`), 백업 `.env.bak_20260930`
- 스모크 결과 (백그라운드 실행 후 중지):
  - ✅ `validate_env` 통과 → PID → 전역 예외 핸들러 → Supervisor → Telegram init
  - ✅ **거시 데이터 실수집 성공** (USD/KRW 1354.57, VIX 16.08, US10Y 5.26%, S&P/나스닥/SOX/WTI) — 네트워크 정상. (KTB3Y 410 오류, KOSPI ^KS200 데이터 1행 부족 → None)
  - ✅ DI 컨테이너 초기화 → **SQLite** 사용(Redis 비활성, DATABASE_URL 미설정)
  - ❌ `connect_kiwoom` → placeholder 키로 토큰 발급 실패 → **60초 간격 무한 재시도**(종료 안 됨)
- 발견 (신규):
  1. **`app/bootstrap.py:253`** `while not is_connected()`에 **최대 재시도/타임아웃 없음** → 자격증명 오류 시 영구 루프 (우선 수정 필요)
  2. `.env`의 `TEST_MODE`/`DRY_RUN`/`MOCK_DATA_ENABLED`/`TELEGRAM_ENABLED`/`DB_TYPE` = **코드에서 0건 참조(죽은 플래그)** → 안전모드 부재
  3. 텔레그램 토큰/챗ID placeholder → 알림 실발송 불가
- 결론: 부팅 시퀀스 자체는 정상. **실 부팅/실 알림은 실제 자격증명 필요**

### ✅ P0 실 부팅 성공 (2026-09-30, 실제 자격증명)
- 실행기: `scripts/dev/run_masked.py` (시크릿 마스킹 실행)
- 결과: **`V10 System Bootstrap Complete`** — 전 서브시스템 활성
  - Kiwoom REST+WebSocket 연결 성공(retries=1) → **195종목 REG 성공 195/실패 0**
  - SignalPipeline / HyperparameterTuner / SentimentPipeline = Active
  - Scheduler 9 jobs (daily_report, feedback_learning, weekly_pdf, daily_ohlcv, macro_update, phase_transition_check, calibration, alert_verifier, hyperparameter_tuning)
  - Health server: `http://0.0.0.0:8081/health`
  - **✅ Telegram 메시지 전송 성공** (부팅 알림 실발송 확인) → 알림 E2E 경로 정상
  - 거시 실데이터 수집(USD/KRW 1354, VIX 16.1, US10Y 5.26%, S&P/나스닥/SOX/WTI)
- 신규 발견 (다음 작업 후보):
  1. 🔴 **유니버스 하드코딩 폴백 사용** — 로그: `📦 하드코딩 500종목 사용 (CSV 없음 또는 읽기 실패)` → 238개 검증 → 195 구독. V10 `infrastructure/market_data/universe_provider.py`가 `scanner/realtime_monitor.py` 경로에 **배선되지 않음** (신규상장/시총변동 미반영)
  2. 🟠 **Telegram `Conflict: terminated by other getUpdates request`** — 동일 봇 토큰으로 **2개 인스턴스 동시 실행** 시 발생. 앱은 poller를 1개만 생성하므로 외부 중복 인스턴스 문제. 단일 인스턴스 강제 필요
  3. 🟠 `🚨 [ASYNCIO] Unclosed client session` 경고(aiohttp) — 세션 정리 필요
  4. 🔎 초기 REG 중 WebSocket 반복 재연결(1/5~4/5) 후 성공 — 초기 대량 구독 안정성 개선 여지
  5. 🔎 macro: KTB3Y(네이버 410) 수집 실패, KOSPI `^KS200` 데이터 1행 → trend None

### 다음 작업
- ✅ P1-1: 유니버스 파이프라인 배선 **완료** (V10 provider 단일화 + 폴백 CRITICAL 알림)
- ✅ P1-3: Kiwoom 무한 재시도 수정(최대 5회+백오프) + aiohttp 세션 정리 **완료**
- ✅ P1-2: 단일 인스턴스 가드(PID 검증+텔레그램 프리플라이트) **완료**
- ✅ P2-2: 백테스터/성과추적기 실측 검증 **완료** (테스트 24개 신규, 전체 스위트 1147 passed)
- ✅ P2-3: 안전모드 구현 **완료** (TEST_MODE/DRY_RUN/MOCK_DATA_ENABLED/TELEGRAM_ENABLED 실동작화, 1165 passed)
- ✅ P2-4: 백테스터 배선 **완료** (DB OHLCV → MA교차 시뮬레이션 → Walk-Forward, CLI 포함, 1179 passed)
- ✅ P2-5: OHLCV 적재 경로 확보 **완료** (yfinance 백필 3,880행 적재, 실 백테스트 수치 산출, 1190 passed)
- ✅ P2-7: 전체 유니버스 백필 **완료** (190/195종목, 92,104행)
- ✅ P2-6: 백테스트 프로덕션 앙상블 연결 **완료** (투표 기반 결합, 실데이터 비교, 1206 passed)
- ✅ P2-8: 다종목(185) 일괄 백테스트 + 48조합 스윕 **완료** (최적 thr=0.15/hold=20d/stop=5%/tp=15%, 1216 passed)
- 🔬 P2-9: 진짜 OOS 검증 **완료 — 최적 파라미터는 과최적화로 판정, 프로덕션 반영 보류** (1219 passed)
- 🔴 P2-10: 데이터 기간 확대(5년) + 롤링 다중 분할 OOS 재검증 / 전략 로직 개선
- 🟠 P1-4: 동적 유니버스 파이프라인(KRX API/pykrx) 도입 검토 — data/krx_universe.csv 부재 해소
- 📊 P2-1: 알림 누락 검증기(`alert_verifier`) 신뢰성 실측
- B등급 잔여: 테스트 결합 고아 모듈(`event_bus`/`feature_store`/`pipeline_manager`/관측성 5종) 배선 여부 결정
- P2: `python app/main.py`는 **한 번에 1개만** 실행 (중복 실행 금지)
- P1: 백테스터/성과추적기 실측 검증, 알림 누락 검증기 신뢰성

### ✅ P1-1 유니버스 파이프라인 배선 (2026-09-30)
- 문제: 유니버스가 `data/stock_universe.py`(사용 중)와 `infrastructure/market_data/universe_provider.py`(고아 중복)로 이원화, 둘 다 하드코딩 **238종목** 폴백을 조용히 사용
- 조치:
  1. **단일 소스화** — 정본을 `infrastructure/market_data/universe_provider.py`로 확정
     - `data/stock_universe.py` → **deprecated shim**(정본 재노출)로 축소, 중복 하드코딩 제거
     - import 갱신: `scanner/realtime_monitor.py`, `report/telegram_commands.py` → 정본 경로
  2. **조용한 폴백 금지** — provider에 `get_last_source()`/`is_fallback()` 추가, 폴백 시 `logger.critical`
  3. **부트스트랩 CRITICAL 알림** — `app/bootstrap.py`에 `_check_universe_source()` 추가(모니터 시작 직후),
     폴백이면 `_send_error_alert()`로 텔레그램 경고 + `startup_details["universe_source"]` 기록
- 검증: 구문 0건/BOM 0, `import app.bootstrap` OK, 폴백 시 `alert_sent=True` 확인
- 남은 과제(별도): `data/krx_universe.csv` 자체가 없고 **동적 유니버스 소스(KRX API/pykrx) 미도입** →
  현 상태로는 매 부팅 시 폴백 경고가 발생(의도된 loud 동작). 실제 유니버스 갱신 파이프라인은 후속 작업 필요

### ✅ P1-3 Kiwoom 무한 재시도 수정 + aiohttp 세션 정리 (2026-09-30)
- 문제 1: `app/bootstrap.py:connect_kiwoom()`의 `while not is_connected()`에 **최대 시도/타임아웃 없음** → 자격증명 오류 시 영구 대기
  - `config/schema.py`에 `reconnect_max_attempts=5`, `reconnect_base_delay=2`, `reconnect_max_delay=60`가 **이미 정의되어 있었으나 무시**됨
  - 수정: 최대 시도 초과 시 `CRITICAL` 로그 + 텔레그램 경고 + `RuntimeError`로 **명확히 종료**, 시도 간 **지수 백오프**(2→4→8→16→32s, 상한 60s)
  - 검증: 가짜 커넥터로 5회 시도 후 RuntimeError + alert 전송 확인(`attempts=5, alert_sent=True`)
- 문제 2: `Unclosed client session`(aiohttp) 경고
  - 원인: `init_data_sources()`의 DART와 `init_sentiment_pipeline()`의 NewsCrawler 세션이 종료 시 닫히지 않음(로컬 변수로 소멸)
  - 수정: `self.news_crawler` / `self.dart_connector`에 보관 → `shutdown()`에서 `disconnect()` 호출
- 검증: py 177개 / SyntaxError 0 / BOM 0 / `import app.bootstrap` OK

### ✅ P1-2 단일 인스턴스 가드 강화 (2026-09-30)
- 문제: 실부팅에서 `telegram.error.Conflict: terminated by other getUpdates request` 발생 → 동일 봇으로 2개 인스턴스가 폴링하면 무한 에러 스팸
- 조치 (3중 가드):
  1. **PID 가드 강화**(`app/bootstrap.py:manage_pid`) — PID 존재 시 해당 프로세스가 **실제 python 프로세스인지 검증**(`tasklist /FO CSV`) → PID 재사용 오탐 방지, 생존 시 `SystemExit(1)`, 스테일 파일은 정리
  2. **텔레그램 프리플라이트**(`report/telegram_commands.py:start`) — 폴링 시작 전 비파괴 `get_updates(timeout=0, limit=1)`(offset 미지정)로 **Conflict 사전 감지** → 중복이면 폴링 시작 안 함(`return False`) + 앱 자원 정리
  3. **부트스트랩 연동** — `start()`가 `True/False` 반환, Conflict 시 CRITICAL 로그 + 텔레그램 경고
- 부수 수정(한글/인코딩 보호): `print()`에 cp949 미지원 문자(`❌`,`—`) 사용 시 `UnicodeEncodeError` 발생 → ASCII 안전 문자열로 교체
- 검증:
  - Conflict 상황 → `start()=False`, polling 미시작 ✅
  - 정상 상황 → `start()=True`, polling 시작 ✅
  - 살아있는 다른 python 프로세스 + PID 파일 → `SystemExit(1)` 차단 ✅ / 스테일 PID 정리 ✅
  - py 177개 / SyntaxError 0 / BOM 0 / `import app.bootstrap` OK

### ✅ P2-2 백테스터/성과추적기 실측 검증 (2026-09-30)
- **백테스터**: `validation/backtester.py`(418줄)는 구현은 실재하나 **배선·테스트 전무(고아 모듈)**
  → 신규 테스트 `tests/unit/test_backtester.py` **22개** 작성 (지표 수학, BacktestResult, WalkForward rolling/anchored 분할, 집계, 파사드)
- **성과추적기**: `_get_daily_returns()`가 DB outcome 기반으로 실수치를 산출하는지 미검증 상태였음
  → 신규 테스트 `tests/unit/test_performance_tracker_real.py` 작성: 임시 SQLite에 decision+outcome 6건 시딩 →
  `total_trades=6, win_rate=1.0, equity=101.0, daily_pnl=1.0%` 실측 확인 / 빈 DB 안전성 확인
- **발견·수정한 차단 이슈 3건**:
  1. 🔴 `pyproject.toml` `[tool.pytest.ini_options].markers` 손상(한글 `?` 유실 + 닫는 따옴표 누락)
     → **TOML 파싱 실패로 `pytest` 실행 자체 불가** → 마커 복구
  2. 🔴 `tests/unit/test_core_utils_final.py`가 존재하지 않는 `ValidationError`/`DataError`/`ExecutionError` import
     → **collection error로 전체 스위트 중단** → `core/exceptions.py`에 3종 **추가**(순수 추가, 기존 동작 무변경)
  3. 🟠 `is_trading_day("2024-01-15")` 호출 시 `AttributeError` → ISO 문자열 허용으로 보강(기존 동작 무변경)
- **결과: `pytest tests/` → 1147 passed / 0 failed / 0 error** (이전: 수집조차 불가)
- 남은 사실(후속):
  - 백테스터는 여전히 **미배선**. 실백테스트에는 DB OHLCV → 전략 시뮬레이션 어댑터 필요
  - `BacktestResult.compute()`가 비-ISO 날짜에서 ValueError → fold가 조용히 빈 결과(경고만) — 취약점
  - 운영 DB(`data/decisions.db`)는 **행 0건**(스키마만 존재) → 성과추적기 실값이 0인 원인은 데이터 부재(코드 결함 아님)

### ✅ P2-3 안전모드 구현 (2026-09-30)
- 문제: `.env`의 `TEST_MODE`/`DRY_RUN`/`MOCK_DATA_ENABLED`/`TELEGRAM_ENABLED`/`DB_TYPE`이 **코드 0건 참조(죽은 플래그)** → 문서상 "있는 척" 상태
- 신규 모듈:
  - `core/runtime_mode.py` — 플래그 단일 소스(`load_runtime_mode`/`get_runtime_mode`), 오설정 방지 규칙
    (TEST_MODE→MOCK_DATA 자동+TELEGRAM 강제 차단, DRY_RUN→TELEGRAM 차단)
  - `infrastructure/market_data/mock_kiwoom_connector.py` — 네트워크/자격증명 없이 부팅 가능한 모의 커넥터
- 배선(부트스트랩):
  - `validate_env` : TEST_MODE면 필수 자격증명 누락 허용(경고 후 계속)
  - `bootstrap`    : 기동 시 안전모드 로그 + `startup_details["runtime_mode"]`, 거시 수집 생략
  - `connect_kiwoom`: TEST_MODE면 실제 연결 생략 → 모의 커넥터로 대체
  - `init_sentiment_pipeline` / `init_data_sources`: TEST_MODE면 뉴스·DART 생략
  - `start_telegram_commands`: TELEGRAM_ENABLED=0/DRY_RUN이면 폴링 생략
  - `TelegramSender.send_raw`: 안전모드면 실발송 대신 로그만(True 반환)
- 검증:
  - 신규 테스트 `tests/unit/test_runtime_mode.py` **18개** 통과(플래그 파싱/우선순위/라벨/캐시)
  - **TEST_MODE 실부팅 성공**: 자격증명 없이 `V10 System Bootstrap Complete`
    (Tickers 195 / Scheduler 9 / SignalPipeline Active / Main loop / Health server :8080)
    SAFE MODE 로그 확인: 거시·키움·뉴스·DART·텔레그램 폴링 전부 생략
  - 전체: py 182개 / SyntaxError 0 / BOM 0 / **pytest 1165 passed**
- 주의: `DB_TYPE`은 기존에도 코드 미사용이며, DB는 `DATABASE_URL` 유무로 SQLite/PostgreSQL이 결정됨(별도 정리 대상)

### ✅ P2-4 백테스터 배선 (2026-09-30)
- 문제: `validation/backtester.py`가 **고아 모듈**(데이터·전략 미연결)이라 검증만 되고 실제 수치 산출 불가
- 신규 `validation/backtest_runner.py` — **DB OHLCV → 전략 시뮬레이션 → Walk-Forward** 전 구간 배선
  - 지표(순수 Python): `sma` / `ema` / `rsi`(Wilder)
  - `MACrossSimulator`: 단기/장기 MA 상향 돌파 진입 + 손절/익절/보유기간 청산(겹침 없음)
  - `BacktestRunner`: `db.get_ohlcv_range()` → 폴드 콜백 주입 → `Backtester.run_walk_forward()` → `BacktestReport`
  - CLI: `python -m validation.backtest_runner <ticker> <start> <end>`
- 검증: 신규 테스트 `tests/unit/test_backtest_runner.py` **14개**
  (지표 정확성, 진입 인덱스, 보유기간/익절/손절 청산, 합성 데이터 Walk-Forward, 빈 데이터/DB 미주입 안전성, 실 DB 저장→조회)
- 부수 수정: CLI에서 cp949 콘솔 이모지 출력 시 `UnicodeEncodeError` → stdout UTF-8(replace) 재설정
- 전체: py 184개 / SyntaxError 0 / BOM 0 / **pytest 1179 passed**
- 남은 사실(중요):
  - 운영 DB `data/decisions.db`의 `ohlcv`는 **행 0건** → 실 백테스트 수치를 얻으려면
    `scheduler/daily_collector`가 실 키움으로 OHLCV를 적재해야 함(또는 데이터 임포트 경로 필요)
  - 따라서 CLI는 현재 "데이터 없음" 리포트를 정상 반환(크래시 없음)

### ✅ P2-5 OHLCV 적재 경로 확보 + 실 백테스트 수치 산출 (2026-09-30)
- 문제: `data/decisions.db`의 `ohlcv`가 **0건**이라 백테스트·성과지표가 모두 0
  - 키움 `request_tr(ticker, "일봉")`은 `dt=어제` **단일 시점 조회** → 과거 이력 백필에 부적합
- 신규 `scheduler/ohlcv_backfill.py` — **yfinance 기반 일봉 백필 유틸**(프로젝트가 이미 yfinance 사용 중)
  - `.KS`(KOSPI) → `.KQ`(KOSDAQ) 자동 폴백, MultiIndex 컬럼 평탄화, NaN 안전 변환
  - 네트워크 접근을 `fetch_history()`에 격리 → 테스트에서 fetcher 주입(오프라인 검증 가능)
  - 저장은 `db.save_ohlcv_batch()`(배치, UNIQUE(ticker,date)로 중복 안전)
  - CLI: `python -m scheduler.ohlcv_backfill --tickers 005930,... --period 2y`
- **실행 결과**: 8종목 × 485행 = **3,880행 적재** (2024-09-30 ~ 2026-09-30, 실패 0, 7.5초)
- **실 백테스트 수치 산출 성공** (`python -m validation.backtest_runner <ticker> 2024-10-01 2026-09-30`)
  | 종목 | 봉 | 거래 | 평균 Sharpe | 평균 승률 | Profit Factor |
  |---|---|---|---|---|---|
  | 005930 | 484 | 6 | -10.657 | 12.5% | 0.23 |
  | 000660 | 484 | 3 | 1.144 | 12.5% | 1.09 |
  | 035420 | 484 | 3 | 0.000 | 25.0% | 1.83 |
- 검증: 신규 테스트 `tests/unit/test_ohlcv_backfill.py` **11개**(헬퍼/폴백/변환/집계) / 전체 **1190 passed**, py 186개·BOM 0·SyntaxError 0
- 주의(후속):
  - 현재 백테스트 전략은 **MA교차 데모 시뮬레이터**다. 프로덕션 앙상블(`domain/strategies` Trend/Reversal/Breakout) 연결은 별도 작업
  - 거래 수가 3~6건으로 적어 통계적 신뢰구간이 넓음 → 규칙/기간 튜닝 필요
  - `data/decisions.db`는 `.gitignore`(`*.db`) 대상 → 데이터가 리포에 커밋되지 않음(정상)

### ✅ P2-7 전체 유니버스 OHLCV 백필 (2026-09-30)
- 실행: `python -m scheduler.ohlcv_backfill --limit 195 --period 2y --delay 0.2`
- 결과: **성공 190/195종목 · 92,104행 적재** (2024-09-30 ~ 2026-09-30), 실패 5건은 상장폐지/데이터 없음(예: 049770, 139050, 140910, 144620, 174880) — 실패는 경고 후 계속(크래시 없음)
- 소요: 196.6초
- DB: `ohlcv` 92,104행 / 190종목

### ✅ P2-6 백테스트 전략을 프로덕션 앙상블로 교체 (2026-09-30)
- 신규 `validation/strategy_backtest.py` — `domain/strategies`의 Trend/Reversal/Breakout을 일봉 위에서 재생
  - 지표(순수 Python, 후방 참조 → 룩어헤드 없음): MACD(12/26/9), Bollinger(20,2σ), Stochastic(14,3), volume_ratio, 52주 고저
  - `compute_indicators()` — 봉별 `tech_data`/`regime`/52주 고저 생성, 60봉 워밍업
  - `EnsembleEvaluator` — **투표 기반 결합**: buy=Σ(w·score|action=BUY), sell=Σ(w·score|action=SELL), net=(buy−sell)/Σw, `net ≥ 0.25`이면 BUY
    - 근거: 단순 점수 평균은 HOLD(고점수)와 SELL(저점수)를 구분하지 못해 신호가 희석됨(실측 결합 0.551 고정 → 임계 0.58 미달)
  - `_edge_indices()` — 구간(폴드) 시작 상태를 새 출발로 보는 엣지 트리거(폴드별 재진입 보장)
- `BacktestRunner`에 `signal_mode="ensemble"` 추가, CLI `--strategy {ma_cross,ensemble}` / `--compare`
- **실데이터 비교 결과** (2024-10-02 ~ 2026-09-30, 484봉)
  | 종목 | 전략 | 거래 | 평균 Sharpe | 평균 승률 | PF |
  |---|---|---|---|---|---|
  | 005930 | ma_cross | 6 | -10.657 | 12.5% | 0.23 |
  | 005930 | **ensemble** | 7 | **0.935** | **45.8%** | **1.82** |
  | 000660 | ma_cross | 3 | 1.144 | 12.5% | 1.09 |
  | 000660 | **ensemble** | 8 | 0.000 | **58.3%** | **1.53** |
  | 035420 | ma_cross | 3 | 0.000 | 25.0% | 1.83 |
  | 035420 | **ensemble** | 7 | -4.173 | 33.3% | **2.34** |
- 검증: 신규 테스트 `tests/unit/test_strategy_backtest.py` **16개** / 전체 **1206 passed**, py 188개·BOM 0·SyntaxError 0
- 남은 것(후속): 앙상블 임계값(0.25)·청산 규칙(5일/±10%·-5%)은 **아직 튜닝 전**이며 표본이 4폴드로 작음 → 다종목 일괄 백테스트 + 파라미터 스윕 필요

### ✅ P2-8 다종목 일괄 백테스트 + 파라미터 스윕 (2026-09-30)
- 신규 `validation/backtest_sweep.py`
  - `prepare_series()` — 종목별 OHLCV 로드 + **net score 1회 캐시**(전략 호출이 병목이라 재사용)
  - `evaluate_combo()` / `run_sweep()` — 임계값 × 청산규칙 격자 탐색 → 포트폴리오 집계
  - `python -m validation.backtest_sweep --limit 190 --start ... --end ... [--sweep] [--thresholds] [--holds] [--top]`
- 성능: 190종목 준비 + 48조합 스윕 = **4.0초** (net score 캐시 효과)
- **기준선**(thr=0.25, hold=5d, stop=5%, tp=10%): 185종목·1,667거래, 평균 Sharpe -0.668, 승률 29.4%, PF 1.05, 수익종목 44.3%
- 🏆 **최적 조합 (중앙 Sharpe 기준)**: `thr=0.15 / hold=20d / stop=5% / tp=15%`
  → 185종목·1,812거래, 평균 Sharpe +1.182(**중앙 +0.596**), 승률 28.7%, PF 1.28, **수익종목 55.7%**
  → 기준선 대비 중앙 Sharpe -0.165 → +0.596, 수익 종목 44.3% → 55.7%
  → 상위 6개 조합이 모두 `stop=5%` · `thr=0.15~0.20` 구간에 몰려 있어 최적점이 톱니형이 아님(안정적)
- 🔴 **발견·수정한 방법론 결함 2건**
  1. `_sharpe()`가 **무캡**이어서 거래 2건 등 표본이 적을 때 값이 폭발(평균 Sharpe +75로 왜곡) → `_METRIC_CAP` 클램프(다른 지표와 일관)
  2. 스윕 순위를 **평균 Sharpe**로 매기면 소수 극단값에 휘둘림 → **중앙 Sharpe 우선**(평균·거래 수 보조)으로 변경
- 검증: 신규 테스트 `tests/unit/test_backtest_sweep.py` **9개** + 백테스터 캡 테스트 → 전체 **1216 passed**, py 190개·BOM 0·SyntaxError 0
- 남은 것(후속):
  - `hold=20d`가 여전히 격자 경계 → 더 긴 보유 탐색 여지(단, 거래 수 감소로 통계력 저하)
  - 폴드가 겹치는 Walk-Forward 1회 실행이라 **진짜 OOS(아웃오브샘플) 검증은 아님** → 별도 기간 분리 검증 필요
  - 최적 파라미터를 실제 시스템(`signal_pipeline` 임계값)에 반영하는 작업은 미착수

### 🔬 P2-9 진짜 OOS(기간 분리) 검증 — **결론: 과최적화 확인, 프로덕션 반영 보류** (2026-09-30)
- 신규 도구: `validation/backtest_sweep.py`에 `slice_series()` / `run_oos_check()` / `--oos-split` 추가
  - IS에서 48조합 튜닝 → 상위 5개를 **파라미터 선택에 쓰이지 않은 기간**에서 재평가
- 🔴 **방법론 결함 추가 발견·수정 (3번째)**: 짧은 구간에서는 폴드당 거래가 1~2건이라 **폴드별 Sharpe가 0.0으로 퇴화**
  → `AggregatedResult.pooled_sharpe`(전 폴드 거래 풀링) 신설, 스윕은 pooled 기준으로 평가하도록 변경
- **검증 결과 1** (IS 2024-10-01~2025-09-30 → OOS 2025-10-01~2026-09-30)
  | 파라미터 | IS 중앙 Sharpe | OOS 중앙 Sharpe | 변화 |
  |---|---|---|---|
  | thr=0.15 / 5d / 5% / 15% (IS 1위) | +2.158 | **-0.192** | **-2.349** |
  | thr=0.15 / 5d / 8% / 10% | +1.984 | -0.051 | -2.035 |
  | thr=0.20 / 20d / 5% / 15% | +1.830 | -0.000 | -1.830 |
  - IS 1위 조합: 승률 39.9% → 30.0%, 수익종목 57.3% → **43.8%**
- **검증 결과 2** (IS 2024-10-01~2025-03-31 → OOS 2025-04-01~2026-09-30)
  - IS 1위: thr=0.20/5d/8%/10% → IS 중앙 0.000, **OOS 중앙 -0.396**, 수익종목 43.8%
- **판정: P2-8의 "최적 파라미터"는 과최적화이며 OOS에서 성과가 유지되지 않는다.**
  → **실제 시스템(`signal_pipeline`) 반영을 보류**한다(반영 시 손실 위험).
- 부수 관찰: Profit Factor가 IS 79~142, OOS 23~55로 비정상적으로 크다 → 손실 분모가 작을 때 PF가 폭주하므로 **PF 단독 순위 사용 금지**
- 검증: 전체 **1219 passed**, py 190개·BOM 0·SyntaxError 0
- 후속 제안:
  1. 데이터 기간 확대(2년 → 5년) + 롤링 다중 분할 OOS로 재검증
  2. 신호 규칙 자체(앙상블 임계값/청산) 튜닝보다 **전략 로직 개선**이 우선일 수 있음
  3. 반영하더라도 **관찰 모드(알림 전용)** 로 제한하고 실거래 파라미터로 쓰지 않기

### B등급 정리 결과 (이번 세션)
- `_archive/orphans/` 이동(의존성 0건): `monitor/calibration_tracker.py`, `analytics/shadow_logger.py`, `analytics/calibration_analyzer.py`
- 유지 결정(사유 명시):
  - `core/circuit_breaker.py` = 정본(실사용: news crawler, daily_monitor) / `risk/circuit_breaker.py` = 테스트 2종 참조 → 보류
  - `monitor/daily_monitor.py`, `monitor/shadow_logger.py` = 레거시 `main.py`(롤백)가 import → 보존
  - `orchestrator/event_bus·feature_store·pipeline_manager`, `observability/anomaly_detector·explainer_v2·model_drift_detector·root_cause_analyzer·trace_propagation` = 각각 unit 테스트가 참조 → 아카이브 시 테스트 붕괴 → 배선 계획 수립 필요
- 검증: `python -c "import app.bootstrap"` → **IMPORT_OK** (부트스트랩 import 그래프 정상)

### 주의사항
- **CONTEXT.md/ROADMAP.md(구버전)는 스테일이었음** — 코드 기준으로 판단할 것
- 피드백 문서(`C:\Users\hdw38\Desktop\달콩\피드백\*.txt`)는 v8.0 기준이라 그대로 결함으로 가정 금지
- 레거시 진입점 `main.py`, `scanner_main.py`는 롤백용으로 보존
- 모든 신규/수정 파일은 UTF-8 (BOM 없음)

---

## 기준 상태 (Ground Truth)
- 리포: https://github.com/djrtn3836-ship-it/-.git
- 브랜치: `genspark_ai_developer` (origin 동기화, HEAD `9c0b0a0`)
- Python: 3.12.9 / 추적 .py: 175개
- 운영 모드: Phase 1 Shadow (실시간 감시 + Telegram 알림, 자동매매 X)
