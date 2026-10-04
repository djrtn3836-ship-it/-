# ROADMAP — stock_analyzer (V10)

> 📅 전면 재작성: 2026-10-01 | 이전 단계(P0~P7) 완료 후 **감사 기반** 신규 로드맵
> 🔎 감사 방법: ① 미완성 마커(TODO/FIXME/NotImplementedError) ② 빈 함수(pass) ③ 고아 모듈(참조 0)
> ④ 무테스트 모듈 전수 스캔 → `audit_report.txt`, `audit2.txt`

---

## 0. 완료 이력 요약 (P0~P7)

| 단계 | 내용 | 결과 |
| :--- | :--- | :--- |
| P0 | 인코딩/스테일 산출물/고아 모듈 정리, 기준 문서 신설 | ✅ |
| P1 | 핵심 경로 신뢰성(env 별칭, Kiwoom 재시도, PID 가드, 동적 유니버스) | ✅ |
| P2 | 관측성/백테스트 인프라, OOS 검증 → **가격기반 엣지 없음** | ✅ |
| P3 | 아키텍처 정리(죽은 플래그, 고아 배선, PG 검토) | ✅ |
| P4 | ruff F 게이트, 뭉개진 파일 3개 복원, `await` 누락 검출 | ✅ |
| P5 | 알파/베타 분해 → 모멘텀은 **베타**(α −4.8%, t −0.74) | ✅ |
| P6 | 롱숏 진단 → **어떤 팩터도 유의 알파 없음** | ✅ |
| P7 | 데이터 축적 게이트 + 알림 품질 지표 | ✅ |
| 고도화 | 유니버스 안정화·거시 13지표·PG 전환 보류·HTML 대시보드 | ✅ |

> 결론(고정): **시스템 가치 = 감시·리스크 알림.** 알파 생성은 비가격 팩터 + 데이터 축적 이후 과제.

---

## 1. 감사 결과 (2026-10-01)

### 1-1. 미완성 마커
- TODO/FIXME/XXX/HACK: **0건** (테스트 파일 제외)
- `NotImplementedError`: 0건 (테스트의 except 절만 존재)
→ 소스에 "미완성 표시"는 없음. 문제는 **표시 없이 배선되지 않은 모듈**이다.

### 1-2. 빈 함수(pass/…)
| 파일 | 함수 | 판정 |
| :--- | :--- | :--- |
| `domain/strategies/base.py` | `name()`, `weight()`, `analyze()` | 추상 메서드 — **정상 패턴** (ABC화 검토) |
| `tests/test_load.py` | `register_realtime()` | 테스트 스텁 — 허용 |

### 1-3. 고아 모듈 (프로덕션 참조 0)
| 모듈 | 줄 수 | 판정 대상 |
| :--- | :--- | :--- |
| `risk/correlation_matrix.py` | 388 | 🎯 **배선**(집중도 리스크) |
| `application/analysis/shadow_mode.py` | 422 | 🎯 **배선**(전략 실시간 평가) |
| `analytics/calibration_tracker.py` | 208 | 🎯 **배선**(ML 신뢰도 캘리브레이션) |
| `observability/trace_tree.py` | 218 | 🎯 **배선**(의사결정 경로 시각화) |
| `domain/models/market_tick.py` | 89 | 배선 또는 보관 결정 |
| `domain/models/position.py` | 169 | 배선 또는 보관 결정 |
| `config/secure_config.py` | 58 | 보관/제거 결정 |
| `scheduler/ohlcv_backfill.py` | – | CLI 도구(의도적) — 문서화만 |
| `validation/backtest_sweep.py` | – | CLI 도구(의도적) — 문서화만 |
| `scripts/dev/*` | – | 개발 도구(의도적) — 유지 |

### 1-4. 테스트 커버리지 공백 (이름조차 언급 없는 모듈 38개 중 핵심)
`core/supervisor.py` · `risk/safety_guard.py` · `core/regime_manager.py` ·
`decision/hybrid_decider.py` · `report/telegram_commands.py` ·
`monitor/phase_transition_validator.py` · `report/daily_report.py` ·
`report/weekly_pdf.py` · `scheduler/daily_collector.py` · `core/exception_handler.py` ·
`regime/regime_detector.py` · `data/stock_universe.py` · `infrastructure/dart/client.py` ·
`infrastructure/news/crawler.py` · `observability/auto_trace.py` · `collector/collector_status.py`

