📋 최종 업데이트된 CONTEXT.md (v5.6.6 FINAL) + Git Push
드디어 모든 작업이 완료되었습니다.
아래는 오늘의 모든 수정 사항(WebSocket 안정화, 500종목 확장, Telegram 최적화)이 반영된 최종 CONTEXT.md 입니다.
이 파일을 프로젝트 루트에 저장한 후, Git Push를 진행하세요.

📁 CONTEXT.md (최종 업데이트 v5.6.6)
markdown
# 🔬 프로젝트 완전 상태 저장소 (Full Context) - v5.6.6 FINAL

> 📌 **이 문서의 목적**: 새 대화를 시작할 때, 5분 만에 이 프로젝트의 완전한 상태를 복원하기 위한 **영속적 컨텍스트**입니다.
> 📅 **최종 업데이트**: 2026-08-11 (화) 21:00 KST
> ✅ **현재 상태**: WebSocket LOGIN 성공, 500종목 구독 완료, Telegram 즉시 전송 최적화 완료 (장 마감으로 데이터 미수신)

---

## 📁 1. 프로젝트 기본 정보

| 항목 | 값 |
| :--- | :--- |
| **프로젝트명** | stock_analyzer_v5.1.2 |
| **버전** | v5.6.6 FINAL |
| **GitHub** | https://github.com/djrtn3836-ship-it/- |
| **Python 버전** | 3.12+ |
| **운영 모드** | Phase 1 Shadow Mode (실시간 감시 + 보고서, 자동매매 없음) |
| **목적** | 키움 REST API 기반 실시간 퀀트 트레이딩 시스템 (500종목 감시) |
| **실행 명령어** | `python scanner_main.py` |
| **PC 사양** | Intel Ultra 5 225F, DDR5 32GB, RTX 5060 (성능 여유 충분) |

---

## 📂 2. 전체 파일 구조 (2026-08-11 최종)
stock_analyzer_v5.1.2/
├── config/
│ ├── config.yaml # 선택적 설정 파일 (수정 불필요)
│ ├── dart_config.yaml # DART API 설정
│ ├── kiwoom_config.yaml # 키움 API 설정
│ └── secure_config.py # 환경변수 암호화 로더
│
├── core/
│ ├── settings.py # ✅ 신규: 중앙 설정 관리 (dataclass)
│ ├── exceptions.py # ✅ 수정: Optional import 추가
│ ├── config.py # ✅ 수정: 통합 설정 관리자
│ ├── scheduler.py # APScheduler 관리 (재시도 포함)
│ ├── holiday_utils.py # 공휴일 판단 유틸리티 (pytimekr)
│ ├── logger.py # 로깅 시스템 (RotatingFileHandler)
│ ├── circuit_breaker.py # 서킷 브레이커
│ └── constants.py # 상수 정의
│
├── data/
│ ├── kiwoom_connector.py # ✅ 수정: v5.6.6 (Authorization 헤더 제거, 완전 안정화)
│ ├── db_manager.py # Async SQLite 관리 (OHLCV 포함)
│ ├── stock_universe.py # 종목 유니버스 (get_universe 함수 추가)
│ ├── dart_connector.py # DART API 연동 (Risk Score + 재무제표)
│ └── news_crawler.py # 뉴스 크롤러
│
├── scanner/
│ ├── realtime_monitor.py # ✅ 수정: v5.6.6 (500종목 확장, 쿨링 유지)
│ └── deep_analyzer.py # ATR + Imbalance + 13개 지표 분석
│
├── report/
│ ├── telegram_sender.py # Telegram 고급화 리포트 (ATR 손절/익절)
│ ├── daily_report.py # 일일 리포트 생성기 (한글화)
│ └── weekly_pdf.py # 주간 PDF 생성기 (DART 연동)
│
├── feedback/
│ └── feedback_learner.py # 피드백 학습 엔진 (DB OHLCV 활용)
│
├── scheduler/
│ └── daily_collector.py # OHLCV 데이터 수집기 (매일 16:30)
│
├── filters/ # 5개 필터 엔진
├── decision/ # 4개 의사결정 엔진
├── monitor/ # 모니터링 모듈
├── orchestrator/ # 오케스트레이터
├── validation/ # 백테스팅 (보류)
├── risk/ # 리스크 관리
├── regime/ # 레짐 감지
│
├── scanner_main.py # ✅ 수정: v5.6.6 (즉시 전송, Worker 2개)
├── test_websocket.py # ✅ 수정: v5.6.3 (appkey/secretkey + token 필드)
├── CONTEXT.md # ✅ 이 파일 (영속적 컨텍스트)
├── requirements.txt # 의존성 목록
├── .env # 🔒 환경변수 (API 키) - GitHub 미포함
└── README.md # 프로젝트 문서

