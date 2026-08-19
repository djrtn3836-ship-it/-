# 🇰🇷 AI 퀀트 트레이딩 시스템 v7.6.4 FINAL

> 한국 주식 시장(KOSPI/KOSDAQ)을 실시간으로 분석하고, Telegram으로 신호를 전송하는 자동화된 AI 퀀트 시스템입니다.

---

## 🚀 주요 기능

- **실시간 데이터 수집**: Kiwoom WebSocket을 통해 500+ 종목 실시간 체결/호가 수집
- **멀티 전략 엔진**: 추세(Trend), 역추세(Reversal), 돌파(Breakout) 3개 전략 병렬 실행 (30% 가중치)
- **머신러닝 융합**: XGBoost 예측 확률을 실시간 점수에 반영 (18% 가중치)
- **리스크 관리**: 포트폴리오 VaR(Monte Carlo 시뮬레이션) 및 개별 종목 Modified VaR
- **트레일링 스탑**: ATR 기반 동적 손절/익절 자동 관리
- **Telegram 알림**: 신호 진입, 손절 상승, 익절, 청산 등 7가지 이벤트 실시간 전송
- **종합 분석 리포트**: `/삼전` 입력 시 재무/뉴스/수급/기술적 지표/AI 판단 통합 리포트 제공

---

## 📂 시스템 구조
stock_analyzer_v5.1.2/
├── scanner_main.py # 메인 엔트리 포인트
├── core/ # 코어 모듈 (설정, 로깅, 예외 처리, 디버그)
├── scanner/ # 실시간 모니터링 & 분석 엔진
├── strategy/ # 3개 멀티 전략
├── orchestrator/ # 전략 라우터, 포트폴리오 관리
├── data/ # 키움, DART, 뉴스, DB 연동
├── report/ # Telegram, PDF 리포트
├── risk/ # VaR, 리스크 관리
├── filters/ # 매크로, 섹터, 종목 필터
├── config/ # YAML 설정 파일들
└── tests/ # 11개 통합 + 16개 단위 테스트

---

## ⚙️ 설치 및 실행

### 1. Python 환경 준비
```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt2. 환경변수 설정
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
🧪 테스트
bash
# 통합 테스트 (11개)
python run_integration_tests.py

# 단위 테스트 (16개)
pytest tests/unit/ -v
📊 로그 및 모니터링
디렉토리	내용
logs/scanner.log	시스템 전체 로그 (JSON 포맷)
logs/blackbox/blackbox.log	WebSocket Raw 데이터 (자동 순환)
logs/crashes/	오류 발생 시 디버그 스냅샷 (CPU/메모리 포함)
logs/debug/debug_trace.jsonl	이벤트 추적 (Calibration 분석용)
🔧 개발자 정보
버전: v7.6.4 FINAL

Python: 3.12.9

라이선스: MIT (비공개)

문의: (본인)

⚠️ 주의사항
본 시스템은 투자 자문 도구가 아니며, 모든 투자 결정은 본인 책임입니다.

실계좌 연동(Live Trading)은 아직 Phase 1(Shadow Mode)으로, 자동매매가 실행되지 않습니다.

반드시 .env 파일에 실제 API 키를 입력해야 정상 동작합니다.

text

---

### 2️⃣ `.env.example` – 환경변수 템플릿

```env
# .env.example - v7.6.4
# 이 파일을 .env로 복사하고 실제 API 키를 입력하세요.

# ============================================================
# 키움증권 Open API+ (필수)
# ============================================================
KIWOOM_APP_KEY=your_kiwoom_app_key_here
KIWOOM_APP_SECRET=your_kiwoom_app_secret_here

# ============================================================
# DART (금융감독원 전자공시) API (선택, 재무 데이터용)
# ============================================================
DART_API_KEY=your_dart_api_key_here

# ============================================================
# NAVER API HUB (뉴스 감성 분석용, 선택)
# ============================================================
NAVER_CLIENT_ID=your_naver_client_id_here
NAVER_CLIENT_SECRET=your_naver_client_secret_here

# ============================================================
# Telegram Bot (필수, 알림 수신용)
# ============================================================
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
TELEGRAM_CHAT_ID=your_telegram_chat_id_here

# ============================================================
# 로깅 설정 (선택, 기본값 사용 권장)
# ============================================================
LOG_LEVEL=DEBUG
STRUCTURED_LOGGING=true
LOG_DIR=./logs
LOG_MAX_BYTES=10485760
LOG_BACKUP_COUNT=10
3️⃣ Dockerfile – 컨테이너 표준화
dockerfile
# Dockerfile - v7.6.4
FROM python:3.12-slim

WORKDIR /app

# 시스템 의존성 설치 (ReportLab 한글 폰트용)
RUN apt-get update && apt-get install -y \
    fonts-nanum \
    && rm -rf /var/lib/apt/lists/*

# Python 패키지 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 소스 코드 복사
COPY . .

# 볼륨 마운트 포인트 (로그, DB, 설정)
VOLUME ["/app/logs", "/app/data", "/app/config"]

# 실행 명령어
CMD ["python", "scanner_main.py"]
4️⃣ docker-compose.yml – 간편 실행
yaml
# docker-compose.yml - v7.6.4
version: '3.8'

services:
  scanner:
    build: .
    container_name: stock_scanner_v764
    restart: unless-stopped
    environment:
      - TZ=Asia/Seoul
    volumes:
      - ./logs:/app/logs
      - ./data:/app/data
      - ./config:/app/config
      - ./.env:/app/.env
    ports:
      - "8080:8080"   # 헬스체크 서버
    stdin_open: true
    tty: true
