# 🇰🇷 AI 퀀트 트레이딩 시스템 v8.0.0 FINAL

> 한국 주식 시장(KOSPI/KOSDAQ)을 실시간으로 분석하고, Telegram으로 신호를 전송하며, **자가 치유(Self-Healing) 및 알림 검증(Alert Verification)** 기능이 내장된 완전 자동화 시스템입니다.

---

## 🚀 주요 기능 (v8.0.0)

- **실시간 데이터 수집**: Kiwoom WebSocket을 통해 **시가총액 상위 200개 종목** 실시간 체결/호가 수집 (105115 오류 완전 차단)
- **자가 치유 감독관 (Supervisor)**: 메인 프로세스가 죽으면 30초 내 자동 재시작, 메모리/큐 적체 감시
- **알림 검증기 (Alert Verifier)**: 매일 16:00에 오늘 발생한 신호와 Telegram 전송 건수를 비교하여 **누락 리포트** 전송
- **멀티 전략 엔진**: 추세(Trend), 역추세(Reversal), 돌파(Breakout) 3개 전략 병렬 실행
- **머신러닝 융합**: XGBoost 예측 확률을 실시간 점수에 반영
- **리스크 관리**: 포트폴리오 VaR(Monte Carlo 시뮬레이션) 및 개별 종목 Modified VaR
- **트레일링 스탑**: ATR 기반 동적 손절/익절 자동 관리
- **Telegram 알림**: 신호 진입, 손절 상승, 익절, 청산 등 7가지 이벤트 실시간 전송
- **종합 분석 리포트**: `/삼전` 입력 시 재무/뉴스/수급/기술적 지표/AI 판단 통합 리포트 제공

---

## 📂 시스템 구조 (v8.0.0)

```text
stock_analyzer_v5.1.2/
├── scanner_main.py          # 메인 엔트리 포인트 (Supervisor 내장)
├── core/
│   ├── supervisor.py        # 🆕 프로세스 감시 및 자동 재시작
│   ├── container.py         # DI 컨테이너 (의존성 주입)
│   ├── config.py            # 중앙 설정 관리
│   ├── logger.py            # JSON/컬러 로깅
│   └── debug_tower.py       # 블랙박스 디버깅
├── scanner/
│   ├── realtime_monitor.py  # WebSocket 데이터 수신 (시총 상위 200개)
│   └── deep_analyzer.py     # AI 분석 엔진 (VaR 캐시 포함)
├── analytics/
│   ├── performance_tracker.py # 실시간 성과 추적
│   └── alert_verifier.py   # 🆕 일일 알림 누락 검증
├── execution/
│   └── order_executor.py    # Paper Trading 주문 실행기
├── risk/
│   └── safety_guard.py      # 시장 위기 감지 (하락/상승 조건 분리)
├── report/
│   ├── telegram_sender.py   # 텔레그램 전송 (자동 분할 + 재시도)
│   └── telegram_commands.py # 자연어 명령어 처리
└── data/
    ├── stock_universe.py    # 종목 유니버스 (시총 정렬 기능 추가)
    └── kiwoom_connector.py  # 키움 REST/WS 커넥터

⚙️ 설치 및 실행
1. Python 환경 준비
bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
pip install psutil yfinance  # Supervisor 및 시총 정렬용
2. 환경변수 설정
.env.example 파일을 .env로 복사하고 실제 API 키를 입력하세요.

bash
cp .env.example .env
3. 실행
bash
python scanner_main.py
📱 Telegram 명령어
명령어	설명
현황, 오늘 장은?	시스템 실시간 상태 (가동 시간, 구독 종목, 큐 사용률, 국면)
신호, 최근 매수 신호	최근 5일간 매수/매도 신호 목록 (최대 10개)
삼전, 005930, 하이닉스	종합 분석 리포트 (재무/뉴스/수급/기술/AI 판단)
🛠️ 설정 파일
파일	설명
config/config.yaml	WebSocket, Rate Limit, Scheduler, Trading 파라미터
config/strategies.yaml	전략별 가중치 (동적 조정 가능)
config/regime_weights.yaml	국면(Bull/Sideways/Bear)별 RSI/이평선 임계값
config/risk_config.yaml	VaR 신뢰수준, 시뮬레이션 횟수, 경고 임계값
📊 로그 및 모니터링
디렉토리	내용
logs/scanner.log	시스템 전체 로그 (JSON 포맷)
logs/blackbox/blackbox.log	WebSocket Raw 데이터 (자동 순환)
logs/crashes/	오류 발생 시 디버그 스냅샷 (CPU/메모리 포함)
🔧 개발자 정보
버전: v8.0.0 FINAL

Python: 3.12.9

특징: Windows 최적화 (uvloop 제거), 자가 치유(Self-Healing), 알림 누락 검증

