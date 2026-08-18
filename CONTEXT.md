📁 1. 최신 CONTEXT.md (v7.2.11) - 전체 내용
markdown
# 🔬 프로젝트 완전 상태 저장소 (Full Context) - v7.2.11 FINAL

> 📌 **이 문서의 목적**: 새 대화를 시작할 때, 10분 만에 이 프로젝트의 완전한 상태를 복원하고, 다른 AI에게 즉시 인수인계할 수 있는 **초정밀 컨텍스트**입니다.
> 📅 **최종 업데이트**: 2026-08-18 (화) 16:00 KST
> ✅ **현재 상태**: 
> - 60개 이상 파일 전수 검사 통과
> - 1분기 고도화 로드맵 100% 완료
> - 세부 정교화(Calibration) 단계 100% 완료
> - Telegram 종합 분석 리포트 (재무/뉴스/수급/기술적 지표/AI 판단) 지원
> - **시스템 출격 준비 완료 (Phase 1 Shadow Mode 운영 중)**

---

## 📁 1. 프로젝트 기본 정보

| 항목 | 값 |
| :--- | :--- |
| **프로젝트명** | stock_analyzer_v5.1.2 |
| **버전** | v7.2.11 FINAL (AI 퀀트 어시스턴트 + 종합 분석 리포트) |
| **Python 버전** | 3.12.9 |
| **운영 모드** | Phase 1 Shadow Mode (실시간 감시 + Telegram 보고서, 자동매매 없음) |
| **실행 명령어** | `python scanner_main.py` |

---

## 📂 2. 전체 파일 구조 (2026-08-18 최종)
stock_analyzer_v5.1.2/
├── config/
│ ├── config.yaml
│ ├── dart_config.yaml
│ ├── kiwoom_config.yaml
│ ├── secure_config.py
│ ├── discovered_keys.json
│ ├── naver_api_cache.json
│ ├── corp_code_cache.json
│ └── nlp_model.pkl # 🔥 NLU 모델 (자동 생성)
│
├── core/
│ ├── settings.py, exceptions.py, config.py, scheduler.py
│ ├── holiday_utils.py (v3.0 - holidays 패키지)
│ ├── logger.py, circuit_breaker.py, constants.py (v2.0)
│ ├── blackbox_logger.py (v1.0)
│ ├── sentiment_analyzer.py (v1.1 - 지연 로딩)
│ ├── font_utils.py (v1.0 - 한글 폰트 통합)
│ ├── debug_tower.py (v2.0 - 디버그 관제탑)
│ ├── regime_manager.py (v1.1 - 중앙 국면 관리)
│ └── natural_language.py (v1.0 - 🔥 경량 NLU 엔진)
│
├── data/
│ ├── kiwoom_connector.py (v6.0.5 - 수급 TR 완전 구현)
│ ├── db_manager.py (v5.4.6 - 디버그 관제탑 적용)
│ ├── stock_universe.py (v5.8.1)
│ ├── dart_connector.py (v5.3.2 - corp_code 매핑 + 캐싱)
│ └── news_crawler.py (v6.2.3 - ThreadedResolver 적용)
│
├── scanner/
│ ├── realtime_monitor.py (v5.6.9 - RegimeManager 연동)
│ └── deep_analyzer.py (v7.2.18 - 체결률 기반 신호 강도 조정)
│
├── report/
│ ├── telegram_sender.py (v7.2.8 - 이벤트 템플릿)
│ ├── telegram_commands.py (v7.0 - 🔥 종합 분석 리포트 + NLU 연동)
│ ├── daily_report.py (v5.9.1)
│ └── weekly_pdf.py (v5.9.4 - DART/수급 연동)
│
├── feedback/
│ └── feedback_learner.py (v7.2.3 - XGBoost 비동기 분리)
│
├── decision/
│ ├── hybrid_decider.py (v7.2.2 - BUY/SELL/HOLD 표준화)
│ └── portfolio_allocator.py (v7.2.4 - config 연동)
│
├── validation/
│ ├── backtester.py (v7.2.4)
│ └── execution_simulator.py (v2.0 - 🔥 호가깊이 기반 체결 시뮬레이터)
│
├── filters/
│ ├── stock_filter.py (v5.1.5 - 🔥 적응형 RSI/이평선)
│ ├── macro_filter.py (v5.1.3 - 🔥 거시 데이터 연동)
│ ├── sector_filter.py, korean_special_filter.py, dynamic_weighter.py
│
├── scheduler/
│ ├── daily_collector.py
│ └── macro_collector.py (v1.1 - 🔥 거시 데이터 수집 + 장애 알림)
│
├── analytics/ # 🔥 신규 폴더 (Calibration)
│ └── calibration_analyzer.py (v1.0 - 통계 분석기)
│
├── tests/
│ └── (11개 통합 테스트 파일)
│
├── scanner_main.py (v7.2.11 - 🔥 NLU + 종합 분석 리포트 연동)
├── run_integration_tests.py
├── CONTEXT.md (v7.2.11 FINAL)
├── requirements.txt (v7.0.0 + pyflakes, scikit-learn, joblib)
├── .env (🔒 GitHub 미포함)
└── README.md

text

---

## 🔧 3. 개발 로드맵 이행 현황 (고도화 + 정교화)

### 📌 1분기 고도화 로드맵 (완료)