text

---

## 🔧 3. WebSocket 6대 개선 및 안정화 (kiwoom_connector.py v5.6.6)

| # | 개선 항목 | 설명 | 검증 상태 |
| :--- | :--- | :--- | :--- |
| ① | **Authorization 헤더 제거** | WebSocket 연결 시 헤더 없이 LOGIN 패킷만 사용 (드디어 성공!) | ✅ **성공** (20:37) |
| ② | **재연결 시 REG 재전송** | 연결 복구 후 `_subscribed_items`를 순회하며 REG 재전송 | ✅ 적용 완료 |
| ③ | **토큰 만료 감지** | `return_code: 100013` 수신 시 `_refresh_token()` 호출 | ✅ 적용 완료 |
| ④ | **다중 그룹 관리** | 100종목 초과 시 `grp_no` 자동 증가 (500종목 → 5개 그룹) | ✅ 적용 완료 |
| ⑤ | **PING Echo** | 수신한 `raw` 원문 그대로 반사 (`json.dumps` 금지) | ✅ 검증 완료 |
| ⑥ | **TR별 Rate Limiter** | `api-id`(ka10004, ka10008 등)별 독립 대기열 | ✅ 적용 완료 |

---

## ⚙️ 4. 설정 관리 구조 (v5.6.6)

### 4.1 핵심 설정값 (config.yaml + .env)

| 설정 키 | 현재 값 | 설명 |
| :--- | :--- | :--- |
| `ws_url` | `wss://api.kiwoom.com:10000/api/dostk/websocket` | WebSocket URL (실전) |
| `rate_limit_capacity` | 5 | 초당 최대 TR 요청 수 |
| `signal.price_change_ratio` | 0.02 | 신호 감지 변동률 (2%) |
| `signal.cooldown_seconds` | 300 | 동일 방향 신호 쿨링 (5분) |
| `signal.emergency_threshold` | 0.05 | 긴급 신호 기준 (5%) |
| `signal.max_subscriptions` | 500 | 최대 구독 종목 수 |
| `queue_maxsize` | 100000 | 메시지 큐 최대 크기 |
| `worker_count` | 2 | 전략 Worker 병렬 개수 |

### 4.2 환경변수 (.env) - GitHub 미포함

