# 🚀 처음 실행하기 (RUN FIRST)

이 폴더는 **stock_analyzer V10** 실행 패키지입니다.
(기준일 2026-10-04 · 스케줄러 16잡 · 테스트 1513 passed)

---

## 1. 준비물

| 항목 | 요구사항 |
| :--- | :--- |
| OS | Windows 10/11 (Linux/macOS도 가능) |
| Python | **3.12** (3.11 이상 권장) |
| API 키 | Kiwoom 앱키/시크릿, Telegram 봇 토큰/챗ID |
| 디스크 | 약 1GB (가상환경 + 5년 시세 DB 포함) |

> ⚠️ **데이터 축적은 평일 장중(09:00~15:30 KST)에만** 진행됩니다.
> 비거래일/주말에 실행하면 "오늘은 비거래일입니다" 메시지와 함께 정상 종료합니다(오류 아님).

---

## 2. 설치 (3단계)

```bash
# ① 가상환경 생성 + 활성화
python -m venv venv
venv\Scripts\activate            # Windows
# source venv/bin/activate       # Linux/macOS

# ② 의존성 설치
pip install -r requirements.txt

# ③ 환경변수 파일 만들기
copy .env.example .env           # Windows
# cp .env.example .env           # Linux/macOS
```

`.env`를 열고 **4개 값**을 채우세요 (UTF-8, BOM 없음):

```ini
KIWOOM_APP_KEY=발급받은_앱키
KIWOOM_APP_SECRET=발급받은_시크릿
TELEGRAM_BOT_TOKEN=봇토큰
TELEGRAM_CHAT_ID=챗ID
TEST_MODE=false          # 실제 수집/알림 사용
TELEGRAM_ENABLED=true
```

> 🔒 `.env`는 절대 GitHub에 올리지 마세요(이미 .gitignore 처리됨).

---

## 3. 실행

```bash
python app/main.py
```

정상 기동 로그(평일):
```
V10 System Bootstrap Complete
  Scheduler jobs: 16
```

종료는 `Ctrl+C`.

---

## 4. 데이터가 이미 들어있습니다 (선택)

이 패키지에는 `data/decisions.db` (약 28MB)가 포함되어 있습니다:

| 테이블 | 내용 |
| :--- | :--- |
| `ohlcv` | **5년 일봉 230,441행 / 190종목** (yfinance 백필) |
| `momentum_paper` | 모멘텀 참고신호 모의 기록 |

→ 덕분에 **과거 시세 백필(수 시간 소요)을 건너뛸 수 있습니다.**
처음부터 새로 받고 싶다면 파일을 지우고 아래를 실행하세요:

```bash
python -m scheduler.ohlcv_backfill --years 5
```

---

## 5. 데이터 축적 확인

```bash
# 축적 진행률 (decisions 500 / outcomes 300 도달 시 ML 검증 가능)
python -m scheduler.data_readiness_monitor

# 운영 대시보드(단일 HTML) → reports/dashboard.html
python -m report.html_dashboard

# 텔레그램으로 현황 확인
#   "현황" 또는 "신호" 라고 보내면 응답
```

---

## 6. 무엇이 자동으로 도나요? (스케줄러 16잡)

| 시각(KST) | 작업 |
| :--- | :--- |
| 평일 08:30 | 모멘텀 참고신호 리포트(모의, 주문 없음) |
| 평일 16:00 | 신뢰도 캘리브레이션 채점(5일 전 예측 → 실현가 승/패) |
| 평일 16:45 | 서킷브레이커 시장 리스크 점검 |
| 평일 17:00 | 집중도 리스크(상관행렬) 점검 |
| 평일 17:30 | HTML 대시보드 생성 |
| 일 09:00 | 데이터 축적 게이트 점검 |
| 토 09:00 | 유니버스(종목 목록) 자동 갱신 |
| 수시 | 신호 분석·알림, 이상탐지, 알림 누락 검증 등 |

---

## 7. 문제 해결

| 증상 | 원인/해결 |
| :--- | :--- |
| "오늘은 비거래일입니다" | 정상 — 평일에 실행하세요 |
| "자격증명 없음" | `.env`의 4개 값 확인 |
| Telegram 응답 없음 | `TELEGRAM_ENABLED=true` 확인, 봇 토큰 재확인 |
| 유니버스 폴백 경고 | `python -m scheduler.universe_fetcher` 실행 |
| DB 잠금 오류 | 이미 실행 중인 프로세스 종료 후 재시작 |

---

## 8. ⚠️ 이 시스템의 위치 (검증 결과)

- **수익 전략이 아닙니다.** 가격 기반 팩터(모멘텀/반전/저변동성/52주고가)에서
  통계적으로 유의한 알파가 없음을 확인했습니다(롱숏 시장중립 진단 포함, |t| < 2).
- 이 시스템의 가치는 **감시·리스크 알림**(집중도·서킷브레이커·이상탐지)입니다.
- 모멘텀은 참고 신호(모의)로만 기록되며 **주문/포지션이 발생하지 않습니다.**
- 차기 단계는 비가격 팩터(감성/공시/ML) 검증이며, 데이터 축적이 선행 조건입니다.
