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

### 🔬 P2-10 전략 고도화 조사 — 5년 데이터 · 롤링 OOS · 진입 필터 (2026-09-30)
- **데이터 확대**: 5년 OHLCV 백필 **230,297행**(190종목, 2021-09-30~2026-09-30)
- **도구 추가**:
  - `run_rolling_oos()` — 확장(anchored) IS → 다음 구간 OOS를 3회 반복 검증(단일 분할의 우연 제거)
  - 진입 필터 4종 (`compute_filters`): `none` / `ma200`(종가>SMA200) / `atr_calm`(ATR%≤6%) / `ma200_atr`
  - `SweepRow`·`evaluate_combo`·`run_sweep`에 `filter_name` 반영, TickerSeries에 필터 캐시
  - CLI: `--rolling-oos N`, `--filters`
- **실험 1** (필터 3 × 임계값 3 × 청산 12 = 108조합, 3분할, 20.4초)
  | # | IS 끝 | OOS 끝 | 튜닝 OOS 중앙 | 기준선 OOS 중앙 | 과최적화 |
  |---|---|---|---|---|---|
  | 1 | 2022-12-27 | 2024-03-26 | +0.000 | **+0.915** | ⚠️ 예 |
  | 2 | 2024-03-26 | 2025-07-01 | +2.996 | +2.314 | 아니오 |
  | 3 | 2025-07-01 | 2026-09-30 | -1.895 | -1.323 | ⚠️ 예 |
  - 튜닝 OOS 평균 **+0.367** < 기준선 OOS 평균 **+0.636** → **튜닝이 오히려 해로움(2/3 구간 과최적화)**
- **실험 2** (장기 보유 20/40/60일 가설, 3분할, 11.6초)
  - 튜닝 OOS 평균 **+0.861** > 기준선 +0.636 → **개선은 있음**
  - 그러나 **분할별 중앙값은 여전히 2/3에서 기준선 열위**, split2(강세장) +4.235에 편중 → 표본 편향
- 🔴 **결론: 현 신호 앙상블에는 견고한 OOS 엣지가 없다.** 임계값·필터·보유기간 튜닝으로 해결되지 않으므로 **프로덕션 파라미터를 변경하지 않는다.**
- 다음 방향(미착수): ① 횡단면 모멘텀(유니버스 상대강도 랭킹) ② 시장지수(KOSPI) 레짐 필터 ③ 팩터 기반 전략 재설계

### ✅ P2-1 알림 누락 검증기 실측 — 결함 수정 (2026-09-30)
- 🔴 **발견**: `analytics/alert_verifier.py` v1.0은 `logs/telegram.log`에서 문자열 `"SIGNAL_ENTRY"`를 세는 방식이었는데,
  **코드/로그 어디에도 그 문자열이 기록되지 않아(전 로그 0건)** 항상 `0 vs 0` → **"✅ 모든 신호 전송됨"이라는 거짓 보고**를 생성
  (누락 종목 목록도 전체 신호를 나열하는 버그)
- ✅ **수정 v2.0**:
  1. `TelegramSender.send()` 성공 시 `logs/alerts_audit.jsonl`에 감사 레코드(kind/ticker/trace_id/date) **1줄 기록**
  2. 검증기는 DB 당일 결정과 **감사 로그를 실제 대조**(`compare_decisions_and_alerts`)
  3. **감사 로그가 없으면 "검증 불가"를 정직하게 보고**(거짓 OK 금지), 누락 종목/일치율 산출
  4. 모듈 임포트 시 싱글턴 생성 제거(부작용 제거)
- 검증: 신규 테스트 `tests/unit/test_alert_verifier.py` **7개** + 감사 기록 실동작 확인(`_write_audit` JSONL) → 전체 **1228 passed**

### ✅ P4-1 통합 테스트 실행기 + CI (2026-09-30)
- 신규 `tests/run_all.py` — 단일 명령 실행기(`--unit`/`--all`/`--quick`), 수집 오류 시 원인 노출, 종료코드 규약
- 신규 `.github/workflows/ci.yml` — push/PR 시 (1) BOM·구문 검사, (2) `python tests/run_all.py`
- 검증: `python tests/run_all.py` → 1235 passed, `--unit` → 1231 passed

### ✅ P1-4 동적 유니버스 파이프라인 도입 (2026-09-30)
- 문제: `data/krx_universe.csv`가 없어 매 부팅 하드코딩 238종목으로 폴백(+CRITICAL 경고), 신규상장/시총변동 미반영
- 신규 `scheduler/universe_fetcher.py` — 네이버 금융 **모바일 JSON API** 수집기
  - `finance.naver.com`은 SPA 전환으로 HTML 파싱 불가(실측 확인) → `m.stock.naver.com/api/stocks/marketValue/{KOSPI|KOSDAQ}` 사용
  - ETF/ETN 제외(`stockEndType=='stock'`), 6자리 코드 검증, 페이지 종료는 **필터 전 원시 개수** 기준
  - CLI: `python -m scheduler.universe_fetcher --pages 3 --out data/krx_universe.csv`
- 🔴 **함께 발견·수정한 버그 2건**
  1. **provider가 CSV의 종목코드를 int로 추론해 선행 0 소실**(005930→5930) → 유효 종목이 515→226으로 축소 → `pd.read_csv(..., dtype=str)` 로 수정
  2. CSV가 시장별 정렬이라 구독 한도(195)에서 KOSPI 편중 → `_interleave_markets()` 라운드로빈 교차 정렬 추가
- **결과**: 실데이터 **515종목**(KOSPI 217/KOSDAQ 298) 수집, provider `source=csv`(폴백 경고 해소)
  - 상위 195종목 시장 구성: **KOSPI 98 / KOSDAQ 97** (기존엔 KOSPI 편중)
- 검증: 신규 테스트 `tests/unit/test_universe_fetcher.py` **7개** → 전체 **1235 passed**

### 🎯 P2-11 횡단면 모멘텀(상대강도) 전략 신설 — 유의미한 OOS 엣지 발견 (2026-09-30)
- 배경: 기존 앙상블은 OOS 엣지 없음(P2-10) → **수익 원천이 다른 횡단면 전략**을 신설
- 신규 `validation/momentum_backtest.py`
  - 매 리밸런싱: trailing `lookback`일 수익률로 전 종목 랭킹 → 상위 `top_k` 동일가중 매수 → `hold_days` 보유
  - 지표: 연환산 Sharpe / CAGR / MDD / 승률(기간 기준), `sweep_momentum`, OOS 분리(`--oos-split`)
  - CLI: `python -m validation.momentum_backtest --limit 190 --start ... --end ... [--sweep|--oos-split]`
- **IS(2021-10~2024-09) → OOS(2024-10~2026-09) 결과**
  | 파라미터 | IS Sharpe | **OOS Sharpe** | OOS CAGR | OOS MDD |
  |---|---|---|---|---|
  | lb=60 k=20 hold=40 (IS 1위) | +0.90 | **+1.91** | +86.4% | 7.4% |
  | lb=120 k=20 hold=10 | +0.79 | **+1.78** | +77.4% | 33.7% |
  | lb=120 k=20 hold=20 | +0.77 | **+1.50** | +85.0% | 35.2% |
  | lb=120 k=5 hold=40 | +0.87 | +1.12 | +123.5% | 48.5% |
  - 상위 5개 **모두 OOS Sharpe 양수**(+1.12~+1.91) — 기존 앙상블(OOS -0.19)과 대조적
- **고정 파라미터(lb=120/k=20/hold=20) 연도별 강건성** (튜닝 없이 4개 구간 개별 적용)
  | 구간 | 총수익 | CAGR | Sharpe | MDD | 승률 |
  |---|---|---|---|---|---|
  | 2022-10~2023-09 | +34.6% | +86.7% | +2.86 | 0.0% | 100% |
  | 2023-10~2024-09 | +10.7% | +23.8% | +1.31 | 2.1% | 50% |
  | 2024-10~2025-09 | +38.8% | +99.1% | +4.57 | 0.5% | 83% |
  | 2025-10~2026-09 | +18.6% | +43.0% | +0.88 | 29.6% | 50% |
  - **4/4 구간 플러스** → 단일 기간 우연이 아닐 가능성
- **거래비용 반영 재검증** (`MomentumConfig.cost_pct`, CLI `--cost`)
  - 고정 파라미터(lb=120/k=20/hold=20), 왕복 0.3% 차감 시 **4/4 연도 구간 플러스 유지**
    | 구간 | 총수익 | CAGR | Sharpe | MDD |
    |---|---|---|---|---|
    | 2022-10~2023-09 | +32.3% | +80.1% | +2.70 | 0.0% |
    | 2023-10~2024-09 | +8.8% | +19.3% | +1.10 | 2.4% |
    | 2024-10~2025-09 | +36.5% | +92.1% | +4.33 | 0.8% |
    | 2025-10~2026-09 | +16.5% | +37.8% | +0.82 | 30.1% |
  - 5년 전체(54회 리밸런싱): 비용 0% 총수익 +229.4%/Sharpe +1.11 → 0.3% +180.9%/+0.98 → 0.6% +139.5%/+0.85
  - **결론: 실무 수준 거래비용을 감안해도 모멘텀 엣지가 유지된다**(생존편향 한계는 별개)
