"""일봉 수집 v2 회귀 테스트 (2026-10-12 야간 — 실측 사고 대응).

사고 3건:
① Kiwoom ka10060 cur_prc 부호 접두("-251000") → 하락 종목 close<=0
   → "비거래일"로 대량 오판 (전 종목 수집 실패로 발견)
② 이 응답엔 open/high/low/vol이 없어 저장돼도 0값 (가짜 OHLC)
③ '어제' 라벨 고정 → 월요일 수집이 일요일 라벨, yfinance 10-12 누락 시 무한 결손
→ v2: yfinance(검증된 백필 경로) 1차 + 실제 응답 날짜 저장 + Kiwoom은 수정 폴백.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

_ROOT = Path(__file__).resolve().parents[2]


class TestConnectorParsing:

    def _src(self, rel):
        return (_ROOT / rel).read_text(encoding="utf-8")

    def test_abs_price_parsing(self):
        src = self._src("data/kiwoom_connector.py")
        assert "_abs_price" in src, "부호 접두 제거가 사라짐(하락 종목 대량 누락 재발)"
        assert 'abs(float(str(v).replace("+"' in src

    def test_bar_date_from_response(self):
        src = self._src("data/kiwoom_connector.py")
        assert "_bar_date" in src and '"date": _bar_date' in src, "응답 날짜 신뢰가 제거됨"

    def test_dt_parameter(self):
        src = self._src("data/kiwoom_connector.py")
        assert "dt: Optional[str] = None" in src and "_dt = dt or" in src

    def test_ohlc_fallback_to_close_not_zero(self):
        src = self._src("data/kiwoom_connector.py")
        assert '_abs_price(record.get("open")) or _close' in src, "0값 OHLC 방지가 사라짐"


class TestCollectorV2:

    def test_uses_yfinance_primary(self):
        src = (_ROOT / "scheduler" / "daily_collector.py").read_text(encoding="utf-8")
        assert "OHLCVBackfiller" in src, "검증된 yfinance 경로가 1차가 아님"
        assert "COLLECT_PERIOD" in src

    def test_kiwoom_is_fallback_with_fixed_date(self):
        src = (_ROOT / "scheduler" / "daily_collector.py").read_text(encoding="utf-8")
        assert "fb.get(\"date\")" in src, "폴백 저장 시 응답 날짜를 쓰지 않음(오라벨 재발)"
        assert '"비거래일"' not in src, "빈 응답을 휴장으로 단정하는 문구가 남아 있음"

    def test_loud_failure_on_trading_day(self):
        src = (_ROOT / "scheduler" / "daily_collector.py").read_text(encoding="utf-8")
        assert "is_trading_day()" in src and "record_failure" in src, "거래일 대량 실패가 무음"


class TestSettleReady:

    def test_settle_requires_exit_row(self):
        src = (_ROOT / "analytics" / "paper_trader.py").read_text(encoding="utf-8")
        assert "idx + h >= len(rows)" in src, "출구 봉 없이 채점하면 미래 정보 사용"
