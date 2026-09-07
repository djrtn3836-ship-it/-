$content = @'

\# 자율 AI 퀀트 시스템 - 장기 개선 로드맵



> 최종 갱신: Session 38

> 기준 버전: V10 DDD 아키텍처

> 현재 진입점: python app/main.py

> 테스트 상태: 1095/1095 passed (재검증 필요)

> mypy strict 완료 모듈: 31개



\---



\## dart\_connector.py / client.py 관련 확정 사항



data/dart\_connector.py와 infrastructure/dart/client.py는 둘 다 실제로

존재하는 파일이다. app/bootstrap.py는 try/except ImportError로 client.py를

우선 사용하지만, report/weekly\_pdf.py는 data.dart\_connector를 직접(무조건)

import하므로 두 파일 모두 라이브 코드이며 어느 쪽도 배제 대상이 아니다.

다음 세션에서 두 파일의 전체 내용을 확보해 순서대로 mypy strict를 적용한다.



\## Session 38 완료 작업



\- data/kiwoom\_connector.py: ConnectionClosed 예외 분기 보존 확인, mypy strict 완료

\- scanner/realtime\_monitor.py: UnboundLocalError 잠재 결함 수정, mypy strict 완료

\- feedback/feedback\_learner.py, report/weekly\_pdf.py: mypy strict 완료

\- pyproject.toml: strict 모듈 31개



\## 미해결 관찰 사항



Session 37에서 deep\_analyzer.py 오류가 0건이 되었음에도 "오류 있는 파일 수"가

79에서 그대로 유지된 현상 확인. strict 목록 확장이 다른 파일(예: bootstrap.py)에서

새로운 오류를 노출시켰을 가능성. 다음 세션에서 전체 그룹 통계로 원인 파일 확인 필요.



\## 다음 우선순위 (Session 39\~)



1\. data/dart\_connector.py + infrastructure/dart/client.py 전체 내용 확보 후 mypy strict 적용

2\. app/bootstrap.py: 이미 전체 내용 확보됨(재요청 불필요), 1300줄 이상 대형 파일이므로

&#x20;  신중하게 다음 세션에서 처리

3\. core/exception\_handler.py, core/exceptions.py, data/news\_crawler.py/

&#x20;  infrastructure/news/crawler.py 순차 정리



이 로드맵은 살아있는 문서입니다.

'@

Set-Content -Path "ROADMAP.md" -Value $content -Encoding utf8