- ⚠️ **주의(반드시 함께 볼 것)**
  1. **생존편향**: 유니버스가 "현재" 상위 515종목 → 상장폐지/하락 종목이 제외되어 **수익률이 과대평가**될 수 있음(가장 큰 한계)
  2. 구간당 리밸런싱이 6회로 적어 Sharpe 신뢰구간이 넓음(연환산 값 과대)
  3. 거래비용·슬리피지·세금 미반영
- 조치: **수치만으로 프로덕션 신호를 바꾸지 않는다.** 관찰 모드 확장 여부는 별도 판단 필요
- 검증: 신규 테스트 `tests/unit/test_momentum_backtest.py` **12개**

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

## 2026-10-01 — P8~P10 (감사 기반 신규 로드맵)
- 감사: TODO/FIXME 0건, 빈 함수는 추상메서드뿐 → **고아 모듈 7건 + 무테스트 핵심 16건**이 실제 부채
- P8: 고아 모듈 6건 처리(상관행렬/섀도우/캘리브레이션/트레이스/도메인모델/secure_config)
- P9: 안전·판단·알림·수집·관측 계층 테스트 약 80개 신설
- P10: 대시보드 10섹션, 임계 제안(자동적용 금지), 주간 PDF 검증 섹션, README
- 수정 버그 5건(구문 오류 커밋 위험, 예외 핸들러 기동 실패, 가짜 상관, input() 정지 위험, async 계약)
- 스케줄러 16잡 / 테스트 1513 passed / ruff F 0

## 2026-10-04 — P12-5: 테스트→운영 데이터 오염 차단 (실측으로 발견)
- 🔴 발견: 테스트가 운영 로그에 직접 기록 → `logs/calibration_predictions.jsonl` 160건,
  `logs/shadow_records.jsonl` 177건, `logs/alerts_audit.jsonl` 287건이 **전부 테스트 픽스처**(005930/MOCK)였다.
  → P12-4 태그 승률 등 모든 분석 데이터가 무효 상태였음.
- 조치: 기록 경로를 환경변수로 격리(`CALIBRATION_LOG_DIR`/`SHADOW_RECORDS_PATH`/`ALERTS_AUDIT_PATH`/
  `READINESS_STATE_PATH`/`BLACKBOX_DIR`/`TRACE_DIR`/`LOG_DIR`) + `tests/conftest.py` 최상단에서 강제 지정.
  카오스 테스트의 DB 복제본도 임시 폴더로 이동.
- 결과: 전체 테스트(1536) 실행 후 `logs/` 파일 **0개**, JSONL/DB 오염 0건.
- 정리: 오염 데이터 삭제, 개발 로그 87개(24MB)는 `_archive/dev_logs_20261004/`로 아카이브.
- 유지: `momentum_paper` 20건(실제 시세 기반 픽, 오염 아님).

## 2026-10-06 — 실시간 경로 첫 실전 검증: 틱 전멸 2건 (435631c, 782791d)
- 🔴 장중 메시지 232만 건 전부 폐기(decisions 0) — 최초 발견.
  ① 키움 WS REAL은 `{"values":{"10":"+18630",...},"type":"0B"}`(숫자 키) 형식인데 변환 코드 부재 → `_normalize_ws_values()` 신설
     (`+`/`-`는 값 부호가 아니라 **전일 대비 방향** → 가격 필드 `abs()` 필수)
  ② APScheduler 스레드에서 `asyncio.run()` → 메인 루프 aiohttp 세션 파손(196크래시) → `run_coroutine_threadsafe`
- 재검증: WS_RECV 1,145건 100% 파싱 성공.

## 2026-10-07 — 실전 2일차: 분석 전멸·경합·스팸 수정 (a56040d, 9697529, ca421fb, b2c68d8, f0170c0)
- 🔴 `await` 누락(deep_analyzer 787) → coroutine 오류 36,965건
- 🔴 dict 변조 크래시(kiwoom 재구독 루프) 6건
- 🔴 사용자가 레거시 `scanner_main.py`를 함께 실행 → 키움 연결 경합으로 오전 데이터 두절
  → 기본 차단(`SCANNER_ALLOW_LEGACY=1` 필요) + PID ctypes 가드 + `run_app.bat`
- 🔴 Supervisor 텔레그램 도배(30초마다): 임계 1024MB 하드코딩 → **시스템 RAM 80%** + 30분 쿨다운
- 🔴 HybridDecider 컨텍스트 규격 불일치(dict vs DecisionContext) → 밤새 33,534건 실패
- CI: await 누락 전역 탐지 + `--check-only` 일원화. 테스트 1536 → 1564.

## 2026-10-08 — 실전 3일차 종일 운영: 자원 폭발 실측
- 앱 정상 가동(WS 28,395 / ANALYZE 28,390, 오류 175건)했으나 **decisions 0건**.
- 🔴 원인①: 앱이 `.env SQLITE_DB_PATH=data/genspark.db`(OHLCV 176행, 005930 0행)를 사용
  → 120일 이력 부재 → 전략 전멸 → **285만 건 전부 HOLD**.
- 🔴 원인②: 틱마다 심층 분석 → DB +1.5GB, 로그 +2.2GB(calibration 1.2GB/shadow 818MB), 메모리 7.3GB.

## 2026-10-09 — 휴장일(한글날): 재연결·로그 폭주 3건 (Session 95, b098798)
- 🔴 데이터흐름 감시 창이 "09:00~15:20" 시간만 보고 **거래일 검사 누락** → 휴장일에도 3분마다
  강제 재연결 + ERROR → 하루 23MB. → `_in_data_flow_window()`(거래일 한정)
- shadow_mode 일치 로그 틱당 기록(26,268줄/7MB) → 불일치만 + 30분 요약
- anomaly_detector 경고 관측당 기록(17,862줄/5.2MB) → 30분 쿨다운(report_history는 유지)

## 2026-10-09~10 — DB 전환·스로틀·오프라인 종단검증
- ① `.env` DB → `data/decisions.db`(23.3만행, 최신 2026-10-08 복구) + 최근 일봉 백필(195/195)
- ② `bootstrap._should_analyze()`: 종목당 300초 1회 / 급변(±2%) 즉시 → 부하 약 1/200
- ③ 정리: `genspark.db` 1.5GB + 폭주 로그 2.2GB → `_archive/junk_20261009/`(삭제 아님)
- ✅ **오프라인 종단 검증**: 실제 분석기 실행 → **000660 SIGNAL_ENTRY**(conf 0.667, ATR 76,692),
  035420 HOLD(score 0.427) — 알림 대상은 `SIGNAL_ENTRY/BUY/SELL`(HOLD 제외)
- ✅ **GitHub = 개발폴더 = 실행폴더 100% 일치**(267파일 전수 대조), 백업 ZIP 11.2MB 재생성
- ⚠️ mypy가 Windows 정책으로 DLL 차단 → 해당 테스트는 환경 감지 skip(CI 전용)
- 테스트 1574 passed / ruff F 0 / BOM·syntax·await 0

### 다음 (월 2026-10-12 거래일)
- `run_app.bat` 기동 → 장중 실제 신호·텔레그램 알림·자원 소모 검증 (최초의 정상 종단 확인 시도)

## 2026-10-10 — 자동학습 로직 정밀 진단 + 끊긴 고리 2개 연결 (Session 97, e3a13ae)
조사(서브에이전트 정밀 맵 + 직접 검증) 결과: **기록→결과계산은 닫혀 있으나 "결과→행동 변경" 고리가 여러 곳에서 끊겨 있었다.**

| 컴포넌트 | 결과 계산 | 행동 환류 | 조치 |
| :--- | :--- | :--- | :--- |
| StrategyBandit | ✅ Beta(α,β) 갱신 | ❌ **전략 선택 미반영** | ✅ weight_sink 연결 |
| FeedbackLearner | ✅ XGB 학습 | ⚠️ XGB만 사용, 팩터 가중치는 죽은 값 | 🔲 결정 필요 |
| HyperparameterTuner | ✅ Optuna | ⚠️ 자동 적용되나 **인메모리(재시작 시 소실)** | 🔲 영속화 제안 |
| Calibration | ✅ N=5일 settle | ❌ ECE 소비자 없음(기록만) | 🔲 리포트 연동 제안 |
| ModelDrift | ❌ record_outcome 프로덕션 호출 0 | ❌ | 🔲 배선 제안 |
| DataReadiness | ✅ 게이트 판정 | ❌ 알림만(차단 없음) | 🔲 유지 |

