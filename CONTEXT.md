 CONTEXT.md (v7.6.2 FINAL) - 전문
markdown
# 🔬 프로젝트 완전 상태 저장소 (Full Context) - v7.6.2 FINAL

> 📌 **이 문서의 목적**: 새 대화를 시작할 때, 10분 만에 이 프로젝트의 완전한 상태를 복원하고, 다른 AI에게 즉시 인수인계할 수 있는 **초정밀 컨텍스트**입니다.
> 📅 **최종 업데이트**: 2026-08-19 (수) 14:30 KST
> ✅ **현재 상태**: 
> - v7.6.2 FINAL (Windows/Unix 완전 호환 + Telegram 로그 안정화)
> - 60개 이상 파일 전수 검사 및 11개 통합 테스트 + 16개 단위 테스트 통과
> - **Phase 1 Shadow Mode 운영 중 (알림 전용, 자동매매 없음)**
> - 전체 기반 튼튼화 완료 (설정 중앙화, 예외 처리 표준화, 그레이스풀 셧다운, 로깅 고도화)

---

## 📁 1. 프로젝트 기본 정보

| 항목 | 값 |
| :--- | :--- |
| **프로젝트명** | stock_analyzer_v5.1.2 |
| **버전** | **v7.6.2 FINAL (AI 퀀트 + 멀티 전략 + VaR + Windows 호환)** |
| **Python 버전** | 3.12.9 |
| **운영 모드** | Phase 1 Shadow Mode (실시간 감시 + Telegram 보고서, 자동매매 없음) |
| **실행 명령어** | `python scanner_main.py` |

---

## 📂 2. 전체 파일 구조 (2026-08-19 최종)
stock_analyzer_v5.1.2/
├── config/
│ ├── config.yaml, dart_config.yaml, kiwoom_config.yaml
│ ├── secure_config.py, discovered_keys.json
│ ├── corp_code_cache.json, nlp_model.pkl
│ ├── strategies.yaml, regime_weights.yaml, debug_config.yaml
│ └── risk_config.yaml
│
├── core/
│ ├── logger.py (v7.1) # JSON 로그, 밀리초 포맷 수정
│ ├── config.py (v7.0) # 설정 중앙화 + 검증
│ ├── exceptions.py (v2.0) # 커스텀 예외 정의
│ ├── exception_handler.py # 전역 예외 핸들러
│ ├── scheduler.py, holiday_utils.py, constants.py
│ ├── blackbox_logger.py, debug_tower.py (v2.1)
│ ├── regime_manager.py, natural_language.py (v1.1)
│ ├── sentiment_analyzer.py, font_utils.py
│ └── settings.py
│
├── data/
│ ├── kiwoom_connector.py (v6.1.4) # REG 재시도 강화
│ ├── db_manager.py (v6.0) # 연결 풀링 + 인덱스 최적화
│ ├── dart_connector.py (v5.4.0) # 연도 자동 탐색
│ ├── news_crawler.py (v6.3.0) # 재시도 + Fallback
│ └── stock_universe.py
│
├── scanner/
│ ├── realtime_monitor.py (v5.6.9)
│ └── deep_analyzer.py (v7.6.0) # config 연동 + PortfolioManager
│
├── strategy/ # 🔥 멀티 전략
│ ├── base_strategy.py (v1.2)
│ ├── trend_strategy.py (v1.2)
│ ├── reversal_strategy.py (v1.2)
│ └── breakout_strategy.py (v1.2)
│
├── orchestrator/
│ ├── strategy_router.py (v1.1) # 캐싱 + 자동 재로드
│ ├── portfolio_manager.py (v1.1) # 포트폴리오 VaR 관리
│ ├── event_bus.py, feature_store.py, pipeline_manager.py
│
├── risk/
│ ├── portfolio_var.py # Monte Carlo VaR
│ ├── var_calculator.py # Modified VaR + 리스크 조정
│ └── safety_guard.py
│
├── report/
│ ├── telegram_sender.py (v7.3.0) # 지수 백오프 + 자동 분할
│ ├── telegram_commands.py (v7.3.0) # allowed_updates 필터링
│ ├── daily_report.py (v6.1) # ML/VaR 표시
│ └── weekly_pdf.py (v6.1)
│
├── validation/
│ ├── execution_simulator.py (v3.1) # 시장 충격 튜닝
│ └── backtester.py
│
├── filters/
│ ├── stock_filter.py (v6.0) # regime_weights.yaml 연동
│ ├── macro_filter.py (v6.0)
│ ├── sector_filter.py
│ ├── korean_special_filter.py
│ └── dynamic_weighter.py
│
├── feedback/
│ └── feedback_learner.py (v7.4.0) # ML 모델 저장/로드
│
├── scheduler/
│ ├── macro_collector.py (v2.1) # 이상치 탐지 + FRED Fallback
│ └── daily_collector.py (v1.1)
│
├── collector/
│ └── collector_status.py # 수집기 통합 상태 관리
│
├── analytics/
│ └── calibration_analyzer.py
│
├── tests/
│ ├── unit/ (16개 단위 테스트)
│ ├── (11개 통합 테스트)
│ ├── diagnose_system.py
│ └── scan_all_files.py
│
├── scanner_main.py (v7.6.2) # Windows 시그널 핸들러 + Telegram 로그 조정
├── requirements.txt
├── .env
└── CONTEXT.md (v7.6.2 FINAL)

