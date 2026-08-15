📄 CONTEXT.md (v7.0.0 FINAL)
markdown
# 🔬 프로젝트 완전 상태 저장소 (Full Context) - v7.0.0 FINAL (AI 퀀트 어시스턴트)

> 📌 **이 문서의 목적**: 새 대화를 시작할 때, 5분 만에 이 프로젝트의 완전한 상태를 복원하기 위한 **영속적 컨텍스트**입니다.
> 📅 **최종 업데이트**: 2026-08-15 (토) 15:30 KST
> ✅ **현재 상태**: 
> - 모든 파일 문법/임포트 검사 통과 (48/48)
> - 키움 API 토큰 발급 성공
> - Telegram 이벤트 템플릿 7/7 테스트 완료
> - NAVER API HUB 뉴스 API 연동 성공 (200 OK 확인)
> - 뉴스 감성 분석, XGBoost 학습, 5일 수익률, Kelly 최적화 기능 탑재

---

## 📁 1. 프로젝트 기본 정보

| 항목 | 값 |
| :--- | :--- |
| **프로젝트명** | stock_analyzer_v5.1.2 |
| **버전** | v7.0.0 FINAL (AI 퀀트 어시스턴트) |
| **GitHub** | https://github.com/djrtn3836-ship-it/- |
| **Python 버전** | 3.12.9 |
| **운영 모드** | Phase 1 Shadow Mode (실시간 감시 + 보고서, 자동매매 없음) |
| **실행 명령어** | `python scanner_main.py` |
| **PC 사양** | Intel Ultra 5 225F, DDR5 32GB, RTX 5060 |

---

## 📂 2. 전체 파일 구조 (2026-08-15 최종)
stock_analyzer_v5.1.2/
├── config/
│ ├── config.yaml # 선택적 설정 파일
│ ├── dart_config.yaml
│ ├── kiwoom_config.yaml
│ ├── secure_config.py # 환경변수 암호화
│ ├── discovered_keys.json # 🔥 자가 학습된 티커 키 저장소 (자동 생성)
│ └── naver_api_cache.json # 🔥 NAVER API 성공 조합 캐시 (자동 생성)
│
├── core/
│ ├── settings.py # 중앙 설정 관리
│ ├── exceptions.py # 커스텀 예외
│ ├── config.py # 통합 설정 관리자
│ ├── scheduler.py # APScheduler + 재시도
│ ├── holiday_utils.py # 공휴일 판단 (pytimekr)
│ ├── logger.py # 로깅 시스템
│ ├── circuit_breaker.py
│ ├── constants.py
│ ├── blackbox_logger.py # 블랙박스 로깅 (자동 로테이션 10MB)
│ └── sentiment_analyzer.py # 🔥 신규: 뉴스 감성 분석기 (KoBERT/KcELECTRA)
│
├── data/
│ ├── kiwoom_connector.py # v6.0.2 (자가 적응 파서 + 하드 리셋 + 백필)
│ ├── db_manager.py # v5.4.2 + close() 메서드
│ ├── stock_universe.py # v5.8.1 (Fallback 500종목)
│ ├── dart_connector.py
│ └── news_crawler.py # 🔥 v6.2.0 (NAVER API HUB 완전 대응)
│
├── scanner/
│ ├── realtime_monitor.py # v5.6.6 (500종목 설정, 쿨링)
│ └── deep_analyzer.py # 🔥 v7.0.0 (뉴스 감성 + XGBoost 통합)
│
├── report/
│ ├── telegram_sender.py # 🔥 v6.2.1 (이벤트 기반 5가지 템플릿)
│ ├── daily_report.py # v5.9.1 (성과 피드백 + 동적 포지셔닝)
│ └── weekly_pdf.py # v5.9.1 (뉴스 요약 + 트레일링 통계)
│
├── feedback/
│ └── feedback_learner.py # 🔥 v7.0.0 (5일 수익률 + XGBoost 학습)
│
├── decision/
│ └── portfolio_allocator.py # 🔥 v7.0.0 (안전장치 + Kelly 고도화)
│
├── validation/
│ └── backtester.py # 🔥 v7.0.0 (검증 체계 유지 + 고도화)
│
├── filters/, decision/, monitor/, orchestrator/, risk/, regime/
│ └── (전체 48개 파일, 모두 문법/임포트 검사 통과)
│
├── scanner_main.py # v6.2.0 (이벤트 기반 액션 센터)
├── diagnose_system.py # v1.1 (시스템 전신 진단)
├── scan_all_files.py # v1.1 (전체 파일 문법/임포트 일괄 검사)
├── test_parser.py # v1.0 (WebSocket 파서 테스트)
├── test_telegram_events.py # v1.0 (Telegram 이벤트 템플릿 테스트)
├── test_domestic_mock.py # v1.0 (국내장 파이프라인 Mock 검증)
├── test_naver_api.py # 🔥 v1.1 (NAVER API HUB 테스트 - 자동 적응형)
├── CONTEXT.md # ✅ 이 파일 (v7.0.0 FINAL)
├── requirements.txt # 🔥 v7.0.0 (transformers, torch, xgboost 추가)
├── .env # 🔒 GitHub 미포함
└── README.md