수정 2건:
- ① **Bandit 학습 → 전략 선택 고리 연결**: 브리지 docstring이 `DeepAnalyzer.update_strategy_weights()`를 호출한다고 주장했으나 **그 메서드는 존재하지 않았다**(호출 0건).
  `SignalPipeline.apply_learned_weights()` 신설(균등 대비 상대비 × 기준가중치, 0.5x~2.0x 클램프, 누적 드리프트 없음) + `BanditFeedbackBridge(weight_sink=...)` + bootstrap 배선.
- ② **대시보드 관측 섹션 무음 공백**: `html_dashboard.py:97`이 없는 함수 `get_ops_snapshot`을 import → ImportError 무음 삼킴. 모듈 캐시 + 함수 추가.

남은 제안(승인 필요): 드리프트 배선 · 튜닝 결과 영속화(+자동적용 정책 재검토) · calibration_executor의 `hash()` 가짜 슬리피지 → 실가격 · FeedbackLearner 팩터 가중치 사용 여부.
[검증] pytest 1590 passed(+11) / ruff F 0 / 레포=실행폴더 272파일 100% 일치

## 2026-10-10 — Session 98: 학습 고리 4건 연결 + 전수 스캔 (e75d052)
사용자 승인("추천대로 전부 진행")에 따라 미연결 4건 전부 적용:

| # | 항목 | 이전 상태 | 조치 |
| :--- | :--- | :--- | :--- |
| ① | 드리프트 감지 | `record_outcome` 프로덕션 호출 0건(영구 꺼짐) | FeedbackLearner가 결과를 OpsMonitor에 공급, bootstrap 2경로 주입 |
| ② | 튜닝 결과 | 인메모리 → 재기동 시 소실 | `save/load_tuning_state`(env `TUNING_STATE_PATH`) + 기동 시 복원 |
| ③ | calibration | `hash(ticker)` 가짜 슬리피지 | DB 실가격(결정가→다음 거래일 시가, ±100bp) + RealisticExecutionSimulator |
| ④ | 팩터 가중치 | DB 로드만 하고 미사용(죽은 값) | `_learned_factor_multipliers()` — 평균 1.0 정규화 + 0.5x~2.0x 클램프 |

부수 수정: 시뮬레이터가 '현재 시각'으로 장중 판정 → 16:00 잡에서 슬리피지 0이 되던 문제(결정 시각 전달), 시뮬레이터 클래스명(`RealisticExecutionSimulator`) 정정.

**전수 스캔 결과(2026-10-10): 프로젝트 .py 135개**
- TODO/FIXME/XXX/HACK: **0건** · `raise NotImplementedError`: **0건**
- 미구현 주석 2건: PostgreSQL 메서드 격차(P6-3, 의도적 보류) · `order_executor` Phase 2(모의투자)
- 참조 0회 모듈 14개 = 전부 의도적(진입점 `app/main.py`·레거시 `main.py`/`scanner_main.py`, CLI `ohlcv_backfill`, dev 스크립트 7개, `position.py`(Phase 2 예약), `secure_config.py`)

[검증] pytest 1603 passed(+13) / ruff F 0 / BOM0 syntax0 unawaited0 / 레포=실행폴더 100% 일치

## 2026-10-10 — Session 99: 모의매매 측정 시스템 (사용자 최대 요구사항 대응)
사용자 최대 요구사항("하루 1번 80% 확률로 5~10% 수익")을 실측으로 검증:
**5년 230,251건 → 무작위 상승확률 44.3%, 5~10% 상승 3.54%(매일 평균 8.7종목 존재)**.
80%는 무작위 대비 17배 예측력이 필요 → 수학적으로 달성 불가(80%/+7%면 연 76,000배). 정직하게 통보함.

대신 "정확도를 측정하는" 장치를 구축(사용자가 선택한 3방향: 모의매매·보고서 고도화·비가격 팩터 연구):
- **① `analytics/paper_trader.py`**: 매매신호(SIGNAL_ENTRY/BUY/SELL) 기록 → 1/3/5/10 거래일 뒤
  **실현 종가만으로** 채점. 승률 + **Wilson 95% 신뢰구간** + 무작위 기준선(44.3%) 대비 우위 판정.
  조건 태그별 승률 분해(비가격 팩터 연구 기반). 소표본(<30) 경고 문구 필수.
- **② bootstrap**: 신호 기록 배선 + 평일 16:10 `paper_settle` 잡(17번째) → 채점 + 텔레그램 리포트.
- **③ 대시보드 11번 섹션**: 모의매매 성과(신뢰구간·기준선 표시).
- 🔴 **실측으로 발견한 버그**: `get_ohlcv`는 "최근 N일"을 반환하므로 진입일이 창 밖이면
  다른 날 가격으로 채점된다 → 창 밖 신호는 확정 보류로 수정.
- 실데이터 검증(무작위 250건 → 904건 채점): 5일 승률 56.0%(95% 49~62%), 평균 +2.37%,
  5~10% 상승 15.0%. ※ 최근 구간이 강세장이라 기준선(44.3%)보다 높게 나옴 — 기간 의존성 확인.

[검증] pytest 1617 passed(+10) / ruff F 0 / BOM0 syntax0 unawaited0 / 레포=실행폴더 일치

## 2026-10-10 — Session 100: 판단력 정밀화 + 전체 다듬기
스스로 결정 로직을 진단해 취약점 2곳을 찾아 수정:

- **① 근거 기반 판단 보류** (`_has_sufficient_evidence`): 이력(지표)이나 ATR이 없으면
  전략을 돌리지 않고 HOLD로 보류. 이전에는 지표가 비어 있어도 판정이 나갈 수 있었다
  (실측 "ATR 계산 실패" 하루 175건). 근거 없는 신호를 원천 차단.
- **② 알림 반복 억제** (`_should_send_alert`): 같은 종목·행동은 `ALERT_COOLDOWN_SEC`(기본 30분)
  내 재발송하지 않음. 5분 분석 스로틀만으로는 같은 종목 알림이 반복됐다.
  (모의매매 기록은 계속 — 알림 정책이 아니라 **신호 자체**를 측정하기 위함)
- **③ 정직성 보강**: 모의매매 리포트/대시보드에 "거래비용(수수료·세금·슬리피지) 미반영" 명시.
- **④ 문서 다듬기**: `.env.example`에 신규 운영 변수 12종 문서화, README/CONTEXT/ROADMAP
  잡 개수 16→17 갱신, README에 모의매매 측정 설명 추가.

[검증] pytest 1623 passed(+6) / ruff F 0 / BOM0 syntax0 unawaited0

### 남은 개선 후보 (미적용 — 데이터/검증 필요)
1. 모의매매에 거래비용 반영한 순수익 지표 병기(현재는 총수익만)
2. 알림에 신뢰도·과거 승률 표시(측정 데이터 축적 후)
3. 비가격 팩터(감성/공시)를 **판단에 반영** — 현재는 태그 기록만. 측정으로 효과 확인 후
4. 다중 시간대 확인(단기·중기 모멘텀 일치 요구), 시장 국면(Bear/Panic) 매수 차단 강화

## 2026-10-10 — Session 102: 알림 파이프라인 신뢰성 (약점 3건)
알림은 이 시스템의 핵심 산출물인데, 전달 경로가 취약했다:

- **① 워커 블로킹**: `await sender.send()`가 네트워크 장애 시 send_raw 재시도
  (4회 × 30초 ≈ 3분)로 **워커 2개를 동시에 정지**시켰다 — 알림이 가장 필요한
  순간에 분석 파이프라인이 멈춘다. → 전송을 백그라운드 큐로 분리(워커는 즉시 반환).
- **② 영구 유실**: 전송 실패 알림은 로그만 남기고 사라졌다.
  → `logs/pending_alerts.jsonl`에 보관, 다음 성공 전송 직후 소량씩 재시도,
  다음 기동 시 복구.
- **③ 셧다운 유실**: 종료 시 큐에 남은 알림이 사라질 수 있었다.
  → 최대 8초 드레인 + 미소진분 파일 보관.
- 구현: `TelegramSender.format()`(순수 포맷) / `send_formatted()`(전송+감사) 분리,
  bootstrap에 `_alert_worker`/`_enqueue_alert`/`_retry_alert_backlog` 추가.
  감사 로그는 **실제 전송 성공 시에만** 기록(기존과 동일 보장).

[검증] pytest 1639 passed(+15) / ruff F 0 / 신규 test_alert_pipeline.py(15)
[환경변수] ALERT_QUEUE_MAX=1000 · ALERT_BACKLOG_PATH=logs/pending_alerts.jsonl · ALERT_BACKLOG_MAX=200

## 2026-10-10 — Session 103: DB 자동 백업 + 정산 순서 교정 + 관리 알림 큐
약점 연속 보완(사용자 무승인 진행 허용):