---

## 2. 신규 로드맵

### P8. 고아 모듈 처리 — 배선 / 보관 결정
- [x] **P8-1** `risk/correlation_matrix.py` 배선 — `risk/correlation_monitor.py` 신설 (2026-10-01)
      - 유동성 상위 50종목 · 60거래일 상관행렬 → 분산화 점수 + 고상관(|ρ|≥0.8) 쌍 탐지
      - 🔴 결측일 종목이 **위치 기반 정렬**로 가짜 상관(ρ 0.95)을 만들던 문제 → 공통 거래일(교집합) 정렬로 수정
      - 평일 17:00 스케줄(`correlation_check`) + 대시보드 7번 섹션 + 고상관 5쌍 이상 시 경고(6h 쿨다운)
      - 실측: 30종목 분산도 0.68, 고상관 7쌍(005930↔000660 ρ 0.938 등)
- [x] **P8-2** `application/analysis/shadow_mode.py` 배선 — `application/analysis/shadow_registry.py` 신설 (2026-10-01)
      - `SignalPipeline.process()` 훅 → 프로덕션 시그널 vs 실험 전략 시그널 실시간 비교
      - 기본 실험 전략 `low_vol_120d`(P5에서 유일한 양의 OOS 알파 후보) 자동 등록
      - 기록은 JSONL(`logs/shadow_records.jsonl`) — DB/PG 계약 불변 유지
      - 안전: 실패/타임아웃은 `ShadowRecord.error`로 흡수, **주문 없음**, 대시보드 8번 섹션
- [x] **P8-3** `analytics/calibration_tracker.py` 배선 — `analytics/calibration_bridge.py` 신설 (2026-10-01)
      - **결과 라벨 부재 문제 해결**: 매매 대신 N거래일(5일) 후 실현가로 승/패 채점
        (BUY=상승, SELL=하락, HOLD=보합 — 임계 ±0.5%)
      - `SignalPipeline` 예측 기록 훅 → 평일 16:00 `calibration_settle` 잡(16잡) → ECE 산출 + AB 피드백
      - 트래커가 메모리 기반이라 `settled.jsonl`에서 매번 재구성(멱등) / 대시보드 9번 섹션
- [x] **P8-4** `observability/trace_tree.py` 노출 — `observability/trace_bridge.py` 신설 (2026-10-01)
      - 전역 TraceTree 싱글턴 + `record_stage()` 헬퍼(실패 무시)
      - `SignalPipeline.process()`가 1단계(입출력/소요시간/성공여부) 기록
      - 텔레그램 `/trace [trace_id]` 명령 + 대시보드 10번 섹션
- [x] **P8-5** 도메인 모델 처리 (2026-10-01)
      - `market_tick.py` → **배선**: `RealtimeMonitor._on_data()`가 V10 도메인 모델로 검증·정규화.
        무효 틱(0가/음수/비정상 코드)은 조용히 통과하지 않고 **카운트 후 거부**(이력 오염 방지)
      - `position.py` → **예약 보관**: Phase 1은 매매가 없어 사용처 없음. Phase 2(실거래) 대기 도메인 모델
- [x] **P8-6** `config/secure_config.py` — 보관 + **안전화** (2026-10-01)
      - 🔴 무인 실행 위험 제거: 키 미설정 시 `input()` 대기 → 프로세스 정지 가능했음.
        대화형 입력 제거하고 명확한 ERROR 후 즉시 반환(AST 테스트로 재발 차단)
      - 용도 문서화: `.env.encrypted` 선택 기능, 평소엔 bootstrap이 `.env` 직접 사용