text

---

## 🔧 3. 핵심 수정 내역 (v6.2.1 → v7.0.0)

| 버전 | 날짜 | 주요 수정 사항 |
| :--- | :--- | :--- |
| **v6.2.1** | 08/15 | Telegram side 필드 수정 (BUY/SELL 방향 정확화), 이벤트 템플릿 완성 |
| **v7.0.0** | 08/15 | 🚀 **AI 퀀트 어시스턴트 출시**<br>① 뉴스 감성 분석 (KoBERT/KcELECTRA) → 판단에 10% 반영<br>② 5일 수익률 추적 (기존 1일 → 1일+5일)<br>③ XGBoost 하이브리드 학습 (데이터 30건 이상 시 자동 학습)<br>④ Kelly 지수 포지션 최적화 (실측 승률 기반)<br>⑤ 백테스터 검증 체계 고도화 (ValidationStatus 유지)<br>⑥ **NAVER API HUB 연동 완료** (구형 URL → 신형 API HUB) |

---

## ⚙️ 4. v7.0.0 신규 기능 상세

| # | 기능 | 설명 | 관련 파일 |
| :--- | :--- | :--- | :--- |
| ① | **뉴스 감성 분석** | NAVER API HUB로 뉴스 수집 → KoBERT/KcELECTRA로 감성 점수(-1~1) 산출 → 판단에 10% 반영 (Fallback: 키워드 기반) | `core/sentiment_analyzer.py`, `data/news_crawler.py` |
| ② | **5일 수익률 추적** | 과거 1일 수익률 + 5일 수익률을 모두 DB에 저장 → 장기 성과 평가 가능 | `feedback/feedback_learner.py` |
| ③ | **XGBoost 학습** | 30개 이상의 과거 결정 데이터로 XGBoost 분류기 학습 → 신뢰도(Confidence)에 반영 (없으면 EMA 모드로 Fallback) | `feedback/feedback_learner.py` |
| ④ | **Kelly 포지션 최적화** | 실측 승률 + 평균 손익 기반 Kelly 지수 계산 → Half-Kelly + 하드캡(8%) 적용 → Telegram에 권장 포지션 비중 표시 | `decision/portfolio_allocator.py` |
| ⑤ | **백테스터 검증 강화** | 기존 ValidationStatus(Enum) 유지 + Walk-Forward 검증 로직 보존 → 미검증 결과는 Phase 1에서만 사용 | `validation/backtester.py` |
| ⑥ | **NAVER API HUB 연동** | 올바른 엔드포인트(`naverapihub.apigw.ntruss.com`), 헤더(`X-NCP-APIGW-API-KEY-*`), `format=json` & `Accept: application/json` 적용 | `data/news_crawler.py` |

---

## 🔑 5. 환경변수 (.env) 최신 구성