- **① DB 자동 백업** (`core/db_backup.py` 신설): decisions.db는 학습 데이터의 전부인데
  자동 백업이 없었다(수동 ZIP뿐). sqlite3 온라인 백업 API로 **일관성 스냅샷** →
  `PRAGMA integrity_check` 통과분만 보관 → 7개 초과 시 오래된 것 삭제.
  잡 `db_backup`(평일 07:30, 18번째) + **기동 시 24시간 경과하면 즉시 1회**.
  실패 시 텔레그램 알림(성공은 조용히 — 로그만).
- **② 정산 순서 교정(실버그)**: 정산 잡들이 일봉 수집(16:30)보다 먼저 실행되어
  (calibration_settle 16:00, paper_settle 16:10) 필요한 종가가 DB에 없어
  **항상 하루 늦게 채점**되던 문제 → 17:15 / 17:05로 이동(일봉 16:30 이후).
- **③ 관리 알림 큐 경유**: 시작/종료 알림도 백그라운드 큐로(RAW 모드, 감사 로그 미기록 —
  alert_verifier 대조 오염 방지). 네트워크 장애 시 기동·종료 지연 제거.

[검증] pytest 1657 passed(+18) / ruff F 0 / 실DB 백업 실증(28.3MB, 무결성 ok, 233,611행)
[신규 테스트] test_db_backup.py(10: 백업·회전·손상·무결성·정산순서) · test_alert_pipeline.py RAW(3)
[부수] tests/run_all.py 검사기: sqlite3.connect 동기 호출 오탐 제거

## 2026-10-10 — Session 104: 거래일 달력 실버그 + 모의매매 만료 정책
연속 약점 보완(사용자 무승인 진행 허용):

- **① 거래일 달력 실버그(2027년 전면 오판)**: `holidays.country_holidays("KR")`는
  지연 확장이라 가져온 시점에 **2026년만 확장**돼 있었다 → `_get_holidays(2027+)`가
  빈 집합 + constants도 2026만 → **2027년 모든 공휴일을 거래일로 오판**
  (실측: `is_trading_day("2027-01-01") → True`, 삼일절도 True).
  영향: 2027-01-01부터 휴장일에 앱이 가동(재연결·분석·알림 낭비).
  → 연도별 인스턴스(`years=year`) 조회 + constants **합집합**으로 수정.
  [검증] 2027-01-01/03-01/12-25 전부 False, 2027-01-04 True, 2028-03-01 False.
- **② 모의매매 미확정 무한 누적**: 진입일이 OHLCV 창(60일) 밖인 기록은 영원히
  채점 불가인데 파일에 계속 남아 무한 성장(매 채점마다 전체 읽기/쓰기).
  → `PAPER_PENDING_MAX_DAYS`(기본 45일) 초과분 자동 정리 + 로그.

[검증] pytest 1672 passed(+15) / ruff F 0 / check-only 0건
[신규 테스트] test_holiday_utils.py(10) · test_paper_trader 만료(2)

## 2026-10-10 — Session 105: 디스크 감시 + DB 유지보수 + 대시보드 관측
약점 연속 보완:

- **① 디스크 여유 감시** (`core/disk_guard.py`): 디스크가 가득 차면 SQLite 쓰기가
  **조용히** 실패한다(결정 저장 실패 = 학습 데이터 손실). 과거 로그 폭주 3.9GB 사고의
  재발 방지. 잡 `disk_check`(매시 :07) + 기동 시 검사, 3GB/5% 미만이면 텔레그램 알림
  (6시간 쿨다운).
- **② DB 정기 유지보수** (`core/db_maintenance.py`): `PRAGMA optimize`(가벼움) +
  테이블별 행수·크기·무결성 로그. 잡 `db_maintenance`(일 03:40). VACUUM은 기본 off
  (수동 `python -m core.db_maintenance --vacuum`).
- **③ 대시보드 관측**: 1번 섹션에 **디스크 여유**·**미전송 알림 보관 건수** 배지 추가
  (알림 큐가 막힌 채 발견되지 않던 관측 사각지대 해소).

[검증] pytest 1686 passed(+14) / ruff F 0 / 대시보드 수집 실측(디스크 785.6GB 여유)
[신규 테스트] test_disk_and_maintenance.py(14)
[스케줄러] 18 → 20잡

## 2026-10-10 — Session 106: 수급(외국인/기관) 수집 파이프라인 — 최대 정보 공백 해소
질문("정보수집 고도화가 다 됐나?")에 대한 감사에서 최대 공백을 발견·착수:

- **발견**: 수급 API는 커넥터에 있었지만 **주간 PDF·온디맨드 명령에서만** 쓰였고
  판단에는 0 상수(`regime_manager`의 `foreigner_futures=0.0`)로 들어갔다.
  P5/P6(가격 파생 팩터 무엣지) 이후 남은 유일한 미개척 정보원이 수급이었다.
- **구현**: `collector/supply_demand_collector.py`(방어적 파싱 — 구조 미상이면
  **추정하지 않고 건너뜀**) + `supply_demand` 테이블 + 잡 `supply_demand`(평일 16:35)
  + 비가격 팩터 태그(`flow_f_buy/sell`, `flow_i_buy/sell`, `flow_both_buy`=쌍끌이).
- **원칙 준수**: 수집·기록만 한다. 점수/행동 변경 없음 — 태그별 승률이 검증되면
  그때 반영(검증 없는 보정 금지). 모의매매 태그 승률로 자연 검증된다.
- [검증] pytest 1704 passed(+18) / ruff F 0 / 신규 test_supply_demand.py(18)
- [부수] PG 격차 문서 8→11건 갱신(parity 테스트 연동)
- ⚠️ 실 API 응답 구조는 월요일 16:35 첫 수집에서 확인 필요(미확인 시 무음 skip)

## 2026-10-10 — Session 107: 재무(펀더멘털) 판단 반영 + 실버그 2건
1순위(재무 반영) 진행 — 실검증이 죽어 있던 경로를 드러냈다:

- **🔴 실버그 ① DART corpCode.xml은 ZIP으로 내려온다**: 다운로드 코드가 XML만
  기대해(Content-Type: application/x-msdownload) corp_code 매핑이 **영원히 실패**
  → 재무·공시 경로 전체가 빈 값(그동안 아무도 몰랐음 — 로그 경고만). ZIP 해제 추가.
- **🔴 실버그 ② `sj_div != "CFS"` 필터 오류**: sj_div는 재무제표 종류(BS/IS),
  연결/별도는 fs_div(CFS/OFS). 이 필터가 전 항목을 버려 재무가 항상 {}였다.
- **🔴 실버그 ③ 결측 0.0 위장 → 가짜 리스크**: 당기순이익 누락을 0으로 채워
  정상 기업이 "적자(fund_risky)"로 오판됐다(삼성전자 실측). 결측은 결측대로
  통과시키도록 수정.
- **구현**: `collector/fundamentals_provider.py`(DART + 7일 TTL 캐시 + 분류 규칙),
  비가격 팩터에 재무 태그(`fund_solid/fund_ok/fund_risky`)·수치(ROE/부채/영업이익률)
  기록. Signal(frozen)에 태그/팩터 부착 → **워커→결정→모의매매 태그 승률**까지 연결
  (이전에는 태그가 어디에도 전달되지 않아 승률 측정이 영원히 빈 상태였다).
- **실검증**: 삼성전자 ROE 10.4%/부채 29.9%/영업이익률 13.1% → fund_solid,
  SK하이닉스 ROE 35.6% → fund_solid (양성 확인)
- 행동 원칙: 점수는 여전히 바꾸지 않는다 — 태그 기록 → 모의매매 승률 검증 후 반영.

[검증] pytest 1714 passed(+10) / ruff F 0 / test_fundamentals.py(10)

## 2026-10-10 — Session 108: 종합 브리핑 리포트 (모든 요소 한 뷰)
"섹션별로 흩어진 정보"를 하나의 읽기 쉬운 브리핑으로 조합:

- `report/briefing.py` — 아침(08:35)/저녁(17:25) 2종:
  - 아침: 시장(지수·환율·VIX·나스닥/니케이/DXY)·국면·어제 신호 요약·모의매매 진행·시스템
  - 저녁: 오늘 신호 요약·모의매매 승률(기준선 대비)·태그 승률(n≥5)·시스템(디스크·백업·미전송)
- 설계: 각 섹션 독립 try/except(부분 브리핑 허용) · 3600자 상한 · RAW 관리 알림
  (감사 로그 미기록) · 태그 승률은 dict/list 두 형태 허용
- 잡 2개 추가(21 → **23잡**)
[검증] pytest 1723 passed(+9) / test_briefing.py(9) / 실DB 렌더 확인

## 2026-10-12 — Session 110: 종목명 표시 + 장외 신호 알림 차단 (사용자 피드백)
세션 109(중복 인스턴스) 이후 실전 신호 첫 관측 → 사용자 피드백 2건 반영:

