📋 CONTEXT.md (최신 완전 상태 저장소 - v5.6.3 FINAL)
markdown
# 🔬 프로젝트 완전 상태 저장소 (Full Context) - v5.6.3 FINAL

> 📌 **이 문서의 목적**: 새 대화를 시작할 때, 5분 만에 이 프로젝트의 완전한 상태를 복원하기 위한 **영속적 컨텍스트**입니다.
> 📅 **최종 업데이트**: 2026-08-11 (화) 20:37 KST
> ✅ **현재 상태**: WebSocket LOGIN 성공, 시스템 정상 가동 중 (장 마감으로 데이터 미수신)

---

## 📁 1. 프로젝트 기본 정보

| 항목 | 값 |
|------|-----|
| **프로젝트명** | stock_analyzer_v5.1.2 |
| **버전** | v5.6.3 FINAL |
| **GitHub** | https://github.com/djrtn3836-ship-it/- |
| **Python 버전** | 3.12+ |
| **운영 모드** | Phase 1 Shadow Mode (실시간 감시 + 보고서) |
| **목적** | 키움 REST API 기반 실시간 퀀트 트레이딩 시스템 |
| **실행 명령어** | `python scanner_main.py` |

---

## 📂 2. 전체 파일 구조 (2026-08-11 최종)
stock_analyzer_v5.1.2/
├── config/
│ ├── config.yaml # 설정 파일 (YAML)
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
│ ├── kiwoom_connector.py # ✅ 수정: v5.6.3 (Authorization 헤더 제거)
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
│ ├── telegram_sender.py # Telegram 고급화 리포트
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
├── test_websocket.py # ✅ 수정: v5.6.3 (appkey/secretkey + token 필드)
├── CONTEXT.md # ✅ 이 파일 (영속적 컨텍스트)
├── requirements.txt # 의존성 목록
├── .env # 🔒 환경변수 (API 키) - GitHub 미포함
└── README.md # 프로젝트 문서

text

---

## 🔧 3. WebSocket 5대 개선 상세 (kiwoom_connector.py v5.6.3)

| # | 개선 항목 | 설명 | 코드 위치 | 검증 상태 |
|---|-----------|------|-----------|-----------|
| ① | **재연결 시 REG 재전송** | 연결 복구 후 `_subscribed_items`를 순회하며 REG 재전송 | `_reconnect_websocket()` | ✅ `test_websocket.py` 성공 |
| ② | **토큰 만료 감지** | `return_code: 100013` 수신 시 `_refresh_token()` 호출 | `_connect_websocket()` | ✅ 적용 완료 |
| ③ | **다중 그룹 관리** | 100종목 초과 시 `grp_no` 자동 증가 | `register_realtime()` | ✅ 적용 완료 |
| ④ | **PING Echo** | 수신한 `raw` 원문 그대로 반사 (`json.dumps` 금지) | `_ws_receiver()` | ✅ 적용 완료 |
| ⑤ | **TR별 Rate Limiter** | `api-id`(ka10004, ka10008 등)별 독립 대기열 | `_rate_limiters` 딕셔너리 | ✅ 적용 완료 |
| ⑥ | **Authorization 헤더 제거** | WebSocket 연결 시 헤더 없이 LOGIN 패킷만 사용 | `_connect_websocket()` | ✅ **LOGIN 성공** |

### 3.1 테스트 코드 성공 로그 (2026-08-11 20:37)