text

---

## 🧠 3. v7.6.2 핵심 업그레이드 사항

| 항목 | 내용 | 상태 |
| :--- | :--- | :--- |
| **Windows 시그널 핸들러** | `add_signal_handler` → `signal.signal` 조건부 분기 | ✅ 완료 |
| **JsonFormatter 밀리초** | `%f` 대신 `datetime` 직접 계산 | ✅ 완료 |
| **Telegram 로그 조정** | `telegram.ext` 로그 레벨 INFO 상향 | ✅ 완료 |
| **REG 재시도 강화** | 최대 2회 재시도 (1초 간격) | ✅ 완료 |
| **중복 코드 제거** | `register_realtime` 내 중복 로직 정리 | ✅ 완료 |
| **전역 예외 처리** | `asyncio` + `sys.excepthook` 통합 | ✅ 완료 |
| **Graceful Shutdown** | 태스크 추적 + `asyncio.gather` | ✅ 완료 |
| **설정 중앙화** | `config.yaml` 모든 값 통합 | ✅ 완료 |

---

## 🛤️ 4. 향후 고도화 로드맵 (v7.6.2 → v8.0) 및 세부 정교화(Calibration) 작업

### 📌 2분기 (3~6개월) – 실전 검증 및 백테스트 고도화 (P0/P1)

| 우선순위 | 작업 항목 | 세부 정교화(Calibration) 작업 | 적용 파일 | 상태 |
| :--- | :--- | :--- | :--- | :--- |
| **P0** | **① 백테스트 Walk-Forward 검증** | • 2020~2025 데이터로 Rolling Window(2년 Train, 1년 Test) 검증<br>• Survivorship Bias 제거 (상장폐지 종목 데이터 포함)<br>• 거래비용(수수료 0.015% + 세금 0.18%) 및 실측 슬리피지 반영<br>• Sharpe Ratio, MDD, Profit Factor, Calibration ECE 측정 | `validation/backtester.py` (고도화)<br>`validation/walk_forward.py` (신규) | ⏳ 대기 |
| **P0** | **② Paper Trading (모의투자) 연동** | • 키움 모의계좌 REST API 주문 모듈 개발 (`주식주문` TR)<br>• `SIGNAL_ENTRY` 발생 시 실제 모의 주문 전송 (BUY/SELL)<br>• 미체결/부분체결 처리 로직 (잔량 관리, 취소)<br>• 주문 결과 Telegram 실시간 알림 | `execution/order_executor.py` (신규)<br>`execution/order_manager.py` (신규)<br>`report/telegram_sender.py` 확장 | ⏳ 대기 |
| **P1** | **③ 포트폴리오 성과 추적기** | • 일별 포트폴리오 PnL, 승률, Sharpe Ratio, MDD 자동 계산<br>• 각 종목별 기여도(Contribution) 분석<br>• Telegram/PDF에 성과 리포트 추가 (일간/주간) | `analytics/performance_tracker.py` (신규)<br>`report/daily_report.py` (v7.0) | ⏳ 대기 |
| **P1** | **④ VaR 고도화 (포트폴리오 레벨)** | • 단일 종목 VaR → 포트폴리오 상관관계 기반 VaR 확장 (Cholesky 분해)<br>• Monte Carlo 시뮬레이션(10,000회)으로 극단적 손실(CVaR) 추정<br>• VaR 임계값 초과 시 Telegram 경고 자동 발송 | `risk/portfolio_var.py` (신규)<br>`orchestrator/portfolio_manager.py` 확장<br>`config/risk_config.yaml` | ⏳ 대기 |

---

### 📌 3분기 (6~9개월) – 실전 운영 (Live Trading) (P0)

| 우선순위 | 작업 항목 | 세부 정교화(Calibration) 작업 | 적용 파일 | 상태 |
| :--- | :--- | :--- | :--- | :--- |
| **P0** | **⑤ Live Trading 전환** | • 실계좌 연동 (초기 소액 100만원 이하)<br>• 손절/익절 자동 실행 검증 (ATR 기반 TP/SL)<br>• 장애 시 수동 개입 프로토콜 마련 (Telegram 긴급 차단 명령어)<br>• 실계좌 주문 전 2차 확인 로직 (Safety Guard 강화) | `execution/live_executor.py` (신규)<br>`core/safety_guard.py` 강화<br>`report/telegram_commands.py` 확장 | ⏳ 대기 |
| **P1** | **⑥ 시스템 자가 치유 강화** | • 프로세스 다운 시 자동 재시작 (Watchdog 스레드)<br>• Telegram으로 비정상 종료 및 재시작 알림<br>• 메모리 누수 감지 및 자동 GC 트리거 | `scanner_main.py` (Watchdog 추가)<br>`core/memory_monitor.py` (신규) | ⏳ 대기 |