- **① 신호가 숫자 코드로만 표시** → `core/stock_names.py`(KRX CSV + mtime 캐시) 신설,
  `TelegramSender.format()`에서 모든 알림에 종목명 주입 → "삼성전자(005930)".
  실증: 089030→테크윙, 095610→테스, 006440→… (오늘 신호 11종 전부 해석됨)
- **② 장외 신호 알림**: 시간외단일가(~18:00) 틱으로 만든 신호가 16:30·16:33에 알림
  (체결 불가 가격 → 오해) → `_alert_hours_ok()` 게이트: 거래일 09:00~15:29만 알림.
  기록·모의매매는 유지(측정 데이터 보존).
- **③ SafetyGuard 유지 로그**: 조건 변화 또는 30분마다만 DEBUG 출력(잔여 도배 제거)
- 발견(미수정): 안전가드 차단 중에도 "매수 시그널" 알림이 나가 문맥 혼란 →
  차기 후보: 차단 중 신호에 참고용 주석 표시
[검증] pytest 1737 passed(+9) / test_stock_names.py(9) / 실이름 매핑 실증

## 2026-10-13 — Session 111: 실전 첫날 검증 → 일봉 수집 실버그 3건 (긴급)
사용자 "로그 검증" 요청으로 오늘 하루 전수 점검 중 **일봉 자동 수집이 전면 오작동**임을 실측:

- **🔴 사고**: Kiwoom ka10060 응답의 `cur_prc`는 부호 접두("-251000")를 붙인다
  → 하락 종목 전부 close<=0 → "비거래일"로 오판(오늘 195종목 중 172종목 누락).
  게다가 이 응답엔 open/high/low/vol 자체가 없어 저장돼도 전부 0(가짜 OHLC).
  '어제' 고정 라벨이라 월요일 수집이 일요일 라벨로 저장되는 오라벨까지(10-11 행 50건).
- **v2 수정**: 검증된 yfinance 경로(OHLCVBackfiller)를 1차로, Kiwoom은 수정 폴백
  (부호 제거·응답 날짜 신뢰·0값 거부). 거래일 대량 실패 시 ERROR+CollectorStatus(무음 금지).
- **데이터 복구**: 오라벨/0값 123행 정리 → yfinance 1개월 백필(195종목·3,271행) →
  yfinance가 비운 10-12(월)까지 Kiwoom으로 보충 → **10-06~10-13 전부 195행 완전 복구**
- **첫 모의매매 채점 완주**: 194건 확정 (전부 10-12 진입 → 10-13 폭락일 청산)
  1일 승률 24.7%·평균 -2.92% — **폭락일 단일 표본이라 전략 판단 보류**(체계 완주가 성과)
[검증] pytest 신규 test_daily_collector_v2.py(8) / 전 종목 5일 연속 데이터 실측

## 2026-10-13 — Session 112: 정밀 감사 — 수급 API 전면 오류 + DART 운영경로 미수정 (긴급)
"다른 오류 정밀분석" 요청 → 전 로그 스캔으로 두 개의 치명 결함을 실측으로 확정:

- **🔴 수급이 전부 0.0(가짜 데이터)**: ka10008/ka10009는 해당 URI에서 지원하지 않는
  API ID였다(return_code=1 실측). 커넥터가 구조 불일치 시 net_buy=0으로 위장 →
  DB에 390행 전부 0값. → 일봉 차트(ka10060) 레코드에 frgnr_invsr/orgn 실측 확인
  → **일봉을 수급 소스로 전환**(부호 유지, 0 위장 금지, 봉 날짜 일치 시만 저장).
  재수집 실값: 삼성전자 외국인 -55만/-135만주 순매도(폭락일과 정합) · 389행 확보.
- **🔴 DART 실운영 경로 미수정**: S107 수정이 data/dart_connector.py에만 적용되고
  실운영(nonprice 재무)은 infrastructure/dart/client.py를 씀 — 이쪽은 ZIP 미처리 +
  부분 캐시(005930 없음) + sj_div 오필터 + 결측 0위장 + 연도 하드코딩(2024~22).
  → ZIP 처리·fs_div·결측 통과·동적 FY 이식 → 캐시 3,934개 재구축.
  실검증: 삼성전자 ROE 10.4%/부채 29.9% · SK하이닉스 ROE 35.6% (운영 모듈로 확인)
- 감사에서 정상 확인: SafetyGuard/drift/anomaly 알림(설계 동작), 중복 가드 16회 차단,
  텔레그램 타임아웃 재시도 정상. 로그 정체 일부(atr_service 등)는 배선 재점검 후보.
[검증] pytest 통과 / 실API 응답 실측(수급·DART·캐시 재구축)

## 2026-10-14 — Session 113: 장외 소음 원천 차단 (워커 게이트)
오늘 로그 검증에서 발견: 장 마감 후에도 이벤트가 계속 생성됨
(실측 16:32~18:31 EVENT_LIFECYCLE_ADVICE 53건+ · SL_TRAIL 6건 · SIGNAL_ENTRY 2건).

- **원인**: 시간외단일가(~18:00) 틱이 워커에서 그대로 분석됨 — 알림은 장외 게이트로
  차단돼 있었지만 **분석 자체는 계속** → 포지션 재평가 이벤트·기록·모의매매 오염.
- **수정**: `_in_signal_window()`(거래일 09:00~15:35, 15:35는 동시호가 유예) 신설 →
  워커가 정규장 외 틱을 **원천 드롭**(task_done 후 continue, 누적 카운트 로그).
- **정리**: 장외 생성 미확정 모의매매 2건(036540·036830) 제거(339→337).
- **실증**: 재시작 직후 장외 틱 200건 즉시 드롭 · 재시작 후 신규 장외 decisions 0건.
[검증] pytest 1752 passed(+7) / test_offhours_gate.py(7)

## 2026-10-15 — Session 114: 재부팅 공백 사고 → 자가 복구 + 부팅 작업 스크립트
사용자 제보: 윈도우 업데이트 재부팅(00:33) 후 **로그인 전이라 앱이 18:44에야 시작**
 → 하루(거래일) 감시 공백: 일봉·수급·정산 전부 놓침(로그 공백으로 확인).
- 원인: 예약작업이 Interactive 타입(로그온 필요) + S4U 등록은 관리자 권한 필요(0x80070005).
- **긴급 복구(수동)**: 일봉 151 · 수급 151(실값) · 정산 58건 확정.
- **재발 방지 ①(코드)**: `_catchup_daily_jobs()` — 기동 시 16:40+ 일봉 부재/16:45+
  수급/17:10+ 정산 미완료를 감지해 백그라운드 자가 복구(멱등, 정산은 1일 1회 가드).
- **재발 방지 ②(스크립트)**: `scripts/register_boot_task.ps1` — 관리자 1회 실행 시
  S4U(로그온 불필요) 부팅 자동 시작 작업 등록. **사용자 실행 대기**.
[검증] pytest 신규 test_startup_catchup.py(2) / 실기동 확인 예정

## 2026-10-16 — Session 115: 신호 유형 라벨(단타/스윙) + 알림 선별 필터
사용자 질문: "단타/스윙/장기 구분 로직이 없냐 + 신호와 매매의 상관관계는?"
→ 사실 확인: 구분 로직 없음(모든 신호가 동일 포맷, 라이프사이클 2시간=단타 전제).
   전략 3종(추세/반전/돌파)은 성격이 다르나 출력에 반영 안 됨.

- **신호 유형 잠정 라벨**: Trend/Breakout 우세→"스윙(추정)", Reversal 우세→"단타(추정)"
  (검증 전 라벨 — 기록·표시 전용, 지평 승률로 추후 확정. '장기'는 전략 부재로 없음)
- **알림 선별 필터(데이터 근거)**: ① 신뢰도 하한(SIGNAL_MIN_CONFIDENCE, 잠정 0.60 —
  상위50% 34.5% vs 하위50% 21.6% 실측) ② 안전가드 차단 중 진입 알림 보류
  (차단 중 신호는 시장보다도 나쁨 실측). **기록·모의매매는 100% 유지**(측정 불변).
- paper_trader에 trade_type 기록(유형별 승률 측정 대비) + 알림에 "🧭 신호 유형" 표시.
[검증] pytest 1773 passed / test_signal_typing_filters.py(10)

## 2026-10-16 — Session 116: 유형별 라이프사이클 (사용자 승인 설계)
사용자 지적: "스윙인데 같은 종목이 오늘도 내일도 신호가 오면 그게 스윙이냐?
정밀 진입 → 매도 타이밍 정확화가 맞는 방향 아닌가" → **사실 확인: 반복 신호 실재**
(신호 종목 81개 중 42개가 2일 이상 반복 — 스윙 정의 위배).

- **단타(추정)**: 당일 15:15 청산 기한 (최소 보유 20분) — 다음날 이월 금지
- **스윙(추정)**: 진입 후 **5거래일** 뒤 15:15 청산 기한
- **재진입 금지**: 청산 후 **2거래일** — 같은 종목 반복 신호 원천 차단
- **매도 타이밍**: ① 목표가 도달 즉시(기존) ② 기한 만료 시 스위퍼가 청산 알림+기록
  ③ 아침 브리핑에 "⏳ 오늘 청산 예정" 섹션
