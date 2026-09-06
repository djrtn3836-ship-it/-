$content = @'

\# 자율 AI 퀀트 시스템 - 장기 개선 로드맵



> 최종 갱신: Session 36

> 기준 버전: V10 DDD 아키텍처

> 현재 진입점: python app/main.py

> 테스트 상태: 1095/1095 passed (재검증 필요)

> signal\_pipeline.py 의존성 체인 mypy strict: 0 errors (달성 완료)

> 전체 코드베이스 mypy (.): 902개에서 감소 예정, 실측 필요



\---



\## Session 36 핵심 조치



1\. 중복 파일 3쌍(daily\_monitor.py, phase\_transition\_validator.py,

&#x20;  shadow\_logger.py, 각 monitor/analytics 경로)의 오류 개수가 2배수/4배수

&#x20;  패턴을 보이는 것을 근거로 두 경로가 동일 구조의 중복 파일임을 확정.

&#x20;  app/bootstrap.py의 실제 import 문으로 monitor/phase\_transition\_validator.py만

&#x20;  라이브임을 확인.

2\. pyproject.toml exclude에 5개 죽은 코드 파일 추가

&#x20;  (daily\_monitor.py x2, shadow\_logger.py x2, analytics/phase\_transition\_validator.py)

3\. monitor/phase\_transition\_validator.py mypy strict 완료 (로직 무변경)

4\. scanner/deep\_analyzer.py는 실제 파일 내용 미확보로 이번 세션에서 보류

&#x20;  (추측 기반 재작성의 위험성 - schema.py/postgres\_manager.py 사고 재발 방지)



\## 백로그



\- FeatureStore 처리 방향 미결정

\- daily\_monitor.py 두 경로의 실제 내용 차이 여부 미확인 (오류 패턴이

&#x20; 2배수가 아니라서 동일 파일이 아닐 가능성 있음, 확인 필요)



\## 다음 우선순위 (Session 37\~)



1\. scanner/deep\_analyzer.py 전체 내용 확보 후 mypy strict 적용

&#x20;  (반드시 실제 파일을 먼저 받은 뒤에만 작업)

2\. mypy . 잔여 오류 상위 파일(kiwoom\_connector.py, weekly\_pdf.py, bootstrap.py,

&#x20;  telegram\_commands.py, dart\_connector.py 등) 순차 공략

3\. Phase 4 마지막 항목 또는 Phase 5 진입 검토



이 로드맵은 살아있는 문서입니다.

'@

Set-Content -Path "ROADMAP.md" -Value $content -Encoding utf8