```env
KIWOOM_APP_KEY=발급받은_앱키
KIWOOM_APP_SECRET=발급받은_시크릿키
DART_API_KEY=발급받은_DART_키
TELEGRAM_BOT_TOKEN=발급받은_봇토큰
TELEGRAM_CHAT_ID=7195362122
🔄 5. 시스템 흐름 (최종 v5.6.6)
text
┌─────────────────────────────────────────────────────────────────────┐
│                         scanner_main.py                            │
│  ┌───────────────────────────────────────────────────────────────┐ │
│  │  1. PID 중복 실행 방지 (scanner.pid)                         │ │
│  │  2. 환경변수 검증 (.env) + 설정 로드 (config.yaml)            │ │
│  │  3. DB 초기화 (decisions.db + OHLCV)                          │ │
│  │  4. 키움 연결 (Access Token 발급)                             │ │
│  │  5. WebSocket 연결 (헤더 없이, LOGIN 패킷 인증)               │ │
│  └───────────────────────────────────────────────────────────────┘ │
│                              │                                      │
│                              ▼                                      │
│  ┌───────────────────────────────────────────────────────────────┐ │
│  │      RealtimeMonitor (수신 전용, 최대 500종목)               │ │
│  │  - WebSocket 데이터 수신 (_on_data)                           │ │
│  │  - 큐에 데이터 적재 (put_nowait)                              │ │
│  │  - 동일 종목 중복 알림은 쿨링(5분)으로 차단                  │ │
│  └───────────────────────────────────────────────────────────────┘ │
│                              │                                      │
│                              ▼                                      │
│  ┌───────────────────────────────────────────────────────────────┐ │
│  │       asyncio.Queue (maxsize: 100,000)                        │ │
│  └───────────────────────────────────────────────────────────────┘ │
│                              │                                      │
│                              ▼                                      │
│  ┌───────────────────────────────────────────────────────────────┐ │
│  │       Strategy Worker x 2개 (병렬 처리, 즉시 전송)            │ │
│  │  - 큐에서 데이터 소비 (get)                                   │ │
│  │  - DeepAnalyzer.analyze() 실행 (13개 지표 + ATR + Imbalance) │ │
│  │  - DB 저장 (save_decision)                                    │ │
│  │  - 🔥 신호 발생 시 즉시 Telegram 전송 (버퍼링 0초)           │ │
│  └───────────────────────────────────────────────────────────────┘ │
│                              │                                      │
│                              ▼                                      │
│  ┌───────────────────────────────────────────────────────────────┐ │
│  │              스케줄러 (APScheduler)                            │ │
│  │  - 매일 07:00 일일 리포트 (Telegram)                          │ │
│  │  - 매일 16:30 OHLCV 수집 (daily_collector)                    │ │
│  │  - 매일 17:00 피드백 학습 (feedback_learner)                  │ │
│  │  - 매주 월 06:00 주간 PDF (weekly_pdf)                        │ │
│  └───────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
🗄️ 6. 데이터베이스 스키마 (요약)
테이블	용도	주요 컬럼
decisions	의사결정 로그	ticker, action, score, confidence, price, positives, negatives
ohlcv	시계열 데이터 (일봉)	ticker, date, open, high, low, close, volume
feedback_weights	팩터 가중치	factor_name, weight
📊 7. 현재 상태 (2026-08-11 21:00 기준)
구성 요소	상태	로그 확인
Access Token	✅ 정상 발급	✅ Access Token 발급 성공
WebSocket LOGIN	✅ 드디어 성공!	✅ WebSocket LOGIN 성공!
REG 구독	✅ 성공 (500종목, 5개 그룹)	📡 REG 구독: 005930, 그룹: 1 ...
RealtimeMonitor	✅ 시작 완료	✅ RealtimeMonitor 시작 완료 (구독 종목: 500개)
전략 Worker	✅ 2개 실행 중	🧠 전략 Worker-1 시작 (즉시 전송 모드)
스케줄러	✅ 등록 완료 (4개 작업)	⏰ 스케줄러 등록 완료
Telegram 시작 알림	✅ 전송 완료	✅ Telegram 메시지 전송 성공 (구독 종목 목록 포함)
헬스체크 서버	✅ 실행 중	🩺 헬스체크 서버 실행 중: http://0.0.0.0:8080/health
실시간 데이터	⏳ 대기 중	장 마감으로 데이터 없음 (정상)
⚠️ 8. 현재 미해결 이슈
이슈	상태	설명
장 마감 데이터 미수신	⏳ 대기	정상 (내일 09:00 이후 수신 예정)
자동 실행 검증	⏳ 미확인	내일(2026-08-12) 08:50 Windows 작업 스케줄러 확인 예정
🎯 9. 다음 목표 (우선순위 순)
순위	목표	설명
①	내일 장중 실시간 데이터 수신 확인	2026-08-12 09:00~15:30 사이 python scanner_main.py 실행
②	자동 실행 검증	Windows 작업 스케줄러(08:50) 정상 작동 확인
③	신호 감지 및 Telegram 리포트 수신	변동률 2% 이상 시 BUY/SELL 신호 수신 확인
④	일일 리포트(07:00) 및 피드백 학습(17:00) 확인	첫 자동 생성 보고서 검증
⑤	주간 PDF 생성 확인	다음 월요일(2026-08-17) 06:00 PDF 자동 생성 확인
🔑 10. 복원 체크리스트 (새 대화 시작 시)
□ CONTEXT.md 파일 읽기 완료
□ GitHub 저장소 최신 상태 확인 (git pull)
□ 현재 시스템 상태(LOGIN 성공, 500종목 구독) 인지
□ 다음 목표(장중 데이터 수신) 이해
□ 주요 파일 구조 파악 완료
📝 11. 변경 이력
날짜	버전	변경 내용
2026-08-11	v5.6.0	WebSocket 5대 개선 + 수신/전략 분리
2026-08-11	v5.6.1~5.6.3	.env 경로, 토큰 필드명, Authorization 헤더 제거 → LOGIN 성공!
2026-08-11	v5.6.4	포트 충돌 해결, PID 정리, 메시지 고급화
2026-08-11	v5.6.5	500종목 확장, Telegram 버퍼링 (10초)
2026-08-11	v5.6.6	Telegram 버퍼링 제거 (0초 지연), 쿨링 최적화, Worker 2개, Queue 100k
이 문서는 프로젝트의 완전한 상태를 저장합니다. 새 대화를 시작할 때 이 파일을 읽으면 5분 만에 모든 컨텍스트가 복원됩니다.