- 전부 설정값(trading_swing_hold_days / trading_reentry_block_days) — 검증 후 조정
[검증] pytest 1784 passed(+11) / test_lifecycle_by_type.py(11) / 실배포·재시작

## 2026-10-16 — Session 118: 기대값 운영 지표 (Top 트레이더 방법론 이식)
사용자 질문: "상위 1% 트레이더 기법을 복제한다면?" → 정직 판정: 수익률 복제는
불가(생존편향+인프라 게임)하나, **방법론(기대값·리스크 퍼스트)은 이식 가능**.
- /성과 리포트에 승/패 평균과 **손익분기 승률** 병기:
  "3일: 승률 22.8% ... ↳ 승 +X%/패 -Y% → 손익분기 승률 Z%"
- 의미: 승률이 낮아도 손익분기 미만이면 기대값 플러스 — 승률 단독 판정 금지 원칙의 계량화
[검증] pytest 1796 passed(+3) / test_expectancy_metrics.py(3) / 실배포·재시작

## 2026-10-16 — Session 119: 수급 5년 백필 (사용자 승인, 주말 무인 실행)
냉철 평가에서 최대 병목 확인: supply_demand 이력이 5일치뿐(10-12~10-16) →
수급 팩터(쌍끌이·연속성·다이버전스) 연구 불가. **과거 dt 조회 실증**(2025-10-16
삼성전자 외국인 +561,198주 정상 반환) → 5년 백필(약 254k 요청, ~28시간).
- scheduler/supply_backfill.py: 주중 역순, 봉 날짜 일치 시만 저장(휴장 오라벨 방지),
  요청별 체크포인트(재부팅 재개·멱등), 연속실패 재로그인→중단, 장중 자동 일시정지
- run_backfill_hidden.vbs + run_backfill_auto.bat (창 숨김, 콘솔 로그 보존)
[검증] pytest 1803 passed(+7) / test_supply_backfill.py(7) / 실발사 확인

## 2026-10-16 — Session 120: 병행 과제 2건 (수급 백필과 동시 진행)
냉철 평가의 "다음 과제"를 백필과 병행 실행(사용자 질문: 동시 진행 가능? → 가능).
- ① **진입 시간대별 측정 장치**: `_hour_bucket`(09:00~09:04 개장 몰림 분리) +
  /성과 리포트 "진입 시간대별(5일, n≥10)" 섹션 — 개장 몰림이 실제로 나쁜지
  데이터로 판정. 기존 신호(ts 보유) 소급 분석 가능(마이그레이션 불필요).
- ② **지수 시계열 DB화**: scheduler/index_backfill.py — KOSPI/KOSDAQ/KOSPI200을
  ohlcv 테이블(티커 ^KS11 등)에 저장 → 유니버스 동일가중(생존편향) 대신 정확한
  벤치마크. 1행 시리즈 저장 생략 가드(^KS200 사고 재발 방지).
[검증] pytest 1811 passed(+9) / test_hour_analysis.py(6) test_index_backfill.py(3)

## 2026-10-17 — Session 121: 유니버스 자동 갱신 실버그 수정 + 주말 잡 보정
발견: scheduler/universe_fetcher.py가 `RuntimeMode.is_safe()`(property를 메서드 호출)
→ TypeError → 토 09:00 유니버스 자동 갱신이 **P6-1(10-01) 이후 한 번도 성공한 적 없음**
(예외 흡수로 "실패(기존 CSV 유지)" 로그만 반복). CSV가 10-09 이후 방치(515종목).
수정: get_runtime_mode().is_safe 인스턴스 접근 + 회귀 테스트 2건.
실행: 수동 갱신 성공 — **515 → 659종목** (KOSPI 262 / KOSDAQ 397).
주말 보정(앱은 비거래일 기동 불가 — 설계): universe_refresh·data_readiness 수동 실행.
- data_readiness: **ml_ready=True 도달** (decisions 42,672 / outcomes 25,386) → 알림 발송
- db_maintenance(일 03:40)는 수급 백필 쓰기 중 VACUUM 위험 → 생략(다음 주 정상)
구조 이슈(다음 승인 안건): 재부팅으로 금요일 인스턴스가 죽으면 주말 잡(토/일) 전부
미실행 — '주말 유지보수 모드' 기동 옵션 검토 필요. (금요일부터 살아있던 인스턴스는
주말 잡을 정상 실행했음 — 내 재시작 킬로 이번 주만 보정 발생, 정직 기록)
[검증] pytest 1813 passed(+2) / test_universe_refresh_fix.py(2)

### Session 121b (2026-10-17 00:5x) — 테스트 계약 정정 + CSV 동기화
- `test_universe_stability.py::TestScheduledRefresh` 3건 실패 원인: 테스트가 **버그에
  맞춰 작성**되어 있었다(RuntimeMode.is_safe를 staticmethod로 주입 = 구버그 호출 계약).
  → get_runtime_mode() 페이크 주입 방식으로 정정(수정된 계약 검증). 18 passed.
- 유니버스 CSV(659종목) 실행폴더→레포 동기화(3자 대조 0).
[검증] pytest 1813 passed 전체 재통과

## 2026-10-17 — Session 122: 주말 유지보수 모드 (사용자 승인)
구조 공백 해소: 재부팅으로 금요일 인스턴스가 죽으면 주말 잡(토 유니버스/일 DB·
데이터점검)이 전부 미실행 — 비거래일 기동 시 **1회성 유지보수 러너**를 실행하고 종료.
- `scheduler/weekend_maintenance.py`: 토=유니버스 갱신(CSV 6일+ 낡으면 일요일도),
  일·평일공휴일=DB 유지보수(PRAGMA optimize, VACUUM 없음)+데이터 준비도, 안전모드 스킵,
  잡 실패 격리, 전부 주입 가능(오프라인 테스트)
- `app/main.py`: 비거래일 분기에 run_maintenance 호출(기본 ON, WEEKEND_MAINTENANCE=0으로 해제).
  인스턴스 락 획득 후 실행 — 살아있는 인스턴스가 있으면 기존 인스턴스가 담당(중복 없음).
- 설계 결정: 주말 내내 상주하지 않음(월요일 시장 감시가 주말 환경에 묶이는 위험 회피) —
  월요일은 평소처럼 새 인스턴스.
[검증] pytest 1819 passed(+6) / 실전: 토요일 python app/main.py 실행 → 유지보수 후 정상 종료

## 2026-10-17 — Session 123: 백필 중단 사고 수정(no_data 오분류) + 재출발
사고: 09:41 백필이 '오류 30연속' 보호로 중단 — 실체는 **062040(데이터 시작
2024-07-29)의 상장 전 날짜가 연속 no_data**(시맨틱 응답)였는데 오류로 오판.
사용자 상태 질문으로 발견(3시간 공백). 수정: no_data=스킵 처리(재시도 없음) +
회귀 테스트(test_no_data_is_skip_not_error). 재출발 12:59(PID 21252) — pre-listing
구간 고속 스킵 정상 확인. 오류 14건(10만 건 중 0.01%·순간 장애)은 완료 후
ohlcv 거래일 대조로 gap 보충 예정.
[검증] pytest 1820 passed(+1) / 재출발 후 체크포인트 전진 확인

## 2026-10-17 — Session 124: 백필 무인 자동 진행 체계 (사용자 요청)
요청: "내가 요청 안 해도 시간을 정해서 자동으로 진행" — 중단 방치(3시간) 재발 방지.
- `scheduler/backfill_supervisor.py` **감시자**: 소켓 락(47654), 중단 시 10분 후
  재시도(기동당 3회), 완료 시 텔레그램 알림 1회 + **예약작업 자동 해제**, 3회 실패
  시 경고 알림(30분 뒤 예약작업이 재기동 — 무한 복구)
- 예약작업 `stock_analyzer_backfill`(30분 주기, wscript 숨김) 등록 — 다음 실행 확인
- `run_backfill_auto.bat` → 감시자 경유로 변경(단일 실행 경로)
- 🔴 테스트가 실제 버그 적발: Windows에서 SO_REUSEADDR를 켜면 중복 바인드가 성공해
  락이 무력화됨(실측) → 제거. 회귀 테스트 8건.
[검증] pytest 1828 passed(+8) / 감시자 전환 후 락 Listen·백필 재개 확인

## 2026-10-19 — Session 125: 로그 전수 점검 + 브리핑 누락 실버그 수정
**백필 최종 결과**: 10-18 02:35 완료 — 저장 127,387건(2차)+1차분, DB 228,730행,
**오류 0**, 감시자가 텔레그램 완료 알림 발송 + 예약작업 자동 삭제(성공).
**커버리지 검증: 195종목 범위 ohlcv 거래일 대비 수급 갭 0건** (앞선 68%는 타 종목 포함
스코프 오류였음 — 정정). 예약작업의 실제 발화는 미검증(발화 로그 0건 — 삭제되어 사후
확인 불가, 다음 등록 시 발화 테스트 필요 — 체인 자체는 VBS 재기동으로 검증).

