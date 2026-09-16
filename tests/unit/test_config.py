# tests/unit/test_config.py
\"\"\"
config 모듈 단위 테스트
\"\"\"

import pytest
from core.config import get_config, ConfigManager


class TestConfig:
    \"\"\"ConfigManager 테스트\"\"\"

    def test_config_load(self, config):
        \"\"\"설정 로드 테스트\"\"\"
        assert config is not None
        assert hasattr(config, 'get_int')
        assert hasattr(config, 'get_string')
        assert hasattr(config, 'get_float')

    def test_max_subscriptions(self, config):
        \"\"\"max_subscriptions 설정 테스트\"\"\"
        max_subs = config.get_int('max_subscriptions', 500)
        assert isinstance(max_subs, int)
        assert max_subs > 0
        assert max_subs <= 1000

    def test_get_int(self, config):
        \"\"\"get_int 메서드 테스트\"\"\"
        cooldown = config.get_int('cooldown_seconds', 300)
        assert isinstance(cooldown, int)
        assert cooldown >= 60

    def test_get_float(self, config):
        \"\"\"get_float 메서드 테스트\"\"\"
        price_change = config.get_float('price_change_ratio', 0.02)
        assert isinstance(price_change, float)
        assert 0.0 <= price_change <= 1.0

    def test_singleton_pattern(self):
        \"\"\"싱글톤 패턴 검증\"\"\"
        cfg1 = get_config()
        cfg2 = get_config()
        assert cfg1 is cfg2