### P9. 테스트 커버리지 보강 (무검증 핵심 모듈)
- [x] **P9-1** 안전 계층 테스트 (2026-10-01) — `test_safety_layer.py` 19개
      - `safety_guard`: 방향성(급락=음수), 타당범위 방어선 **전 조건 완비성**, 쿨다운, 차단→해제
      - `supervisor`: **장중 사망 시 재시작 보류**(수동 개입 요청) / 장외 자동 재시작
- [x] **P9-2** 판단 계층 테스트 (2026-10-01) — `test_decision_layer.py` 19개
      - `hybrid_decider`: 강신호 매수, **손절 < 진입 < 익절** 불변식, 리스크 시 수량 축소
      - `regime_detector`: 레짐 결정성, 정규화 경계, 한국 특수요인(만기/배당/프로그램/외인비율)
- [x] **P9-3** 알림 계층 테스트 (2026-10-01) — `test_notification_layer.py` 17개
      - `telegram_commands`: **import 검증(P8-4 SyntaxError 사고 재발 방지)**, `/trace` 경로,
        권한 가드, 미인식 문장 안내
      - `daily_report`: 빈 입력 진단, 리스크/액션 목록, 산출 경로
- [x] **P9-4** 수집 계층 테스트 (2026-10-01) — `test_infra_layer.py`(수집 파트 9개)
      - `collector_status`: 싱글턴, **3회 연속 실패 시 비정상**(단발 오탐 방지), 성공 시 회복
- [x] **P9-5** 관측 계층 테스트 (2026-10-01) — `test_infra_layer.py`(관측 파트 11개)
      - `exception_handler`: 핸들러 설치/복원, **이벤트 루프 없는 동기 컨텍스트 안전성**
      - `auto_trace` / `trace_config`: 상속 자동 추적, 데코레이터 동작 보존, 전역 토글

### P10. 운영·관측 고도화
- [x] **P10-1** 대시보드 확장 (2026-10-01, P8 배선에 포함)
      - 10개 섹션: 시스템·데이터축적·감시알림·모멘텀모의·매크로·검증결론·**집중도·섀도우·캘리브레이션·트레이스**
- [x] **P10-2** 알림 품질 기반 임계 **제안** (2026-10-01)
      - `OpsMonitor.suggest_tuning()`: 억제율>50% → 쿨다운 단축, 감지율>10% → 임계 상향,
        감지 0 & 관측>500 → 하향(미탐), 오류율>1% → 입력 점검
      - **자동 적용 금지**(`applied: False`) — 운영자 승인 기반. 대시보드 3번 섹션에 표시
- [x] **P10-3** 주간 PDF에 검증 상태 섹션 추가 (2026-10-01)
      - "11. 전략 검증 상태": P5/P6 결론(α −4.8%, 시장 베타, 감시 가치) + P7 게이트 진행률
      - '전략이 검증됐다'는 오해 방지 문구 고정
- [x] **P10-4** README 운영 문서 최신화 (2026-10-01)
      - CLI 도구 5종(상관/캘리브레이션/섀도우/대시보드/트레이스), 스케줄러 **16잡** 명시
      - 알려진 한계 정정: 모멘텀=베타, 롱숏 알파 부재, PG 보류, 데이터 게이트 선행 조건
      - 텔레그램 `/trace` 명령, 테스트 수(1513) 갱신

### P11. 데이터 축적 이후 (게이트 도달 시 자동 트리거)
- [ ] **P11-1** 비가격 팩터(ML/감성/공시) 횡단면 검증
- [ ] **P11-2** 유의 전략 발견 시 Paper → 소액 실거래 승격 절차 수립
- [ ] **P11-3** PostgreSQL 재검토(트리거 충족 시, `docs/postgres_migration_assessment.md`)

---

## 3. 운영 원칙 (불변)
- 승인 기반 개발 / 기존 기능 보존(삭제 시 `_archive/` 이동) / UTF-8 (BOM 없음)
- 변경마다 `pytest` + BOM·뭉개짐·ruff F 검사 → TEST_MODE 부팅 확인
- 커밋 메시지에 검증 결과 명시, `CONTEXT.md`·`ROADMAP.md`·`DEVELOPMENT_LOG.md` 최신화