**실버그 수정(로그 점검 발견)**: 08:40 기동일엔 08:35 아침 브리핑이 항상 누락
(정시 크론 경과) → 브리핑 캐치업(시간창 08:35~11:00/17:25~21:00 + 멱등 플래그) 추가.
실전 검증: 재시작 10:49 → 10:51:49 "⏪ morning 브리핑 지연 발송" + 텔레그램 전송 ✓.

**관찰 과제(다음)**: SafetyGuard kospi_drop(-11.22%)이 금요일에 고정돼 있다가
재시작으로 -6.32% 갱신 — 장수 인스턴스에서 지표 갱신 공백. 갱신 주기 조사 필요.
(kospi_drop=KOSPI 5일 수익률, 임계 -3%, 현재도 차단 유지 — 폭락 후유증은 실제.)
[검증] pytest 1831 passed(+3) / 실전: 브리핑 지연 발송·감시 체인 재기동 확인

## 2026-10-19 — Session 126: 로그 전수 점검 마무리 — 백업 도구 재구축
**ZIP 백업 도구 재구축(중요 발견 2건)**:
- 기존 mkzip.ps1이 %TEMP% 정리로 소실 → scripts/에 레포 편입(mkzip.py + ps1 래퍼).
- 🔴 .NET CreateEntryFromFile은 FileShare.Read만 허용 → **쓰기 중인 로그/DB가 ZIP에서
  누락**(실측 73건 실패 — decisions.db 누락 위험까지 확인). PowerShell 함수 경유 시
  NullReference 재현(원인 미규명) → **파이썬 zipfile로 재구현**(공유 읽기 실증).
- DB는 sqlite3 online backup **일관 스냅샷**으로 포함(WAL 병합, -wal/-shm 제외).
- 최종: entries=507 failed=0 · DB 스냅샷 86MB 포함 검증 ✅ (사용: `python scripts/mkzip.py`)
**백필/감시 최종**: 갭 0건 · 감시 체인 재기동 검증(완료 감지→자동 해제→종료) · 예약작업
발화는 미검증(다음 등록 시 테스트).
**다음 조사 과제**: SafetyGuard kospi_drop 갱신 공백(금 -11.22% 고정 → 재시작 후
-6.32% — 장수 인스턴스에서 지표 리프레시 안 됨. 원인/주기 조사 필요).
[검증] 3자 대조 0 · 커밋·푸시 완료

## 2026-10-19 — Session 127: 장중 거시 갱신 잡 — SafetyGuard 신선도 실버그 수정
**조사 결과**: macro_update 잡은 하루 1회(08:00)뿐 + SafetyGuard는 get_cached_macro()
(캐시)를 읽음 → **장중 내내 기동 시점 값으로 위기 판단**(금요일 -11.22%가 월요일
10:41까지 고정, 재시작으로만 갱신됨 — 로그 실측).
**수정**: `_run_macro_intraday` 잡 추가 — 평일 09~15시 :05/:35 (30분 주기),
거래일 가드 + fetch_macro_data(force=True). job_count 23→24.
**실증**: 재시작 11:35 기동 수집(-6.59 갱신) → **12:05:00~03 첫 장중 발화 확인**
(나스닥·SOX·KTB·달러인덱스 등 전체 재수집 로그). SafetyGuard가 이제 '오늘'의
시장을 본다.
참고: 이 시점 글로벌 동반 급락 진행 중(나스닥 -4.8%, SOX -9.0%, 니케이 -4.8%)
— 위기 감지가 정확히 중요한 국면.
[검증] pytest 1833 passed(+2) / 12:05 발화 실측

## 2026-10-19 — Session 128: 수급 팩터 소급 검증 — 정직한 "무효" 판정
**도구**: `validation/flow_factor_test.py` — 사건 연구(시장 베타 제거, 초과수익 =
개별 전방수익 − 같은 날 유니버스 평균) + 연도별 일관성 + **중복 제거(켜지는 날만)**
**핵심 교훈**: 1차 검정에서 C_매집다이버 t=+3.94(6/6 연도 플러스)로 유일하게
'유의'가 나왔으나, 연속 지속일 중복(겹치는 전방창) 제거 시 **t=+0.5로 붕괴** —
교과서적 overlapping-window 착시를 실측. 도구 기본값 dedupe=True + 회귀 테스트 10건.
**최종 판정(195종목·5년·중복제거)**:
- A 쌍끌이 t=+1.63 / B 외국인연속3 t=+0.48 / C 매집다이버 t=+1.30 → **무효**
- D 외국인5일순매수 t=+2.63(5일)/+2.25(20일), 초과 +0.16~+0.30%p — 경계 통과하나
  **왕복비용(~0.3%) 이하 = 실전성 없음** + 다중검정(8조합) 감안 시 유의 미약
→ **flow 태그는 측정용 유지가 옳다** (원칙대로 점수 미반영이 정답이었음).
**데이터 보강**: 누락 137종목 일봉 5년 백필(156,884행, 실패 0) → ohlcv 전 종목 완비.
[검증] pytest 1843 passed(+10) / 실측 표본 195종목 22만+ 이벤트

## 2026-10-19 — Session 129: 재무(퀄리티) 팩터 검증 — 무효 판정 (접근가능 팩터군 소진)
**도구**: validation/fundamentals_factor_test.py — DART 사업보고서 FY2021~2024 수집
(648건, 재개 가능 캐시) → 이듬해 4/1 진입·12개월 창 → 연도별 횡단면 상·하위 20%.
**품질 장치**: 기업행위 아티팩트 필터(일간 ±31% 초과 = 분할/감자, yfinance 비조정)
— 구현 중 경계 버그(창 밖 점프와 비교)를 테스트가 적발·수정.
**결과(아티팩트 제외 후)**:
- ROE: 상위초과 평균 -11.3%p (플러스 2/4년) → 무효
- 부채비율: -1.0%p (3/4년이나 평균 음수 — FY2024 -30%p 지배) → 무효
- 영업이익률: -19.8%p (1/4년) → 무효
FY2024(-64/+97%p)는 **2025~26 초강세장(삼성전자 +222%)의 퀄리티-정크 로테이션
실데이터**(아티팩트 아님 — 2종목만 제외됨). 강세장에서 퀄리티가 정크에 진 것.
**결론**: 가격·수급·재무 — 접근 가능한 팩터군 전부 무엣지 실증 완료. 시스템 가치 =
감시·리스크·측정 (기존 결론의 최종 재확인).
[검증] pytest 1856 passed(+13)

## 2026-10-19 — Session 130: 운영 잔여 3건 — KOSPI200 프록시 · 예약작업 발화 실증 · 시장 폭
- **KOSPI200 대체**: yfinance ^KS200 1행 문제 → 069500.KS(ETF) 프록시 폴백 체인.
  실측 1,200행 저장(2021-10-19~) — 지수 3종 완비.
- **예약작업 발화 실증**: 임시 1분 작업 생성→100초 내 마커 확인→삭제. 메커니즘 정상.
  (백필 감시작업 미발화는 /TR 인용부호 문제였을 가능성 — 다음 등록 시 단순 명령 테스트)
- **시장 폭 신규**: analytics/market_breadth.py — 자체 195종목 일봉으로 MA20/60 상회
  비율·상승/하락·20일 신고/신저 → 브리핑 시장 섹션에 📐 라인(표시용, 점수 불변).
  실측: MA20↑ 20% · MA60↑ 30% · 상승/하락 137/54 · 신고 11/신저 28 — 급락 국면
  (폭 붕괴)과 당일 반등을 동시에 정확히 전달.
[검증] pytest 1869 passed(+13) / 발화 실증 / 프록시 1,200행

## 2026-10-19 — Session 131: 시장 폭 레짐 가치 검증 — "극단 패닉 = 단기 반등" 첫 통과
**도구**: validation/breadth_timing_test.py — 5년(1,213일) 일별 폭 역사(그 시점까지
데이터만 사용 — 룩어헤드 없음, 테스트로 고정) + KOSPI 전방 5/20일 + 이벤트 첫날
중복제거(S128 교훈). 폭 역사 JSON 저장(data/breadth_history.json).
**결과**:
- **L2 (MA20 상회 ≤10%) — 판정 통과**: 12회, 5일 초과 +4.51%p, 적중 83%,
  t=+2.45, 연도 3/4 플러스(22:-0.3 24:+6.9 25:+6.4 26:+10.7)
  → 극단 패닉 뒤 단기 반등 경향. L1(≤20%)·H1(≥80%)은 무효.
- 주의: n=12 소표본 · 6조합 다중검정 · 이벤트가 24~26년에 몰림(21/23년 없음) →
  판정은 "경계 통과"가 정확. 알림/점수 반영은 보류, 브리핑 맥락으로만.
