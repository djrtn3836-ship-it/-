# 자율 AI 퀀트 시스템 - 장기 개선 로드맵

> 최종 갱신: Session 45
> 시스템 정체성: 텔레그램 알림 보조 집사 (자동매매 아님)
>   - 실시간 매수가/매도가/ATR 스탑로스를 텔레그램으로 알림
>   - 최종 매매 판단과 실행은 항상 사용자 본인
>   - OrderExecutor mode="paper" 하드코딩 (실제 주문 없음)

\---

## 📌 외부 프로젝트 참고 자산 (세션 간 기억용, Session 44에서 확립)

### \[최우선] SNOWBALL-master/src/market\_analyzer.py

위치: C:\\Users\\hdw38\\Desktop\\SNOWBALL-master\\src\\market\_analyzer.py
목적: 텔레그램 알림에 "리스크 스코어 72/100" 직관적 지표 추가
핵심:
@dataclass class MarketSignal:
risk\_score: float   # 0\~100 종합
atr\_score: float    # 0\~30
rsi\_score: float    # 0\~25
bb\_score: float     # 0\~25
volume\_score: float # 0\~20
이식: telegram\_sender.py \_format\_signal\_entry()에 삽입
주의: ATR 계산 기준이 다를 수 있음 -> kiwoom 기준으로 스케일링 재검토

### \[최우선] SNOWBALL-master/src/risk\_manager.py

위치: C:\\Users\\hdw38\\Desktop\\SNOWBALL-master\\src\\risk\_manager.py
목적: core/regime\_manager.py 국면 전환 휩쏘 방지
핵심: 확정 카운터 패턴 (raw 신호 N회 연속 동일해야 공식 전환)
confirm\_count: int = 0 / CONFIRM\_THRESHOLD: int = 3
이식: regime\_manager.py 레짐 전환 로직에 "연속 N회 확인 후 전환" 조건 추가
주의: 우리 시스템은 일봉 기반이라 N=1\~2로 낮춰도 됨

### \[선택] TradingAgents-main/tradingagents/agents/utils/rating.py

위치: C:\\Users\\hdw38\\Desktop\\TradingAgents-main\\tradingagents\\agents\\utils\\rating.py
목적: telegram\_sender.py \_infer\_advice\_action() 유니코드 견고성 강화
핵심: unicodedata.normalize("NFKC", text) 전처리 한 줄만 추가해도 충분

### \[선택] vibe-investing/kiwoom\_sdk/python/kiwoom\_sdk/skill/**init**.py

위치: C:\\Users\\hdw38\\Desktop\\vibe-investing\\kiwoom\_sdk\\python\\kiwoom\_sdk\\skill\_*init*\_.py
목적: telegram\_commands.py 자연어 인텐트 매핑 강화
핵심: INTENT\_KEYWORDS 정규식 패턴 (account\_query / stock\_search / place\_order)
주의: place\_order는 우리가 자동매매 안 하므로 "신호 조회" 인텐트로 의미 변경 필요

### \[체크리스트용] ZeroQuant/scripts/ml/feature\_engineering.py

위치: C:\\Users\\hdw38\\Desktop\\zeroquant-main\\scripts\\ml\\feature\_engineering.py
목적: signal\_pipeline.py 피처 커버리지 점검 (22개 피처 목록)
주의: 도입 아님, 비교 기준으로만 사용

\---

## Session 45 결과

* risk/var\_calculator.py: v2.0 -> v2.1, strict 8개 오류 전부 해결

  * dict -> Dict\[str, Any] 6건 (131/133/192/516/526/566번 줄)
  * no-any-return 1건 (519번, to\_dict() 반환 타입 확정으로 자동 해소)
  * unused-ignore 1건 (43번 줄) — ⚠️ 중요 정정 사항:
43번 줄은 numpy가 아니라 scipy 재할당 줄(\_scipy\_norm = None)임을
원본 코드 직접 대조로 확인. scipy 줄의 ignore만 제거하고,
numpy 줄(36번)의 ignore는 원본 그대로 유지해야 정확한 수정임
(numpy는 실제 설치된 타입 스텁 패키지라 재할당 시 진짜 오류 발생,
scipy는 미설치 시 ignore\_missing\_imports=true로 이미 Any 추론되어
재할당에 ignore 불필요 → "unused"로 지적됨)
* risk/portfolio\_var.py: v2.0 -> v2.1, strict 12개 오류 전부 해결
(Dict\[str,List\[float]]/Dict\[str,float] 파라미터 타입 명시,
\_get\_var\_calculator -> Any 반환 타입, \_fallback\_individual\_var 반환 타입 명시,
Dict\[str,Any] 반환 타입 6건)
* orchestrator/portfolio\_manager.py: 전이 오류 20개 자동 해소 -> Success

## 다음 우선순위 (Session 46\~)

### 1순위: report/telegram\_commands.py (난이도 높음)

처리 명령어:
python pack\_project.py report/telegram\_commands.py
mypy report/telegram\_commands.py --strict 2>\&1
예상 오류 패턴: python-telegram-bot의 Optional\[Message]/Optional\[Chat]
타입 관련 union-attr 오류 다수

### 2순위: app/bootstrap.py 전체 오류 재집계

Remove-Item -Recurse -Force .mypy\_cache -ErrorAction SilentlyContinue
mypy app/ --strict 2>\&1 | Select-String "error:" | Measure-Object |
Select-Object -ExpandProperty Count
(Session 43 기준 273개 -> 이번 세션 수정 반영 시 추가 감소 예상)

### 3순위: README.md/CONTEXT.md 정체성 정리

"Phase 2 Paper Trading", "실계좌 연동" 항목 삭제
-> "텔레그램 알림 보조 집사" 정의로 교체

## 세션 운영 규칙

* 파일은 한 번에 하나\~두 개씩만 요청/처리
* 외부 저장소는 20줄 헤더 스크리닝만 (README 전문 붙여넣기 지양)
* 수정본은 항상 Set-Content 파워셸 스크립트로 제공
* mypy 로그는 Select-String "error:"로 필터링해서 첨부
* 오류 개수 분해는 반드시 error: 줄만 직접 세어 검증 (note: 줄 제외)
* ⚠️ Session 45 교훈: 줄 번호 매핑 주장은 반드시 원본 코드 직접 카운트로
재검증할 것. AI가 제시한 줄 번호 표를 그대로 믿지 말고, 최소 2\~3개
지점을 샘플 검증해 방법론 신뢰도를 확인한 후 전체 적용

이 로드맵은 살아있는 문서입니다.



$roadmapContent = @'

\# 자율 AI 퀀트 시스템 - 장기 개선 로드맵



> 최종 갱신: Session 46 (본 대화 세션 전체 반영)

> 시스템 정체성: 텔레그램 알림 보조 집사 (자동매매 아님)

>   - 실시간 매수가/매도가/ATR 스탑로스를 텔레그램으로 알림

>   - 최종 매매 판단과 실행은 항상 사용자 본인

>   - OrderExecutor mode="paper" 하드코딩 (실제 주문 없음)



\---



\## 🚨 절대 가드레일 (모든 세션에서 반드시 준수)



1\. `infrastructure/kiwoom/` 폴더는 존재하지 않는 것으로 확인 완료.

&#x20;  → `data/kiwoom\_connector.py`, `scanner/realtime\_monitor.py`는 "레거시"가 아니라

&#x20;    V10 경로에서도 실제 사용되는 핵심 활성 코드. \*\*삭제 대상에서 절대 제외.\*\*

2\. `execution/order\_executor.py`의 `mode`는 항상 Paper로 하드코딩. 실주문 절대 발생 안 함.

3\. `core/container.py`의 `performance\_tracker.initialize()` 호출 절대 제거 금지

&#x20;  (제거 시 PerformanceTracker 영구 미시작 회귀 발생).