```text
✅ Access Token 발급 성공
📡 LOGIN 패킷 전송 완료 (서버 응답 대기 중)
✅ WebSocket LOGIN 성공!
📡 WebSocket 연결 및 인증 완료
📡 REG 구독: 005930, 그룹: 1
📡 REG 구독: 000660, 그룹: 1
📡 REG 구독: 035420, 그룹: 1
📡 REG 구독: 005380, 그룹: 1
📡 REG 구독: 051910, 그룹: 1
✅ RealtimeMonitor 시작 완료 (구독 종목: 5개)
✅ Telegram 메시지 전송 성공 (시작 알림)
⚙️ 4. 설정 관리 구조
4.1 설정 우선순위
text
1. 환경 변수 (.env) → 최우선
2. config/config.yaml → 두 번째
3. core/settings.py 기본값 → 마지막
4.2 핵심 설정값 (config.yaml + .env)
설정 키	현재 값	설명
ws_url	wss://api.kiwoom.com:10000/api/dostk/websocket	WebSocket URL (실전)
ws_ping_interval	20	PING 간격 (초)
ws_login_timeout	10	LOGIN 응답 대기 (초)
rate_limit_capacity	5	초당 최대 TR 요청 수
price_change_ratio	0.02	신호 감지 변동률 (2%)
cooldown_seconds	300	동일 방향 신호 쿨링 (5분)
emergency_threshold	0.05	긴급 신호 기준 (5%)
queue_maxsize	10000	메시지 큐 최대 크기
max_subscriptions	50	최대 구독 종목 수
4.3 환경변수 (.env) - GitHub 미포함
env
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
⚠️ 7. 현재 상태 (2026-08-11 20:37 기준)
항목	상태	상세
WebSocket LOGIN	✅ 성공	return_code: 0 확인
REG 구독	✅ 성공	5개 종목 등록 완료
실시간 데이터 수신	⏳ 대기 중	장 마감으로 데이터 없음 (정상)
Telegram 시작 알림	✅ 전송 완료	🟢 시스템 상태 보고 도착 확인
헬스체크 서버	✅ 실행 중	http://0.0.0.0:8080/health
전략 Worker	✅ 실행 중	큐 소비 대기
스케줄러	✅ 등록 완료	4개 작업 등록됨
PID 파일	✅ 생성됨	scanner.pid (중복 실행 방지)
7.1 현재 미해결 이슈
이슈	상태	설명
장 마감 데이터 미수신	⏳ 대기	정상 (내일 09:00 이후 수신 예정)
자동 실행 검증	⏳ 미확인	내일(2026-08-12) 08:50 확인 예정
🎯 8. 다음 목표 (우선순위 순)
순위	목표	설명
①	내일 장중 실시간 데이터 수신 확인	2026-08-12 09:00~15:30 사이 python scanner_main.py 실행
②	자동 실행 검증	Windows 작업 스케줄러(08:50) 정상 작동 확인
③	신호 감지 및 Telegram 리포트 수신	변동률 2% 이상 시 BUY/SELL 신호 수신 확인
④	주간 PDF 생성 확인	다음 월요일(2026-08-17) 06:00 PDF 자동 생성 확인
📌 9. 주요 파일 해시 (변경 감지용)
파일	최종 수정일	크기 (추정)
core/settings.py	2026-08-11	~3KB
core/exceptions.py	2026-08-11 (20:36 수정)	~2KB
core/config.py	2026-08-11	~5KB
data/kiwoom_connector.py	2026-08-11 (20:37 수정)	~25KB
scanner/realtime_monitor.py	2026-08-11	~15KB
scanner_main.py	2026-08-11	~12KB
test_websocket.py	2026-08-11 (20:34 수정)	~6KB
CONTEXT.md	2026-08-11 (현재)	~8KB
🔑 10. 복원 체크리스트 (새 대화 시작 시)
□ CONTEXT.md 파일 읽기 완료
□ GitHub 저장소 상태 확인 (git pull 최신화)
□ 현재 시스템 상태(LOGIN 성공) 인지
□ 다음 목표(장중 데이터 수신) 이해
□ 주요 파일 구조 파악 완료
📝 11. 변경 이력
날짜	버전	변경 내용
2026-08-11	v5.6.0 FINAL	WebSocket 5대 개선 + 수신/전략 분리 + 코드 품질 개선
2026-08-11	v5.6.1	test_websocket.py .env 절대 경로 지정
2026-08-11	v5.6.2	토큰 발급 필드명 수정 (appkey/secretkey)
2026-08-11	v5.6.3	Authorization 헤더 제거 → LOGIN 성공!
2026-08-11	v5.6.3	exceptions.py Optional import 오류 수정
🧪 12. 실행 방법
12.1 일반 실행 (장중)
bash
cd C:\Users\hdw38\Desktop\stock_analyzer_v5.1.2
python scanner_main.py
12.2 WebSocket 연결 테스트
bash
python test_websocket.py
12.3 헬스체크 확인
bash
curl http://localhost:8080/health
이 문서는 프로젝트의 완전한 상태를 저장합니다. 새 대화를 시작할 때 이 파일을 읽으면 5분 만에 모든 컨텍스트가 복원됩니다.

text
