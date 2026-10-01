# tests/unit/test_config.py
"""
config 모듈 단위 테스트
"""

from core.config import get_config


class TestConfig:
    """ConfigManager 테스트"""

    def test_config_load(self, config):
        """설정 로드 테스트"""
        assert config is not None
        # 실제 사용 가능한 메서드만 테스트
        assert hasattr(config, "get_int") or hasattr(config, "get")

    def test_max_subscriptions(self, config):
        """max_subscriptions 설정 테스트"""
        max_subs = config.get_int("max_subscriptions", 500)
        assert isinstance(max_subs, int)
        assert max_subs > 0
        assert max_subs <= 1000

    def test_get_int(self, config):
        """get_int 메서드 테스트"""
        cooldown = config.get_int("cooldown_seconds", 300)
        assert isinstance(cooldown, int)
        assert cooldown >= 60

    def test_singleton_pattern(self):
        """싱글톤 패턴 검증"""
        cfg1 = get_config()
        cfg2 = get_config()
        assert cfg1 is cfg2