4\. 큰 파일 전체 교체 시 heredoc(`@'...'@`) 붙여넣기 중 내용 유실 위험 있음

&#x20;  → 빌드 직후 반드시 `($content -split "\\`n").Count`로 줄 수 검증 후 저장.

5\. `Set-Content -Encoding utf8`은 BOM을 삽입함 → 항상

&#x20;  `New-Object System.Text.UTF8Encoding($false)` + `\[System.IO.File]::WriteAllText`로 저장.

6\. mypy 오류 개수는 AI가 예측한 숫자를 그대로 믿지 말고 매번 직접 재실행하여 확인.



\---



\## ✅ 완료된 작업 (Session 46 전체)



\### P0 보안·정체성

\- `config/naver\_api\_cache.json`(네이버 API 키 평문 노출) 삭제 + `.gitignore` 등록 + `git rm --cached`

\- `infrastructure/kiwoom/` 부재 확인 → 가드레일 확정

\- `README.md`/`CONTEXT.md` 실행 명령어(`python app/main.py`) 및 정체성 문구 통일



\### P1 설정 정리

\- `pytest.ini` testpaths 통일 (`tests/unit` → `tests`)

\- `pyproject.toml` 중복 `\[\[tool.mypy.overrides]]` 블록 병합 (tomllib로 문법 검증 완료)



\### ROADMAP 외부 프로젝트 이식 (SNOWBALL 등)

\- `core/regime\_manager.py`: 확정 카운터 패턴(Whipsaw 방지, CONFIRM\_THRESHOLD=2) 적용, v1.3 완전 타입화

\- `report/telegram\_sender.py`: 리스크 스코어 지표(`\_calc\_risk\_score`, ATR/RSI/BB/Volume 4요소) 추가 및 알림 문구 연결 확인



\### P2 코드 정리 (오펀 파일 삭제)

\- `strategy/` 폴더(4개), `decision/explainer.py`, `decision/portfolio\_allocator.py`,

&#x20; `decision/timing\_manager.py`, `scanner/emergency\_recovery.py`, `data/base\_db.py`,

&#x20; `data/postgres\_db.py`, `analytics/phase\_transition\_validator.py`



\### 중대 사고 및 복구

