$content = @'

\# 자율 AI 퀀트 시스템 - 장기 개선 로드맵



> 최종 갱신: Session 39

> 기준 버전: V10 DDD 아키텍처

> 현재 진입점: python app/main.py

> 테스트 상태: 1095/1095 passed (재검증 필요)

> mypy strict 완료 모듈: 33개

> 전체 mypy 오류: 637 -> 약 574 예상 (검증 필요)



\---



\## Session 39 핵심 발견



data/dart\_connector.py와 infrastructure/dart/client.py는 CACHE\_FILE 경로

한 줄을 제외하고 내용이 완전히 동일한 중복 파일임을 확정. 두 파일 모두

라이브 코드(각각 weekly\_pdf.py, bootstrap.py가 사용). 정밀 검증 과정에서

두 가지 실제 mypy strict 위반 지점을 발견해 수정:

1\) \_load\_cache()의 len() 호출이 Optional\[Dict] 타입에 대해 \[Sized] 오류를

&#x20;  유발할 수 있어 len(x or {}) 가드 적용

2\) get\_company\_info\_sync()가 warn\_return\_any=true 설정 하에서 Any를

&#x20;  직접 반환해 \[no-any-return] 오류를 유발하므로 dict()로 감싸 방지

&#x20;  (search\_notices\_sync의 list() 래핑과 동일 원리, 결과는 얕은 복사본)



이번 세션에서는 타입 힌트만 추가하고 두 파일의 중복 구조 리팩터링은 보류.



\## 다음 우선순위 (Session 40\~)



1\. app/bootstrap.py mypy strict (이미 전체 내용 확보됨, 재요청 불필요,

&#x20;  단 1300줄 이상 대형 파일이므로 신중히 처리)

2\. core/exception\_handler.py + core/exceptions.py (소형, 각 2개 오류 추정)

3\. data/news\_crawler.py + infrastructure/news/crawler.py

&#x20;  (dart\_connector와 유사한 중복 쌍 가능성, 확인 필요)

4\. 전체 mypy 오류 500개 이하 달성 목표



이 로드맵은 살아있는 문서입니다.

'@

Set-Content -Path "ROADMAP.md" -Value $content -Encoding utf8



