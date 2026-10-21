# -*- coding: utf-8 -*-
import logging

from core import logger as lg


def test_env_level_applied_at_call_time(monkeypatch, tmp_path):
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    name = "test_env_level_unique"
    logger = lg.setup_logger(name, log_dir=str(tmp_path), console_output=False)
    assert logger.level == logging.INFO
    assert logger.propagate is False
    assert logger.handlers[0].maxBytes > 0