\- `orchestrator/portfolio\_manager.py`: 손상 의심 → 직접 검증 결과 실제로는 정상(git 이력 6개 커밋은

&#x20; 모두 손상돼 있었으나 워킹트리는 무사했던 것으로 확인)

\- `core/supervisor.py`: heredoc 붙여넣기 실패로 빈 파일 생성됨 → `StreamWriter` 줄 단위 안전 재작성으로 복구, v1.3 완전 타입화

\- `analytics/` 패키지 7개 파일(`alert\_verifier`, `calibration\_analyzer`, `calibration\_executor`,

&#x20; `calibration\_tracker`, `daily\_monitor`, `performance\_tracker`, `shadow\_logger`) 우발적 삭제

&#x20; → `git restore analytics`로 즉시 복구

\- BOM/CRLF 오염 5개 파일(`README.md`, `CONTEXT.md`, `pytest.ini`, `regime\_manager.py`,

&#x20; `realtime\_monitor.py`) 정리



\### mypy strict 타입화 완료 (Success 확인, 파일별 최초 오류 수 기준)



| 파일 | 오류 수 | 상태 |

| :--- | :--- | :--- |

| `report/telegram\_commands.py` | 46 | ✅ (gather 언패킹 타입 명시로 잔여 has-type 4건도 해소) |

| `core/regime\_manager.py` | 16 | ✅ |

| `core/supervisor.py` | 11 | ✅ |

| `infrastructure/database/postgres\_manager.py` | 35 | ✅ |

| `execution/order\_executor.py` | 6 | ✅ (OrderMode\\|str 안전 변환 포함) |

| `risk/safety\_guard.py` | 7 | ✅ (지역변수 `triggered` 포함) |

| `core/natural\_language.py` | 9+3 | ✅ |

| `data/stock\_universe.py` | 3 | ✅ |

| `core/container.py` (자신) | 2 | ✅ |

| `infrastructure/cache/cached\_db\_manager.py` | 7 | ✅ |

| `application/analysis/strategy\_bandit.py` | 6 | ✅ |

| `application/analysis/bandit\_feedback\_bridge.py` | 5 | ✅ |

| `validation/execution\_simulator.py` | 4 | ✅ (이번 세션) |

| `core/sentiment\_analyzer.py` | 6 | ✅ (이번 세션) |

| `decision/hybrid\_decider.py` | 5 | ✅ (이번 세션, deep\_analyzer.py 연쇄 1건도 해소) |

| `infrastructure/cache/redis\_cache.py` | 2 | ✅ (이번 세션, 추정 수정 — 재검증 권장) |



\### 오류 수 궤적 (직접 실행 확인 기준)

\- `app/bootstrap.py --strict`: 273(Session 43) → 169 → 153 → 103 → 86 → 68 → \*\*예상 \~50\*\* (직접 확인 필요)

\- `core/container.py --strict`: 53 → 45 → 27 → \*\*예상 \~9\*\* (직접 확인 필요)

\- `pytest tests`: \*\*1104 passed\*\* 유지 중



\---



\## 🔄 다음 우선순위 (Session 47\~)



\### 1순위: analytics/performance\_tracker.py (추정 5건)

```powershell

python pack\_project.py analytics/performance\_tracker.py

mypy analytics/performance\_tracker.py --strict





$roadmapContent = @'

\# 자율 AI 퀀트 시스템 - 장기 개선 로드맵



> 최종 갱신: Session 46 (본 대화 세션 전체 반영, mypy 타입화 대장정 마무리 단계)

> 시스템 정체성: 텔레그램 알림 보조 집사 (자동매매 아님)

>   - 실시간 매수가/매도가/ATR 스탑로스를 텔레그램으로 알림

>   - 최종 매매 판단과 실행은 항상 사용자 본인

>   - OrderExecutor mode="paper" 하드코딩 (실제 주문 없음)



\---



\## 🚨 절대 가드레일 (모든 세션에서 반드시 준수)



1\. `infrastructure/kiwoom/` 폴더는 존재하지 않는 것으로 확인 완료.

&#x20;  → `data/kiwoom\_connector.py`, `scanner/realtime\_monitor.py`는 "레거시"가 아니라

&#x20;    V10 경로에서도 실제 사용되는 핵심 활성 코드. \*\*삭제 대상에서 절대 제외.\*\*

&#x20;  → 반면 `infrastructure/dart/client.py`, `infrastructure/news/crawler.py`는

&#x20;    실제로 존재하는 파일이므로 `data/dart\_connector.py`, `data/news\_crawler.py`와

&#x20;    타입 호환성 이슈가 실재함 (bootstrap.py 61/66번 줄 assignment 오류의 근본 원인).

2\. `execution/order\_executor.py`의 `mode`는 항상 Paper로 하드코딩. 실주문 절대 발생 안 함.

3\. `core/container.py`의 `performance\_tracker.initialize()` 호출 절대 제거 금지.

4\. 큰 파일 전체 교체 시 heredoc(`@'...'@`) 붙여넣기 중 내용 유실 위험 있음

&#x20;  → 빌드 직후 반드시 `($content -split "\\`n").Count`로 줄 수 검증 후 저장.

5\. 파일 쓰기는 항상 `New-Object System.Text.UTF8Encoding($false)` +

&#x20;  `\[System.IO.File]::WriteAllText`로 BOM 없이 저장.

6\. mypy 오류 개수/줄 번호는 AI의 추측을 믿지 말고 매번 직접 재실행하여 확인.

7\. "Returning Any" 오류가 `int()`/`float()` 캐스팅으로 해결되지 않을 경우,

&#x20;  변수가 `Any`로 고정 추론되었거나 데코레이터 영향일 가능성이 높음 →

&#x20;  `cast()`로 명시적 타입 단언 필요 (bandit\_feedback\_bridge.py에서 검증된 패턴).

8\. 문자열 치환이 반복 실패하는 파일은 추측 대신 mypy가 알려준 정확한 줄 번호를

&#x20;  배열 인덱스로 직접 참조해 확인 후 수정 (`Get-Content $path -Encoding UTF8`

&#x20;  결과의 `\[줄번호-1]` 인덱스 사용).



\---



\## ✅ 완료된 작업 (Session 46 전체)



\### P0 보안·정체성

\- `config/naver\_api\_cache.json`(API 키 평문 노출) 삭제 + `.gitignore` 등록

\- `README.md`/`CONTEXT.md` 실행 명령어 및 정체성 문구 통일



\### P1 설정 정리

\- `pytest.ini` testpaths 통일, `pyproject.toml` 중복 mypy override 블록 병합



\### ROADMAP 외부 프로젝트 이식

\- `core/regime\_manager.py`: Whipsaw 방지(확정 카운터) 적용, v1.3 완전 타입화

\- `report/telegram\_sender.py`: 리스크 스코어 지표 추가



\### P2 코드 정리 (오펀 파일 삭제, 8개 파일/폴더)



\### 중대 사고 및 복구

\- `core/supervisor.py` heredoc 손상 → StreamWriter 안전 재작성으로 복구

\- `analytics/` 패키지 7개 파일 우발적 삭제 → `git restore`로 복구

\- BOM/CRLF 오염 5개 파일 정리



\### mypy strict 타입화 완료 (Success 확인)

| 파일 | 상태 |

| :--- | :--- |

| `report/telegram\_commands.py` | ✅ |

| `core/regime\_manager.py` | ✅ |

| `core/supervisor.py` | ✅ |

| `infrastructure/database/postgres\_manager.py` | ✅ |

| `execution/order\_executor.py` | ✅ |

| `risk/safety\_guard.py` | ✅ |

| `core/natural\_language.py` | ✅ |

| `data/stock\_universe.py` | ✅ |

| `infrastructure/cache/cached\_db\_manager.py` | ✅ |

| `application/analysis/strategy\_bandit.py` | ✅ |

| `application/analysis/bandit\_feedback\_bridge.py` | ✅ (cast() 패턴 확립) |

| `core/sentiment\_analyzer.py` | ✅ |

| `decision/hybrid\_decider.py` | ✅ (deep\_analyzer.py 연쇄 해소) |

| `infrastructure/cache/redis\_cache.py` | 🔄 이번 세션 cast() 재적용 완료 (재검증 필요) |

| `validation/execution\_simulator.py` | 🔄 이번 세션 cast() 재적용 완료 (재검증 필요) |

| `app/bootstrap.py` 자체(61,66,71,76) | 🔄 근본 원인 규명 후 수정 완료 (재검증 필요) |

| `risk/portfolio\_var.py` / `var\_calculator.py` | ⚠️ 독스트링 주장 검증 대기 중 (이번 세션 실행) |



\### 오류 수 궤적

\- `app/bootstrap.py --strict`: 273 → 169 → 153 → 103 → 86 → 68 → 52 → \*\*재검증 필요\*\*

\- `core/container.py --strict`: 53 → 45 → 27 → 11 → \*\*재검증 필요\*\*

\- `pytest tests`: \*\*1104 passed\*\* 유지 중



\---



\## 🔄 다음 우선순위 (Session 47\~)



\### 1순위: 이번 세션 수정분 최종 확인

```powershell

mypy infrastructure/cache/redis\_cache.py --strict

mypy validation/execution\_simulator.py --strict

mypy app/bootstrap.py --strict 2>\&1 | Select-String "^app\\\\bootstrap.py"

mypy risk/portfolio\_var.py --strict

mypy risk/var\_calculator.py --strict