---

### 📌 4분기 (9~12개월) – 지속적 개선 및 확장 (P1/P2)

| 우선순위 | 작업 항목 | 세부 정교화(Calibration) 작업 | 적용 파일 | 상태 |
| :--- | :--- | :--- | :--- | :--- |
| **P1** | **⑦ 하이퍼파라미터 자동 튜닝** | • Bayesian Optimization으로 전략 가중치, ATR 승수, Kelly 분율 최적화<br>• Calibration 리포트(체결률/슬리피지)를 피드백으로 사용<br>• 튜닝 결과를 `config.yaml`에 자동 반영 | `analytics/hyperopt.py` (신규)<br>`core/config.py` 확장 | ⏳ 대기 |
| **P2** | **⑧ 해외 종목 확장 (미국)** | • Yahoo Finance API로 미국 주식(SPY, QQQ, 개별주) 데이터 수집<br>• 미국 장 시간대(22:30~05:00 KST) 스케줄러 추가<br>• 환율 변동을 리스크 팩터에 반영 | `scheduler/us_market_collector.py` (신규)<br>`core/constants.py` 확장<br>`risk/var_calculator.py` 확장 | ⏳ 대기 |
| **P2** | **⑨ 커뮤니티/오픈소스화** | • GitHub public 전환 (민감 정보 제거)<br>• API 문서화 (Sphinx) 및 설치 스크립트 제공<br>• Docker 이미지 배포로 실행 환경 표준화 | `README.md`, `docs/`, `Dockerfile` | ⏳ 대기 |

---

## ✅ 5. 현재까지 완료된 정교화 작업 (v7.6.2)

| 단계 | 작업 내용 | 결과 |
| :--- | :--- | :--- |
| ① | 거시 데이터 확장 (VIX, S&P, SOX, WTI, KTB) | Yahoo Finance 9개 지표 수집 |
| ② | 매수/매도 타점 정밀화 (시장 충격 + 3분할 체결) | Almgren-Chriss 모델 도입 |
| ③ | Calibration 자동 튜닝 | 체결률 기반 임계값 자동 조정 |
| ④ | ML 신호 융합 | XGBoost 예측 18% 가중치 반영 |
| ⑤ | 멀티 전략 라우터 | 3개 전략 병렬 점수 집계 (30%) |
| ⑥ | VaR 리스크 조정 | Modified VaR 기반 포지션 패널티 |
| ⑦ | ML/VaR 리포트 고도화 | 일일/주간 Telegram/PDF에 표시 |
| ⑧ | 단위 테스트 도입 | 16개 핵심 함수 테스트 통과 |
| ⑨ | 설정 중앙화 | config.yaml 단일 진입점 |
| ⑩ | 예외 처리 표준화 | 전역 핸들러 + Telegram 알림 |
| ⑪ | Graceful Shutdown | 시그널 핸들러 + 태스크 추적 |
| ⑫ | Windows 호환성 | 시그널 핸들러, 로그 포맷 수정 |

---

## 📊 6. 테스트 검증 결과 (최종)

| 테스트 스크립트 | 결과 | 비고 |
| :--- | :--- | :--- |
| `run_integration_tests.py` | ✅ 11/11 통과 | UTF-8 강제 적용 |
| `pytest tests/unit/` | ✅ 16/16 통과 | 단위 테스트 전면 통과 |
| `diagnose_system.py` | ✅ 통과 | 모든 핵심 모듈 정상 |
| `scan_all_files.py` | ✅ 통과 | Pyflakes 경고 0개 |
| `test_telegram_events.py` | ✅ 7/7 통과 | 이벤트 템플릿 정상 |

---

## 💬 7. Telegram 최종 명령어 (v7.6.2)

| 사용자 입력 | 시스템 응답 |
| :--- | :--- |
| `현황`, `오늘 장은?` | 시스템 실시간 상태 + 현재 국면 + 수집기 건강 상태 |
| `신호`, `최근 매수 신호` | 최근 5일간 신호 목록 (최대 10개) |
| `삼전`, `005930`, `하이닉스` | **종합 분석 리포트** (재무/뉴스/수급/기술 + ML 예측 + VaR 계수) |
| `/분석 005930` | 동일 (종합 분석 리포트) |

---

## 🚀 8. 바로 다음에 시작할 작업 (권장)

**추천 순서 (위험도 최소화 전략):**
1. **③ 포트폴리오 성과 추적기** (영향도 낮음, 기존 리포트에 추가만 하면 됨)
2. **② Paper Trading 모의투자 연동** (실제 자금 없이 검증 가능)
3. **① 백테스트 Walk-Forward 검증** (Paper Trading 데이터로 보정)

성과 추적기를 먼저 만들면, Paper Trading 시작과 동시에 실시간 성과를 모니터링할 수 있어 효율적입니다.

---

**📌 Git Push 완료 후, 원하시는 작업을 말씀해주세요!** 😊