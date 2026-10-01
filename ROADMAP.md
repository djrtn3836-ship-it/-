# ROADMAP — stock_analyzer (V10)

> 한국 주식(KOSPI/KOSDAQ) 실시간 감시 + Telegram 알림 보조 시스템. 자동매매 아님.
> 최종 갱신: 2026-09-30 | 브랜치: `genspark_ai_developer` | 공식 진입점: `python app/main.py`

## 운영 원칙
- 승인 기반 개발: [분석·계획 보고 → 승인 → 수행]
- 기존 기능 보존: 삭제가 필요하면 영향도를 명시하고 승인 후 `_archive/`로 이동
- 모든 소스 파일은 **UTF-8 (BOM 없음)** 유지
- 각 작업 완료 시 `DEVELOPMENT_LOG.md` 최신화

---

## 단계 요약

| 단계 | 목표 | 상태 |
| :--- | :--- | :--- |
| P0 | 현재 상태 고정 (문서/인코딩/파일 정리) | 🔄 진행 중 |
| P1 | 핵심 경로 신뢰성 (알림 실제 도착 보장) | ⏳ 대기 |
| P2 | 관측성/디버깅 통합 (나비효과 추적) | ⏳ 대기 |
| P3 | 아키텍처 정리 (분해/정본화/계층 규칙) | ⏳ 대기 |
| P4 | 검증 인프라 (통합 테스트 + CI) | ⏳ 대기 |
| P5 | Paper Trading 안전 진입 | ⏳ 대기 |
| P6 | 고도화 (유니버스/거시/DB/대시보드) | ⏳ 대기 |

---

## P0. 현재 상태 고정
- [x] V10 기준 문서 재작성 (CONTEXT.md)
- [x] ROADMAP.md / DEVELOPMENT_LOG.md 신설
- [x] BOM(U+FEFF) 56개 파일 제거
- [x] A등급 스테일 산출물 정리
- [x] B등급 중복/고아 모듈 정본 확정 (의존성 0건 아카이브)
- [x] `python app/main.py` 부팅 검증 (실 자격증명 + TEST_MODE 양쪽 성공)
- 완료 기준: `git status` clean, 구문 오류 0, BOM 0, 문서-코드 일치
- 잔여: 테스트 결합 고아 모듈(`event_bus`/`feature_store`/`pipeline_manager`/관측성 5종) 배선 여부 결정

## P1. 핵심 경로 신뢰성
- [x] 신호 → Telegram 알림 E2E 실측 (부팅 알림 수신 확인)
- [x] `validation/backtester.py` 실구현 + Walk-Forward (배선: `backtest_runner` + `strategy_backtest`)
- [x] `analytics/performance_tracker.py` 실측값 검증 (임시 DB 시딩 테스트)
- [x] OHLCV 적재 경로 확보 (`scheduler/ohlcv_backfill.py`, 190종목 92,104행)
- [ ] ML 피처 학습/예측 정합성 재검증 (`feedback/feedback_learner.py`)
- [ ] 알림 누락 검증기(`analytics/alert_verifier.py`) 결과 신뢰성 확인
- 완료 기준: 하루치 신호가 알림 건수와 일치, 백테스트가 실제 DB 데이터로 산출

## P2. 관측성/디버깅 통합
- [ ] `observability/tracer` + `scripts/trace_ctl.py` ON/OFF 완성
- [ ] 파일별 독립 로그(콘솔 분리) 표준화
- [ ] 신규 파일 자동 계측(`TracedService`/`auto_trace_module`) 표준 확립
- [ ] 미배선 관측 모듈(explainer_v2/root_cause_analyzer/anomaly_detector/model_drift_detector) 배선 또는 보관 결정
- 완료 기준: 특정 오류 발생 시 어느 파일·줄에서 시작됐는지 로그로 추적 가능

## P3. 아키텍처 정리
- [ ] `scanner/deep_analyzer.py` 책임 분해 (신호/ATR/트레일링/합의/스코어링)
- [ ] 고아 모듈 정본화 및 계층 재배치
- [ ] 설정 단일화 (`config/schema.py` 기준)
- [ ] 계층 의존 규칙 자동 검증(import-linter) 도입
- 완료 기준: 500줄 초과 God 파일 0, 순환 참조 0

## P4. 검증 인프라
- [ ] `tests/run_all.py` 단일 실행기 (unit/integration/e2e)
- [ ] CI: mypy --strict + ruff + pytest
- 완료 기준: `python tests/run_all.py` 단일 명령으로 전체 통과

## P5. Paper Trading 안전 진입
- [ ] `execution/order_executor.py` 3중 안전장치(포지션/일일손실/중복주문) 검증
- [ ] Calibration(슬리피지 보정) 실측 연동
- [ ] Phase 전환 자동 검증 조건 확정
- ⛔ **게이트: OOS 검증 통과 필요** — 2026-09-30 검증 결과 현 파라미터/전략은 IS→OOS에서 성과 붕괴(과최적화)로 **보류**
- 전제: P1~P4 완료, OOS 샤프/승률/MDD 기준 충족

