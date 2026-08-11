📋 CONTEXT.md - 정밀 확장 버전 (전체 상태 저장소)
아래 내용으로 CONTEXT.md 파일을 완전히 덮어쓰기 하세요.
(기존 요약본에서 훨씬 더 자세하고 정밀한 버전입니다)

markdown
# 🔬 프로젝트 완전 상태 저장소 (Full Context) - v5.6.0 FINAL

> 📌 **이 문서의 목적**: 새 대화를 시작할 때, 5분 만에 이 프로젝트의 완전한 상태를 복원하기 위한 **영속적 컨텍스트**입니다.
> 📅 **최종 업데이트**: 2026-08-11 (화) 17:30 KST

---

## 📁 1. 프로젝트 기본 정보

| 항목 | 값 |
|------|-----|
| **프로젝트명** | stock_analyzer_v5.1.2 |
| **버전** | v5.6.0 FINAL |
| **GitHub** | https://github.com/djrtn3836-ship-it/- |
| **Python 버전** | 3.12+ |
| **운영 모드** | Phase 1 Shadow Mode (실시간 감시 + 보고서) |
| **목적** | 키움 REST API 기반 실시간 퀀트 트레이딩 시스템 |

---

## 📂 2. 전체 파일 구조 (2026-08-11 기준)
stock_analyzer_v5.1.2/
├── config/
│ ├── config.yaml # 설정 파일 (선택)
│ ├── dart_config.yaml # DART API 설정
│ ├── kiwoom_config.yaml # 키움 API 설정
│ └── secure_config.py # 환경변수 암호화 로더
│
├── core/
│ ├── settings.py # ✅ 신규: 중앙 설정 관리 (dataclass)
│ ├── exceptions.py # ✅ 신규: 커스텀 예외 클래스
│ ├── config.py # ✅ 수정: 통합 설정 관리자 (YAML + .env)
│ ├── scheduler.py # APScheduler 관리 (재시도 포함)
│ ├── holiday_utils.py # 공휴일 판단 유틸리티 (pytimekr)
│ ├── logger.py # 로깅 시스템 (RotatingFileHandler)
│ ├── circuit_breaker.py # 서킷 브레이커
│ └── constants.py # 상수 정의
│
├── data/
│ ├── kiwoom_connector.py # ✅ 수정: WebSocket 5대 개선 완료 (v5.5.0)
│ ├── db_manager.py # Async SQLite 관리 (OHLCV 포함)
│ ├── stock_universe.py # 종목 유니버스 (get_universe 함수 추가)
│ ├── dart_connector.py # DART API 연동 (Risk Score + 재무제표)
│ └── news_crawler.py # 뉴스 크롤러
│
├── scanner/
│ ├── realtime_monitor.py # ✅ 수정: 수신/전략 분리 (asyncio.Queue)
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
├── scanner_main.py # ✅ 수정: 메인 진입점 (설정 통합 + 큐 Worker)
├── test_websocket.py # ✅ 신규: WebSocket 연결 테스트 코드
├── requirements.txt # 의존성 목록
├── .env # 🔒 환경변수 (API 키) - GitHub 미포함
└── README.md # 프로젝트 문서

text

---

## 🔧 3. WebSocket 5대 개선 상세 (kiwoom_connector.py v5.5.0)

| # | 개선 항목 | 설명 | 코드 위치 |
|---|-----------|------|-----------|
| ① | **재연결 시 REG 재전송** | 연결 복구 후 `_subscribed_items`를 순회하며 REG 재전송 | `_reconnect_websocket()` |
| ② | **토큰 만료 감지** | `return_code: 100013` 수신 시 `_refresh_token()` 호출 | `_connect_websocket()` |
| ③ | **다중 그룹 관리** | 100종목 초과 시 `grp_no` 자동 증가 | `register_realtime()` |
| ④ | **PING Echo** | 수신한 `raw` 원문 그대로 반사 (`json.dumps` 금지) | `_ws_receiver()` |
| ⑤ | **TR별 Rate Limiter** | `api-id`(ka10004, ka10008 등)별 독립 대기열 | `_rate_limiters` 딕셔너리 |

---

## ⚙️ 4. 설정 관리 구조

### 4.1 설정 우선순위
환경 변수 (.env) → 최우선

config/config.yaml → 두 번째

core/settings.py 기본값 → 마지막

text