```env
# 키움 API
KIWOOM_APP_KEY=발급받은_앱키
KIWOOM_APP_SECRET=발급받은_시크릿키

# Telegram
TELEGRAM_BOT_TOKEN=발급받은_봇토큰
TELEGRAM_CHAT_ID=7195362122

# 🔥 신규: NAVER API HUB
NAVER_CLIENT_ID=발급받은_Client_ID
NAVER_CLIENT_SECRET=발급받은_Client_Secret
🗄️ 6. 데이터베이스 스키마 (요약)
테이블	용도	주요 컬럼
decisions	의사결정 로그	ticker, action, score, confidence, price, positives, negatives, sentiment_score
ohlcv	시계열 데이터 (일봉)	ticker, date, open, high, low, close, volume
feedback_weights	팩터 가중치	factor_name, weight
decision_outcomes	성과 추적	decision_id, return_1d, return_5d, is_correct
📊 7. 현재 상태 (2026-08-15 15:30 KST 기준)
구성 요소	상태	확인 방법
전체 파일 검사	✅ 48/48 통과	python scan_all_files.py
환경변수 (.env)	✅ 모든 키 존재	diagnose_system.py
Access Token	✅ 발급 성공	diagnose_system.py
Telegram 이벤트 템플릿	✅ 7/7 테스트 통과	python test_telegram_events.py
국내장 파이프라인	✅ 3/3 Telegram 전송 성공	python test_domestic_mock.py
NAVER API HUB	✅ 200 OK (뉴스 수집 성공)	python test_naver_api.py
뉴스 감성 분석	✅ Fallback 모드 정상	core/sentiment_analyzer.py
XGBoost 학습	⏳ 데이터 부족 시 EMA 모드	feedback/feedback_learner.py
WebSocket 자가 치유	✅ 활성화	data/kiwoom_connector.py
블랙박스 로깅	✅ 정상 기록	logs/blackbox/blackbox.log
🔑 8. 복원 체크리스트 (새 대화 시작 시)
□ CONTEXT.md 파일 읽기 완료
□ GitHub 저장소 최신 상태 확인 (git pull)
□ Python 3.12+ 설치 확인
□ .env 파일에 API KEY, SECRET, Telegram 토큰, 네이버 Client ID/Secret 입력
□ pip install -r requirements.txt 실행 (transformers, torch, xgboost 포함)
□ python diagnose_system.py 실행 (모든 테스트 통과 확인)
□ python scan_all_files.py 실행 (48개 파일 모두 통과 확인)
□ python test_telegram_events.py 실행 (Telegram 이벤트 템플릿 테스트)
□ python test_naver_api.py 실행 (NAVER API 200 OK 확인)
□ python scanner_main.py 실행 (실제 운영 시작)
📝 9. 주요 파일별 최신 버전
파일	버전	핵심 기능
data/kiwoom_connector.py	v6.0.2	자가 적응 파서 + 하드 리셋 + 백필
scanner/deep_analyzer.py	v7.0.0	뉴스 감성 + XGBoost 통합
report/telegram_sender.py	v6.2.1	이벤트 기반 5가지 템플릿
feedback/feedback_learner.py	v7.0.0	5일 수익률 + XGBoost 학습
decision/portfolio_allocator.py	v7.0.0	안전장치 + Kelly 고도화
validation/backtester.py	v7.0.0	검증 체계 유지 + 고도화
data/news_crawler.py	v6.2.0	NAVER API HUB 완전 대응
core/sentiment_analyzer.py	v1.0	뉴스 감성 분석 (KoBERT/KcELECTRA)
⚠️ 10. 현재 미해결 이슈 (없음)
이슈	상태	설명
장 마감 데이터 미수신	⏳ 대기	정상 (월요일 09:00 이후 수신 예정)
XGBoost 학습 데이터 부족	⏳ 대기	30건 이상 쌓이면 자동 학습 시작
🎯 11. 다음 목표 (우선순위 순)
순위	목표	설명
①	월요일 장중 실시간 데이터 수신 확인	2026-08-17 09:00~15:30 사이 실행
②	Telegram 5가지 이벤트 알림 수신 확인	SIGNAL_ENTRY, SL_TRAIL, ATR_SPIKE, TP_HIT, EXIT
③	뉴스 감성 점수 반영 확인	Telegram 신호에 "📰 뉴스 감성: +0.25" 표시
④	XGBoost 학습 자동 실행 확인	30건 이상 데이터 축적 시 자동 학습
🛠️ 12. 비상시 대응 매뉴얼
문제 상황	진단 도구	복구 방법
Telegram 신호 안 옴	logs/blackbox/blackbox.log 확인	새 키 발견 시 config/discovered_keys.json에 추가
WebSocket 재연결 실패	diagnose_system.py 실행	네트워크/방화벽 확인 후 PC 재부팅
뉴스 감성 분석 오류	python test_naver_api.py 실행	NAVER API HUB에서 Client Secret 재발급
XGBoost 학습 실패	feedback/feedback_learner.py 로그 확인	데이터 30건 이상 쌓일 때까지 대기 (EMA 모드로 Fallback)
📌 13. 변경 이력
날짜	버전	변경 내용
2026-08-11	v5.6.0~v5.6.6	WebSocket 5대 개선, 500종목 설정
2026-08-12	v5.6.6 FINAL	Fallback 251종목 구독 성공
2026-08-13	v5.8.0	Fallback 500종목, FatalError 제거
2026-08-14	v5.9.0~v5.9.1	트레일링 스탑, 성과 피드백, 뉴스 요약
2026-08-15	v6.0.0~v6.2.1	Phoenix 자가 복구, 블랙박스, 이벤트 기반 액션 센터
2026-08-15	v7.0.0	🚀 AI 퀀트 어시스턴트: 뉴스 감성 + XGBoost + 5일 수익률 + Kelly 최적화 + NAVER API HUB 연동