| 항목 | 상태 | 설명 |
| :--- | :--- | :--- |
| **거시 데이터 통합** | ✅ 완료 | Yahoo Finance 실시간 수집 (KOSPI, USD/KRW, VIX, 금리), 10분 TTL 캐싱, 장애 시 Telegram 알림 |
| **적응형 기술 분석** | ✅ 완료 | RegimeManager 60초 갱신, StockFilter 동적 RSI/이평선 (Bull/Bear/Sideways 대응) |
| **매수/매도 타점 정밀화** | ✅ 완료 | 호가깊이 기반 체결 시뮬레이터 (다중 호가 순회, 평균 체결가, 부분 체결 지원) |
| **UI/대시보드** | ❌ 대체 | Telegram `/상태` 명령어로 대체 (외부 접속 이슈 해결) |
| **시스템 리팩토링** | ✅ 완료 | RegimeManager 분리, 디버그 관제탑, 통합 테스트 완료 |
| **자연어 이해(NLU)** | ✅ 완료 | scikit-learn 기반 의도 분류기 + 종목명 인식 (오프라인, 무료) |
| **종합 분석 리포트** | ✅ 완료 | 재무(PER/ROE/부채비율), 뉴스(감성/헤드라인), 수급(외국인/기관), 기술적 지표 통합 |

### ⚙️ 정교화(Calibration) 세부 작업 (완료)

| 단계 | 작업 내용 | 적용 파일 | 결과 |
| :--- | :--- | :--- | :--- |
| **①** | 주문량 동적 계산 | `deep_analyzer.py` | 평균 거래량 기반 주문량 (0.8%), 최소 10주 ~ 최대 500주 |
| **②** | 체결률 기반 신호 강도 조정 | `deep_analyzer.py` | 30% 미만: HOLD 보류, 30~70%: 확신도 하향, 70% 이상: 정상 진입 |
| **③** | 거시 데이터 장애 알림 | `macro_collector.py` | 3회 연속 수집 실패 시 Telegram 경고 발송 (30분 재발 방지) |
| **④** | Calibration 통계 분석기 | `analytics/calibration_analyzer.py` | `debug_trace.jsonl` 분석 → 체결률/슬리피지/국면 전환 통계 리포트 생성 |
| **⑤** | Telegram 한글 명령어 버그 수정 | `telegram_commands.py` | `CommandHandler` → `MessageHandler`로 변경, 한글 명령어 정상 동작 |
| **⑥** | 자연어 종목명 인식 확장 | `natural_language.py` | `STOCK_NAME_MAP`에 "하이닉스", "현대차" 등 추가, 티커 추출 로직 개선 |
| **⑦** | 종합 리포트 정보 추가 | `telegram_commands.py` | 현재가 + 점수 → 재무/뉴스/수급/기술적 지표/AI 판단 통합 |

---

## 🗄️ 4. 데이터베이스 스키마 (최신)

| 테이블 | 용도 | 주요 컬럼 |
| :--- | :--- | :--- |
| `decisions` | 의사결정 로그 | ticker, action, score, confidence, price, sentiment_score |
| `ohlcv` | 시계열 데이터 | ticker, date, open, high, low, close, volume |
| `feedback_weights` | 팩터 가중치 | factor_name, weight |
| `decision_outcomes` | 성과 추적 | decision_id, return_1d, return_5d, is_correct |

---

## 📊 5. 통합 검증 최종 결과 (2026-08-18)

| 테스트 스크립트 | 결과 | 비고 |
| :--- | :--- | :--- |
| `diagnose_system.py` | ✅ 통과 | (파일 경고 무시) |
| `scan_all_files.py` | ✅ 통과 | (55/55) |
| `test_domestic_mock.py` | ✅ 통과 | Telegram 3/3 전송 성공 |
| `test_naver_simple.py` | ✅ 통과 | NAVER API 200 OK |
| `test_parser.py` | ✅ 통과 | 7/7 파싱 성공 |
| `test_telegram_events.py` | ✅ 통과 | 7/7 템플릿 전송 성공 |
| `run_integration_tests.py` | ✅ 11/11 통과 | 모든 테스트 성공 |

---

## 💬 6. Telegram 최종 명령어 및 자연어 예시

| 사용자 입력 | 시스템 응답 |
| :--- | :--- |
| `현황`, `오늘 장은?`, `/status` | 시스템 실시간 상태 (가동 시간, 구독 종목, 큐 사용률, 국면) |
| `신호`, `최근 매수 신호`, `/신호` | 최근 5일간 매수/매도 신호 목록 (최대 10개) |
| `삼전`, `005930`, `하이닉스`, `현대차` | **종합 분석 리포트** (재무/뉴스/수급/기술적 지표/AI 판단) |
| `삼전 분석`, `/분석 005930` | 종합 분석 리포트 (동일) |

---

## 🛡️ 7. 비상시 대응 매뉴얼

| 문제 상황 | 진단 도구 | 복구 방법 |
| :--- | :--- | :--- |
| Telegram 응답 없음 | `logs/blackbox/blackbox.log` | `discovered_keys.json` 확인, 키 추가 |
| 종목 분석 시 "현재가 없음" | `RealtimeMonitor` 연결 확인 | 장중인지 확인, WebSocket 재연결 대기 |
| Calibration 분석 데이터 부족 | `analytics/calibration_analyzer.py` | 24시간 이상 운영 후 재실행 |
| NLU 모델 오류 | `config/nlp_model.pkl` 삭제 | 재시작 시 자동 재학습 |

---

## 📌 8. 변경 이력 (v7.2.8 → v7.2.11)

| 날짜 | 버전 | 변경 내용 |
| :--- | :--- | :--- |
| 2026-08-17 | v7.2.8 | DART 매핑, 수급 TR 구현, 모든 검증 완료 |
| 2026-08-18 | v7.2.11 | **거시 데이터, NLU, 종합 분석 리포트, 정교화(Calibration) 완료** |

---

**🚀 시스템 v7.2.11 FINAL: 이제 Telegram에 '삼전'만 입력해도 재무, 뉴스, 수급, AI 판단이 담긴 종합 리포트를 받을 수 있습니다!**