- **현재 폭 20% = 역사 백분위 8%** (패닉 구간 상단, L2 트리거까지 10%p)
- 버그 수정: '현재 폭'을 정렬 최댓값으로 출력하던 것 → 최신 날짜값(테스트 고정).
[검증] run_all 1864 passed(+6)

## 2026-10-19 — Session 132: 폭 패닉 반등 강건성 검증 + 브리핑 백분위/패닉 맥락
**강건성 (validation/breadth_timing_test.py --robust 신설)**
- 임계 스윕(KOSPI 전방 5일, 첫날 중복제거): ≤5% +5.80(t=5.29) · ≤8% +2.21 ·
  ≤10% +4.51(t=2.45) · ≤12% +4.38(t=2.53) · ≤15% **+5.06(t=3.46)** ·
  ≤20% +0.10 · ≤25% +0.35 → **10~15% 플레이트(나이프엣지 아님), 20% 이상 무효(경계 ~15%)**
- 순열검정 2,000회(무작위 같은 수의 날): ≤10/12/15 모두 p≤0.001 — 다중검정 감안해도 강함
- KOSDAQ(^KQ11) 교차: 5일 +5.98%p·적중 92%·t=2.41 — 두 시장 일치(공통 메커니즘)
- **2022년 제외: n=8, +1.41%p, t=0.97** → 효과는 24~26 V자 국면 주도, 22 약세장(이벤트 6회
  평균 -0.3%) 무반등 — **국면 의존적**. 정확한 결론: "경계 통과 + 국면 조건부"
**운영 반영(표시 전용 — 판단/점수/주문 불변)**
- analytics/market_breadth.py: load_breadth_history/breadth_percentile 신설,
  format_breadth에 '역사 백분위 N%' 병기, PANIC_THRESHOLD=15/PANIC_NOTE 상수
- report/briefing.py: 📐 폭 라인에 백분위 + 폭 ≤15%일 때 "⚠ 패닉권" 맥락 1줄
- 오늘 실측: 폭 20%(백분위 8%) — 패닉 문턱 15% 바로 위
- 폭 역사 자동 갱신은 수동(도구 --save-history) 유지 — 백로그 후보
[검증] run_all 1875 passed(+11)

## 2026-10-19 — Session 133: 폭 기능 완성 — 주말 자동 갱신 + 대시보드 표시
**① 계산 공용화(구조)**: S/load_universe/load_index/breadth_at/_idx_le를
validation 도구에서 analytics/market_breadth.py(프로덕션)로 이관 — 검증도구는
재수출로 호환 유지(기존 테스트 무변경 통과). save_history/refresh_history 신설
(refresh_history = 전 종목 재계산, 100일 미만이면 오류로 낡은 파일 방지).
**② 주말 유지보수에 폭 역사 갱신**: run_maintenance에 breadth_history 스텝
(매 유지보수 실행, to_thread, 실패 격리 — _Recorder 테스트 2건 추가).
검증: 실행폴더에서 실측 1,213일 갱신 확인(2026-10-19 = 19.9%).
**③ 대시보드 매크로 카드에 시장 폭**: MA20↑ + 역사 백분위 + 패닉 뱃지 + 맥락.
실측 렌더 확인(n=196 · 19.9% · 백분위 7.7% · 패닉 아님).
→ 이제 브리핑(하루 2회)과 대시보드(17:30) 모두 폭을 보여주고,
   폭 역사 파일은 주말 유지보수 때 자동 갱신되어 백분위가 낡지 않는다.
[검증] run_all 1882 passed(+7) · ruff F 0건(부수: 미사용 os import 1건 정리)

## 2026-10-19 — Session 134: 자동학습 루프 실버그 3건 수정 (캐치업 정산 + 5d 오염)
**발견(실측)**: ML 기반 판별력 검증을 준비하다 데이터에서 실버그 적발 —
① 학습기가 **'어제 하루'만** 처리 → 앱이 하루라도 꺼지면 그날 outcomes **영구 누락**
   (실측: 10-14·10-16 배치 누락, outcomes가 10-12/13에만 존재).
② `return_5d`가 실제로는 1일 값 — ohlcv **최신 종가**를 1d·5d에 같이 사용
   (검증: 기존 outcomes 25,386건 **전부** return_5d == return_1d).
③ 누락일 재처리 경로 없음(재정산·갱신 개념 부재).
**수정** (feedback_learner v7.4.3):
- 최근 10일 내 **미확정 전부** 재정산(결과 없음/5d 미성숙/성숙 전 저장분) — 매일 자동 복구.
- 결정일 고정 봉: 1일차=결정일 다음 봉, 5일차=+5봉 종가. 5d 미성숙은 **NULL 저장**
  → 다음 실행에서 재정산(집계는 None 제외, 전부 미성숙이면 1d 대체).
- db_manager.get_unsettled_decisions 신설(성숙 +7일 규칙) — PG KNOWN_GAPS 등록(패리티 가드).
**실증**: 실행폴더 1회 수동 실행 → 42,672건 재정산(4초) · XGB 재학습(39,248샘플) ·
  텔레그램 보고서 정상(v7.4.3, 기간 라벨, 진짜 5일 평균 -6.44%).
  장중(15:12) 실행분은 10-19 종가 미확정 상태였으므로 삭제 후 17:00 자동 실행으로
  **마감 종가 기준 재정산**되도록 조치(앱 재시작 PID 17900 — 신버전 로드).
[검증] run_all 1892 passed(+10 신규 테스트 tests/unit/test_feedback_catchup.py)

## 2026-10-19 — Session 135: 자체 ML/신뢰도 판별력 검증 도구 신설 (결과 대기)
- validation/ml_discrimination_test.py — 진입 신호(BUY+SIGNAL_ENTRY)의 confidence·
  ml_score·score 5분위 × 초과수익(같은 날·행동 평균 차감 — flow_factor_test 관례)
  + Spearman + 상하위 Welch t + 일자별 일관성 + HOLD(조용함) ml 분위 적중.
  아티팩트 필터(|1d|>70·|5d|>300), ml_score=0.5(ML 미적용) 분리 보고.
- 실측 전제: 기록된 BUY confidence = 0.610~0.705 — **0.60 알림 필터는 실제로 발동 안 함**
  (실질 경계 = 분석기 자체 ≥0.61). ml_score는 0.21~0.86이나 절반(6,153건)은 0.5 기본값.
- S134에서 outcome 정합성 확보 → 17:00 자동 재정산 직후 실행 예약(백그라운드 job).
[검증] run_all 1901 passed(+9)

## 2026-10-19 — Session 136: 상황조건 스캔(①)·매도규칙(②)·구역 정식화(③)·신호 성적표(⑤)
**① validation/condition_sweep_test.py** — 16개 상황조건 × KOSPI 전방 1/5/20일(순열검정·반기·연도).
결과: **생존 후보 2개 — '폭≤15'(n=18, +5.05%p, 78%, p=0.001, 4/5년)과 '폭≤15&하락중'
(n=17, +5.21%p, 76%, p=0.001)**, 경계 '폭≤10'(반기 -0.3/+9.3 → 강등). 신저비율·신저
다이버전스·외국인 스트릭·변동성 국면 무효. 관찰: 'KOSPI5일≥+5%' t=2.47·p=0.002이나 연도
3/6로 탈락(후속 관찰). 부수 발견: 패닉 이벤트 18건 중 17건이 하락 진행 중 → '하락중=차단'
로직이 역사적 기회를 놓침(구역 로직 교정 근거). 결과 저장 data/condition_sweep.json.
**② validation/exit_rule_test.py** — 진입(거래량×3+|2%|) 고정, 청산 7규칙 비교(비용 0.3%).
전체 8,389건: 고정20일 +1.50(t=6.7) vs 목표7/손절3 -0.52(승률 28%) → **타이트 손절·익절이
기대값 훼손**. 패닉권 이벤트 285건: 고정20일 +6.99%·승률 69%·t=6.8·전년도 + → "패닉 매수
후엔 20일 보유, 타이트 규칙 금지" 실데이터 근거(군집·β 한계 명시).
**③ analytics/market_zones.py** — 진입금지(SafetyGuard -3%)/기회(폭≤15)/관찰/정상.
브리핑 🚦 + 대시보드 뱃지(표시 전용). 스캔 결과에 맞춰 '폭≤15&하락' 차단 로직을 기회로 교정.
**⑤ validation/signal_vs_random_test.py** — 알림신호 vs 같은 날 무작위(순열). 잠정: 1일
-0.43%p(p=0.93) · 5일 -1.45%p(n=26) → **현재까지 무작위와 구분 안 됨**(정직 기록, 주간 재실행).
**④ 공매도(KRX)**: 시도 — 403(봇 차단) → 별도 세션 조사로 보류(기대값 낮음).
[검증] run_all 1930 passed(+29: 신규 테스트 4파일)
