# PostgreSQL 마이그레이션 검토 (P6-3)

> 검토일: 2026-10-01 | 결론: **전환 보류** (조건 충족 시 재검토)

## 1. 검토 배경

로드맵 P6 "고도화" 항목 중 하나로, SQLite → PostgreSQL 전환 가능성을 검토했다.
`DATABASE_URL` 환경변수가 설정되면 `PostgresManager`가 자동 활성화되는 구조라,
전환 비용이 낮아 보였으나 **실측 결과 그렇지 않다**.

## 2. 실측 근거

### 2-1. 인터페이스 격차 (핵심 리스크)

`DatabaseManager`(SQLite)의 공개 메서드 28개 중 **8개가 `PostgresManager`에 미구현**:

| 미구현 메서드 | 영향받는 프로덕션 경로 | 심각도 |
| :--- | :--- | :--- |
| `get_daily_total_volume` | `risk/market_risk_monitor.py` — 서킷브레이커 거래량 입력 | 🔴 즉시 중단 |
| `get_ohlcv_range` | `scheduler/momentum_report.py`, `validation/backtest_runner.py`, `CachedDbManager` | 🔴 즉시 중단 |
| `save_momentum_picks` | `scheduler/momentum_report.py` — 모의 추적 기록 | 🔴 즉시 중단 |
| `get_momentum_paper_pending` | `scheduler/momentum_report.py` — 평가 대상 조회 | 🔴 즉시 중단 |
| `get_momentum_paper_stats` | 모멘텀 리포트 누적 성과 | 🟠 기능 상실 |
| `get_latest_momentum_paper_return` | 모멘텀 리포트 요약 | 🟠 기능 상실 |
| `update_momentum_paper_result` | 모의 추적 결과 확정 | 🟠 기능 상실 |
| `analyze_db` | 내부 통계 갱신 | 🟡 경미 |

또한 `PostgresManager._create_schema()`가 만드는 테이블은 6개로,
**`momentum_paper` 테이블이 스키마에 없다**.

> 호출 시 `AttributeError`로 **런타임에 터진다** — 기동은 성공하므로
> "정상 부팅 후 특정 스케줄에서만 실패"하는 가장 찾기 어려운 유형이다.
> (P6-1에서 발견한 `await` 누락과 같은 계열의 무음 실패)

### 2-2. 데이터 규모 — SQLite로 충분

| 항목 | 실측 |
| :--- | :--- |
| DB 파일 크기 | `decisions.db` **29 MB** |
| 최대 테이블 | `ohlcv` **230,441행** |
| 트랜잭션성 테이블 | `decisions` 0행, `decision_outcomes` 0행 |
| 동시 접속 | 단일 프로세스(`app/main.py`) + WAL |

PostgreSQL이 필요한 수준(다중 writer, GB 단위, 동시 대시보드 질의)이 **아니다**.

### 2-3. 전환 시 추가 작업량

1. 미구현 8개 메서드 구현 + `momentum_paper` 스키마 이식
2. SQLite 전용 구문 24곳 다이얼렉트 변환
   (`INSERT OR REPLACE` 6, `ON CONFLICT` 18, `PRAGMA` 3, `datetime('now')` 2, `AUTOINCREMENT` 5)
3. 기존 29 MB 데이터 마이그레이션 + 검증 스크립트
4. `asyncpg` 운영 의존성 및 장애 대응 절차 문서화

## 3. 결론 및 결정

**전환 보류.** 근거:
- 얻는 것: 없음(현 규모에서 성능/동시성 이점 없음)
- 잃는 것: 검증된 SQLite 경로를 미검증 PG 경로로 교체 → **무음 실패 위험 증가**
- 비용: 미구현 8개 + 다이얼렉트 24곳 + 마이그레이션/검증

## 4. 재검토 트리거 (아래 중 하나라도 충족 시)

- [ ] `decisions`/`decision_outcomes` 누적이 **100만 행** 초과
- [ ] DB 파일 크기 **5 GB** 초과 또는 단일 쿼리 5초 초과
- [ ] **다중 프로세스** writer 필요 (예: 별도 대시보드가 쓰기 수행)
- [ ] 외부 시스템(BI/분석)의 동시 질의 요구 발생

## 5. 재발 방지 장치 (이번 검토에서 구현)

격차가 **무음으로 커지지 않도록** 두 가지를 상시 검사한다:

1. `tests/unit/test_db_interface_parity.py`
   - 미구현 목록이 위 표와 **정확히 일치**하지 않으면 CI 실패
   - SQLite에만 새 메서드를 추가하면 즉시 차단
   - 미구현을 해소해도 실패 → 문서 갱신 강제
2. `core/container.py` 기동 경고
   - 실제로 PG가 활성화되면 미구현 목록을 `ERROR` 레벨로 출력
   - "부팅은 되는데 특정 스케줄에서만 터지는" 상황 사전 차단

## 6. 부록 — 전환을 실제로 수행할 때의 순서

1. `momentum_paper` 포함 스키마 이식 + 미구현 8개 구현
2. 파리티 테스트 통과(`KNOWN_GAPS == set()`) 및 문서 갱신
3. 데이터 이행 스크립트 + 행수/최신일자 대조 검증
4. **읽기 전용 이중 운영**(SQLite 쓰기 + PG 읽기 검증) 1~2주
5. 쓰기 전환 → 롤백 리허설(`DATABASE_URL` 제거로 즉시 SQLite 복귀 확인)
