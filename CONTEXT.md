# 프로젝트 컨텍스트 (영속적 저장소) - v5.6.0 FINAL

## 📌 기본 정보
- **프로젝트명**: stock_analyzer_v5.1.2
- **버전**: v5.6.0 FINAL
- **GitHub**: https://github.com/djrtn3836-ship-it/-
- **작성일**: 2026-08-11
- **상태**: WebSocket 연결 디버깅 중

## 🔧 최신 변경 사항 (2026-08-11)
### 1. WebSocket 5대 개선 완료 (`kiwoom_connector.py`)
- 재연결 시 REG 자동 재전송 (`_subscribed_items` 저장 후 재구독)
- 토큰 만료(return_code:100013) 감지 및 자동 재발급
- 다중 그룹(grp_no) 자동 할당 (100종목 초과 시 분할)
- PING Echo 검증 (raw 원문 그대로 반사)
- TR별 독립 Rate Limiter 적용 (api-id 기반)

### 2. 수신/전략 분리 (`realtime_monitor.py`, `scanner_main.py`)
- `asyncio.Queue` 도입 (10,000 버퍼)
- 전략 Worker 별도 실행 (수신 블로킹 방지)
- 큐 가득 시 데이터 드롭 정책 적용

### 3. 코드 품질 개선
- 신규 파일: `core/settings.py` (중앙 설정 관리)
- 신규 파일: `core/exceptions.py` (커스텀 예외)
- 신규 파일: `core/config.py` (통합 설정 관리자)
- 설정 중앙화 (config.yaml + .env 병합)
- 하드코딩 제거 (설정값 중앙 관리)

## ⚠️ 현재 이슈 (2026-08-11 기준)
- **WebSocket LOGIN 실패**: `접속 허용 요청 처리에 실패했습니다`
- **추정 원인**: IP 화이트리스트 미등록 또는 WebSocket API 사용신청 미완료
- **진행 상황**: `test_websocket.py`로 연결 테스트 중

## 📁 핵심 파일 상태
| 파일 | 상태 | 설명 |
|------|------|------|
| `core/settings.py` | ✅ 신규 생성 | 중앙 설정 관리 (dataclass) |
| `core/exceptions.py` | ✅ 신규 생성 | 커스텀 예외 클래스 |
| `core/config.py` | ✅ 신규 생성 | 통합 설정 관리자 |
| `data/kiwoom_connector.py` | ✅ 수정 완료 | WebSocket 5대 개선 |
| `scanner/realtime_monitor.py` | ✅ 수정 완료 | 큐 도입 (수신/전략 분리) |
| `scanner_main.py` | ✅ 수정 완료 | 설정 통합 + 큐 Worker |
| `test_websocket.py` | ✅ 신규 생성 | 연결 테스트 코드 |

## 🎯 다음 목표 (순서)
1. WebSocket LOGIN 성공 확인 (IP 재등록 또는 사용신청)
2. 장중 실시간 데이터 수신 검증
3. Telegram "🟢 시스템 시작 성공" 알림 확인

## 📌 주요 설정 (config.yaml + .env)
- WebSocket URL: `wss://api.kiwoom.com:10000/api/dostk/websocket`
- Rate Limit: 초당 5회
- 신호 감지: 변동률 2%, 쿨링 5분
- 스케줄: 07:00 리포트, 16:30 OHLCV, 17:00 학습, 월 06:00 PDF