### 4.2 핵심 설정값 (config.yaml + .env)

| 설정 키 | 기본값 | 설명 |
|---------|--------|------|
| `ws_url` | `wss://api.kiwoom.com:10000/api/dostk/websocket` | WebSocket URL |
| `ws_ping_interval` | 20 | PING 간격 (초) |
| `ws_login_timeout` | 10 | LOGIN 응답 대기 (초) |
| `rate_limit_capacity` | 5 | 초당 최대 TR 요청 수 |
| `price_change_ratio` | 0.02 | 신호 감지 변동률 (2%) |
| `cooldown_seconds` | 300 | 동일 방향 신호 쿨링 (5분) |
| `emergency_threshold` | 0.05 | 긴급 신호 기준 (5%) |
| `queue_maxsize` | 10000 | 메시지 큐 최대 크기 |
| `max_subscriptions` | 50 | 최대 구독 종목 수 |

### 4.3 환경변수 (.env)

```env
KIWOOM_APP_KEY=발급받은_앱키
KIWOOM_APP_SECRET=발급받은_시크릿키
DART_API_KEY=발급받은_DART_키
TELEGRAM_BOT_TOKEN=발급받은_봇토큰
TELEGRAM_CHAT_ID=7195362122
🔄 5. 시스템 흐름 (수신/전략 분리 구조)
text
┌─────────────────────────────────────────────────────────────────┐
│                      scanner_main.py                           │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  1. PID 중복 실행 방지 (scanner.pid)                   │   │
│  │  2. 환경변수 검증 (.env)                                │   │
│  │  3. 설정 로드 (config.yaml + .env)                      │   │
│  │  4. DB 초기화 (decisions.db + OHLCV)                    │   │
│  │  5. 키움 연결 (Access Token 발급)                       │   │
│  │  6. WebSocket 연결 + LOGIN + REG                        │   │
│  └─────────────────────────────────────────────────────────┘   │
│                              │                                  │
│                              ▼                                  │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │         RealtimeMonitor (수신 전용)                     │   │
│  │  - WebSocket 데이터 수신 (_on_data)                     │   │
│  │  - 큐에 데이터 적재 (put_nowait)                        │   │
│  │  - 가벼운 처리 (블로킹 없음)                            │   │
│  │  - 큐 가득 시 데이터 드롭 (지연 방지)                   │   │
│  └─────────────────────────────────────────────────────────┘   │
│                              │                                  │
│                              ▼                                  │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │       asyncio.Queue (maxsize: 10,000)                   │   │
│  └─────────────────────────────────────────────────────────┘   │
│                              │                                  │
│                              ▼                                  │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │       Strategy Worker (분석 전용)                       │   │
│  │  - 큐에서 데이터 소비 (get)                             │   │
│  │  - DeepAnalyzer.analyze() 실행 (무거운 작업)            │   │
│  │  - DB 저장 (save_decision)                               │   │
│  │  - Telegram 전송 (send)                                 │   │
│  └─────────────────────────────────────────────────────────┘   │
│                              │                                  │
│                              ▼                                  │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              스케줄러 (APScheduler)                      │   │
│  │  - 07:00 일일 리포트 (Telegram)                         │   │
│  │  - 16:30 OHLCV 수집 (daily_collector)                   │   │
│  │  - 17:00 피드백 학습 (feedback_learner)                 │   │
│  │  - 매주 월 06:00 주간 PDF (weekly_pdf)                  │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
🗄️ 6. 데이터베이스 스키마 상세
6.1 decisions 테이블 (의사결정 로그)
컬럼	타입	인덱스	설명
id	INTEGER PK	-	자동 증가
ticker	TEXT	✅	종목코드
action	TEXT	✅	BUY/SELL/HOLD
score	REAL	-	0~1 점수
confidence	REAL	-	0~1 신뢰도
price_at_decision	REAL	-	결정 시점 가격
positives	TEXT	-	매수 근거 (JSON 배열)
negatives	TEXT	-	리스크 (JSON 배열)
counterfactuals	TEXT	-	반사실적 분석 (JSON 배열)
created_at	DATETIME	✅	생성 시간
6.2 ohlcv 테이블 (시계열 데이터)
컬럼	타입	인덱스	설명
id	INTEGER PK	-	자동 증가
ticker	TEXT	✅	종목코드
date	TEXT	✅	YYYY-MM-DD
open	REAL	-	시가
high	REAL	-	고가
low	REAL	-	저가
close	REAL	-	종가
volume	INTEGER	-	거래량
6.3 feedback_weights 테이블 (팩터 가중치)
컬럼	타입	설명
id	INTEGER PK	자동 증가
factor_name	TEXT UNIQUE	momentum/volume/volatility/macro/sector
weight	REAL	현재 가중치 (0.1~3.0)
updated_at	DATETIME	업데이트 시간
⚠️ 7. 현재 미해결 이슈 (2026-08-11 기준)
이슈 #1: WebSocket LOGIN 실패
항목	내용
오류 메시지	❌ LOGIN 실패: 접속 허용 요청 처리에 실패했습니다. 접속을 종료합니다
발생 위치	kiwoom_connector.py → _connect_websocket() → LOGIN 패킷 전송 후
추정 원인	① IP 화이트리스트 미등록
② WebSocket API 사용신청 미완료
③ 실전계정 vs 모의계정 URL 불일치
영향	실시간 데이터 수신 불가 (REST API는 정상)
해결 방안	① 키움 개발자센터 IP 재등록
② WebSocket API 사용신청 확인
③ test_websocket.py로 연결 테스트
이슈 #2: 자동 실행 확인 필요
항목	내용
상태	Windows 작업 스케줄러 등록 완료 (08:50 실행)
확인 필요	내일(2026-08-12) 아침 08:50에 정상 실행되는지 확인
대비	로그 파일(logs/scanner.log) 및 Telegram 시작 알림 확인
🧪 8. 테스트 코드 (test_websocket.py)
python
# test_websocket.py - WebSocket 연결 테스트 (실전투자용)
# 실행: python test_websocket.py
# 성공 기준: ✅ LOGIN 성공! 로그 확인
🎯 9. 다음 목표 (우선순위 순)
순위	목표	설명
①	WebSocket LOGIN 성공	IP 재등록 또는 사용신청 완료 후 test_websocket.py 재실행
②	실시간 데이터 수신 검증	LOGIN 성공 후 장중(09:00~15:30)에 0B(체결가) 데이터 수신 확인
③	Telegram 시작 알림 확인	scanner_main.py 실행 시 🟢 시스템 상태 보고 메시지 도착 확인
④	자동 실행 검증	내일(2026-08-12) 08:50 자동 실행 확인
📌 10. 주요 파일 해시 (변경 감지용)
파일	최종 수정일	크기 (추정)
core/settings.py	2026-08-11	~3KB
core/exceptions.py	2026-08-11	~2KB
core/config.py	2026-08-11	~5KB
data/kiwoom_connector.py	2026-08-11	~25KB
scanner/realtime_monitor.py	2026-08-11	~15KB
scanner_main.py	2026-08-11	~12KB
test_websocket.py	2026-08-11	~6KB
🔑 11. 복원 체크리스트 (새 대화 시작 시)
□ CONTEXT.md 파일 읽기 완료
□ GitHub 저장소 상태 확인
□ 현재 이슈(WebSocket LOGIN 실패) 인지
□ 다음 목표(IP 재등록 → LOGIN 성공) 이해
□ 주요 파일 구조 파악 완료
📝 12. 변경 이력
날짜	버전	변경 내용
2026-08-11	v5.6.0 FINAL	WebSocket 5대 개선 + 수신/전략 분리 + 코드 품질 개선 + CONTEXT.md 생성
이 문서는 프로젝트의 완전한 상태를 저장합니다. 새 대화를 시작할 때 이 파일을 읽으면 5분 만에 모든 컨텍스트가 복원됩니다.

text

---

## ✅ 이 파일을 사용하면 얻는 이점

| 상황 | 요약본 사용 시 | 정밀본 사용 시 |
|------|---------------|----------------|
| **새 대화 시작** | "대충 이런 프로젝트였어요" | "코드 구조, 설정값, 오류, 다음 목표까지 완벽히 복원" |
| **오류 해결** | 원인 추측 필요 | 정확한 오류 메시지와 원인 파악 가능 |
| **코드 수정** | 어디를 수정해야 할지 모름 | 어떤 파일을 수정해야 할지 정확히 앎 |
| **시간 낭비** | 30분~1시간 소요 | 5분 만에 복원 완료 |

---

지금 이 파일로 `CONTEXT.md`를 **완전히 덮어쓰기** 하고 Git Push 하세요! 😊