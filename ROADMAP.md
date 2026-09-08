$content = @'

\# 자율 AI 퀀트 시스템 - 장기 개선 로드맵



> 최종 갱신: Session 40

> 기준 버전: V10 DDD 아키텍처

> 현재 진입점: python app/main.py

> 테스트 상태: 1095/1095 passed (재검증 필요)

> mypy strict 완료 모듈: 37개

> 전체 mypy 오류: 572 -> 약 526 예상 (검증 필요)



\---



\## Session 40 핵심 성과



core/exception\_handler.py에서 실제 운영 버그를 발견하고 수정함:

setup\_global\_exception\_handler()가 원본 핸들러(sys.excepthook, loop의 기존

예외 핸들러)를 새 핸들러로 교체한 "이후"에 캡처하고 있어, restore\_exception\_handler()

호출 시 커스텀 핸들러를 자기 자신으로 재설정하는 무의미한 동작이 되고

진짜 원본으로는 절대 복원되지 않던 문제. 원본 값을 교체 전에 먼저

캡처하도록 순서를 수정하여 근본 해결.



core/exceptions.py의 handle\_exceptions 데코레이터는 Session 32에서 검증된

observability/tracer.py의 traced() 패턴(Callable\[..., Any] + 분기별 정의)을

동일하게 적용하여 코드베이스 타입 처리 방식의 일관성을 유지.



data/news\_crawler.py + infrastructure/news/crawler.py는 dart\_connector 쌍과

동일한 구조(모듈 독스트링만 다름)의 완전한 중복 파일임을 확정. 둘 다

라이브 코드이므로 모두 타입 힌트 적용 완료.



pyproject.toml: strict 모듈 33개 -> 37개.



\## 다음 우선순위 (Session 41\~)



1\. app/bootstrap.py mypy strict (이미 전체 내용 확보됨, 재요청 불필요,

&#x20;  단 1300줄 이상 대형 파일이므로 단독 세션으로 신중히 처리)

2\. mypy 전체 실행으로 파일별 그룹 통계 재확인 (526 예측치 검증)

3\. core/config.py, report/daily\_report.py, report/telegram\_commands.py 순차 공략

4\. 전체 mypy 오류 500개 이하 달성 목표



이 로드맵은 살아있는 문서입니다.

'@

Set-Content -Path "ROADMAP.md" -Value $content -Encoding utf8



