# Session 57: 메서드 시그니처 분석 보고서

## 1. RealisticExecutionSimulator
- execute_order: 실제 파라미터 및 반환값 확인 필요
- apply_slippage: 슬리피지 계산
- calculate_transaction_cost: 거래 비용 계산
- get_execution_report: 실행 리포트 조회
- reset: 시뮬레이터 리셋

## 2. Backtester
- add_trade: Trade 추가
- calculate_pnl: 손익 계산
- get_statistics: 통계 조회
- run_backtest: 백테스트 실행
- trades: Trade 목록

## 3. RealtimeMonitor
- add_tick: Tick 데이터 추가
- detect_imbalance: 불균형 감지
- get_statistics: 통계 조회
- calculate_imbalance: 불균형 계산
- tickers: 모니터링 대상 티커

## 다음 단계:
1. 실제 메서드 호출 테스트
2. 테스트 코드 수정
3. skip 마크 제거
4. 커버리지 측정