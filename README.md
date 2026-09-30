# Stock Analyzer V10 — 텔레그램 알림 보조 집사

⚠️ **자동매매 시스템이 아닙니다.** 한국 주식 시장(KOSPI/KOSDAQ)을 실시간 감시하며, 매수·매도 신호와
손절매 추천을 텔레그램으로 알려줍니다. 최종 매매 판단과 실행은 항상 사용자 본인이 합니다.

- 🟢 **현재 운영 모드**: Phase 1 Shadow Mode (실시간 감시 + 알림, 자동매매 없음)
- 📄 기준 문서: [`CONTEXT.md`](CONTEXT.md) · [`ROADMAP.md`](ROADMAP.md) · [`DEVELOPMENT_LOG.md`](DEVELOPMENT_LOG.md)

---

## 🚀 주요 기능

- **실시간 감시**: Kiwoom REST/WebSocket으로 시가총액 상위 종목(최대 195개, 키움 한도) 체결 수집
- **동적 유니버스**: 네이버 금융 시가총액 API로 실제 상장 종목 수집(`data/krx_universe.csv`), 시장 교차 정렬
- **멀티 전략 엔진**: 추세(Trend)·역추세(Reversal)·돌파(Breakout) 병렬 실행 + 앙상블
- **머신러닝/감성**: XGBoost 예측 확률, 뉴스 감성 파이프라인, DART 공시 분석
- **리스크 관리**: Monte Carlo VaR, SafetyGuard(위기 시 진입 차단), ATR 기반 트레일링 스탑
- **Telegram**: 신호/이벤트 7종 실시간 알림, 자연어 명령어, **알림 누락 검증**(감사 로그 기반)
- **자가 치유**: Supervisor(30초 감시), 단일 인스턴스 가드, Kiwoom 재시도 상한

---

## 🧭 진입점

| 진입점 | 설명 |
| :--- | :--- |
| `python app/main.py` | ✅ **공식 진입점 (V10 DDD)** → `app/bootstrap.py` |
| `python scanner_main.py` | 🟡 레거시 (v8.0, DeprecationWarning) |
| `python main.py` | 🟠 롤백용 (v5.1.2) |

---

## 🧪 안전모드 (자격증명 없이 검증)

`.env`의 플래그가 **실제로 동작**합니다(`core/runtime_mode.py`).

| 플래그 | 효과 |
| :--- | :--- |
| `TEST_MODE=true` | 외부 연결(키움/거시/뉴스/DART) 없이 부팅, 모의 키움 커넥터 사용 |
| `DRY_RUN=true` | 부작용 차단(텔레그램 실발송 X, 로그만) |
| `MOCK_DATA_ENABLED=true` | 모의 데이터 사용 (TEST_MODE면 자동 포함) |
| `TELEGRAM_ENABLED=false` | 텔레그램 전송/폴링 비활성 |

```bash
# 자격증명 없이 부팅 검증
TEST_MODE=true python app/main.py
```

---

## 🛠️ 도구 (CLI)

### 데이터
```bash
# 과거 일봉(OHLCV) 백필 — yfinance 기반 (예: 유니버스 상위 190종목, 5년)
python -m scheduler.ohlcv_backfill --limit 190 --period 5y

# 동적 유니버스 수집 → data/krx_universe.csv
python -m scheduler.universe_fetcher --pages 3
```

### 백테스트
```bash
# 단일 종목 Walk-Forward (프로덕션 앙상블 vs MA교차 비교)
python -m validation.backtest_runner 005930 2021-10-01 2026-09-30 --compare

# 다종목 일괄 / 파라미터 스윕 / 롤링 OOS
python -m validation.backtest_sweep --limit 190 --start 2021-10-01 --end 2026-09-30 --sweep
python -m validation.backtest_sweep --limit 190 --start 2021-10-01 --end 2026-09-30 --rolling-oos 3

# 횡단면 모멘텀(상대강도) 전략
python -m validation.momentum_backtest --limit 190 --start 2021-10-01 --end 2026-09-30 --sweep
python -m validation.momentum_backtest --limit 190 --start 2021-10-01 --end 2026-09-30 --oos-split 2024-09-30

# 모멘텀 참고 신호 + 모의 추적 — 매 영업일 08:30 스케줄러 등록됨 (주문/포지션 없음)
python -m scheduler.momentum_report

# 생존편향 스트레스 (상폐율 0/2/5/10% × 상폐 시 -60% 가정)
python -m validation.momentum_backtest --limit 190 --start 2021-10-01 --end 2026-09-30 \
    --lookback 120 --top-k 20 --hold 20 --cost 0.003 --stress
```

### 검증
```bash
python tests/run_all.py          # unit + integration
python tests/run_all.py --unit   # unit만
```
CI: `.github/workflows/ci.yml` (BOM·구문 검사 + 테스트)

---

## ⚙️ 설치 및 실행

```bash
python -m venv venv
venv\Scripts\activate            # Windows
pip install -r requirements.txt

cp .env.example .env             # 실제 API 키 입력 (UTF-8, BOM 없음)
python app/main.py
```

**필수 환경변수**: `KIWOOM_APP_KEY`, `KIWOOM_APP_SECRET`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
(구버전 이름 `KIWOOM_API_KEY`/`KIWOOM_SECRET_KEY`도 자동 인식)

---

## 📱 Telegram 명령어

| 입력 | 동작 |
| :--- | :--- |
| `현황` / `오늘 장은?` | 시스템 상태(가동 시간, 구독 종목, 큐, 국면) |
| `신호` / `최근 매수 신호` | 최근 신호 목록 |
| `삼전` / `005930` | 종합 분석 리포트(재무/뉴스/수급/기술/AI) |

---

## 📂 구조

```text
app/            main.py(진입점), bootstrap.py(배선)
domain/         models/, strategies/ (Trend/Reversal/Breakout)
application/    analysis/ (signal_pipeline, hyperparameter_tuner, ...)
infrastructure/ market_data/ (universe_provider, mock_kiwoom), cache/, dart/, database/, news/
observability/  tracer, health_score, anomaly_detector, ...
validation/     backtester, backtest_runner, strategy_backtest, backtest_sweep, momentum_backtest
scheduler/      ohlcv_backfill, universe_fetcher, momentum_report, daily_collector, macro_collector
scanner/ analytics/ risk/ report/ core/ data/ (기존 공존 — Strangler Fig 진행 중)
```

---

## ⚠️ 알려진 한계 (중요)

- **백테스트 전략 앙상블(Trend/Reversal/Breakout)은 롤링 OOS에서 견고한 엣지가 없습니다.**
  (IS Sharpe +2.16 → OOS −0.19, 2개 분할 붕괴) → **프로덕션 파라미터 변경 보류**
- **횡단면 모멘텀은 OOS·거래비용·생존편향 스트레스(상폐율 10%)에서도 플러스**였으나,
  OOS 2년이 강세장이라 베타 기여분이 분리되지 않았습니다
  → **참고 신호(모의)로만 운영**: 주문/포지션 없이 픽을 기록하고 20거래일 뒤 성과를 자동 확정
- 해외거래소/옵션/선물 미지원, 실계좌 연동 없음(Paper Mode only)

---

## 🔧 개발자 정보

- 버전: **V10** (문서 기준일 2026-09-30)
- Python: 3.12.9 (Windows)
- 테스트: `pytest` (asyncio_mode=auto) — 현재 **1252 passed**
- 규칙: 승인 기반 개발 / 기존 기능 보존 / **UTF-8 (BOM 없음)** / 작업 후 `DEVELOPMENT_LOG.md` 갱신