## P6. 고도화
- [x] 유니버스 파이프라인(`infrastructure/market_data/universe_provider.py`) 안정화 (2026-10-01)
      - 🔴 **`await` 누락 버그 수정**: `_check_universe_source()`가 무음 미실행 → 폴백 CRITICAL 알림이 죽어 있었음
        (CI에 'await 누락 탐지' + 회귀 테스트 추가)
      - **CSV 자동 갱신 스케줄**(토 09:00, `universe_refresh`, 잡 13개) — 그동안 수동 실행만 가능했음
        + 축소 방지 가드(신규 < 기존 90%면 덮어쓰지 않음)
      - **신선도 검사**: `UNIVERSE_MAX_AGE_DAYS`(기본 14일) 초과 시 `csv_stale` 판정 → 부팅 시 경고 알림
      - **시장 정보 보존**: KOSDAQ 종목이 전부 KOSPI로 표기되던 문제 수정 → KOSPI 217 / KOSDAQ 298
- [x] 거시 지표 확장 (해외지수/금리/원자재) — 2026-10-01
      - 신규: 니케이225 · 달러인덱스 · **US 2Y + 10Y−2Y 스프레드(역전 신호)** · 구리 · 금
      - `MacroFilter` 지표 8 → **13개**(가중치 합 1.0 유지)
      - 🔴 실데이터 버그 3건 동시 수정:
        `^KS200`이 1행만 반환 → **최고 가중치 KOSPI 지표가 무음 사망**(→ `^KS11` 1순위)
        KTB 3Y 네이버 엔드포인트 HTTP 410 → 값이 3.0에 **고정**(→ FRED 폴백, 실측 4.286)
        US 2Y로 13주물(`^IRX`)을 쓰던 오류(→ FRED `DGS2` 1순위)
- [x] PostgreSQL 마이그레이션 **검토 완료 → 전환 보류** (2026-10-01)
      - 근거 문서: `docs/postgres_migration_assessment.md`
      - 실측: SQLite 공개 메서드 28개 중 **8개가 PG 미구현**(서킷브레이커 거래량·모멘텀 리포트 등
        프로덕션 경로가 런타임 AttributeError) + `momentum_paper` 테이블 스키마 부재
      - 현 규모(DB 29MB·최대 23만행·단일 프로세스)는 SQLite로 충분 → 이득 없음
      - 재검토 트리거: decisions 100만행 / DB 5GB / 다중 writer / 외부 동시질의
      - 재발 방지: `test_db_interface_parity.py`(격차 변동 시 CI 실패) + 기동 시 ERROR 경고
- [ ] 대시보드/리포트 고도화

---

## P7. 알파 검증 결과와 전환 (2026-10-01 완료)

### 검증 결과 요약 (P5/P6)
| 단계 | 결론 |
| :--- | :--- |
| P5 알파/베타 분해 | 모멘텀 α(연) −4.8%, β 1.22, R² 0.81 → **엣지 아님(베타)** |
| P5 벤치마크 비교 | 단순 보유(동일가중 유니버스)가 **모든 구간에서 우위** (Sharpe 1.23 vs 0.94) |
| P5 전략 스캔 | 모멘텀/단기반전/저변동성/52주고가 **모두 |t| < 2** |
| P6 롱숏(시장중립) | 베타 제거 시 Sharpe −0.72~+0.41 → **어떤 가격 팩터도 유의 알파 없음** |

→ **② 모멘텀 실거래 승격 = 부결** (참고 신호로만 유지, 리포트에 α 없음 명시)

### 차기 경로 결정
- **미개척 경로**: 가격 외 정보(감성/공시/ML)를 횡단면 팩터로 투입
- **선행 조건(블로커)**: 아래 데이터가 **축적되어야** 백테스트 가능
  | 데이터 | 현재 | 필요 |
  | :--- | :--- | :--- |
  | `decisions` | **0행** | 수백 행 이상 |
  | `decision_outcomes` | **0행** | 결과 라벨 축적 |
  | ML 모델(`models/`) | 없음 | 학습 데이터 확보 후 |
  | `ohlcv` | 230,441행 ✅ | – |

### P7 작업 항목
- [x] **데이터 축적 게이트** (`scheduler/data_readiness_monitor.py`)
      - decisions 500 / outcomes 300 기준 준비도 판정, 주 1회(일 09:00) 점검
      - 임계 **최초 도달 시 1회만** 텔레그램 알림(상태 파일로 중복 방지)
- [x] **감시·알림 가치 강화** (P6 결론: 시스템 가치는 알파가 아니라 감시·리스크 알림)
      - `OpsMonitor` 임계/윈도우 **환경변수로 튜닝**
        (`OPS_ANOMALY_THRESHOLD/WINDOW`, `OPS_DRIFT_WINDOW`, `OPS_ALERT_COOLDOWN_SEC`)
      - `quality_snapshot()`: 이상감지율·알림률·억제율·오류율 + 개선 힌트
- [ ] (보류) 롱숏 전략: 한국 개인 공매도 제약 → 진단 도구로만 사용
