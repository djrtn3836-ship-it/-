"""
Configuration Manager v5.1.2
YAML/JSON 설정 파일 로드 및 관리, Config Versioning 지원
"""

import os
import yaml
import json
import hashlib
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class ConfigManager:
    """통합 설정 관리자 (Config Versioning 포함)"""
    
    _instance = None
    _config: Dict[str, Any] = {}
    _version_history: list = []
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._load_all()
        return cls._instance
    
    def _load_all(self):
        """모든 설정 파일 로드"""
        config_dir = Path(__file__).parent.parent / "config"
        
        # YAML 파일 로드
        for file in config_dir.glob("*.yaml"):
            key = file.stem
            with open(file, "r", encoding="utf-8") as f:
                self._config[key] = yaml.safe_load(f)
            
            # Config Versioning (Claude 피드백)
            self._record_version(key)
        
        # JSON 파일 로드
        for file in config_dir.glob("*.json"):
            key = file.stem
            with open(file, "r", encoding="utf-8") as f:
                self._config[key] = json.load(f)
            self._record_version(key)
        
        logger.info(f"Config loaded: {len(self._config)} sections")
    
    def _record_version(self, section: str):
        """설정 변경 이력 기록 (Claude 피드백)"""
        content = json.dumps(self._config.get(section, {}), sort_keys=True)
        hash_value = hashlib.md5(content.encode()).hexdigest()
        
        self._version_history.append({
            "section": section,
            "hash": hash_value,
            "timestamp": datetime.now().isoformat(),
            "version": self._config.get("system", {}).get("version", "unknown")
        })
    
    def get(self, section: str, key: str = None, default: Any = None) -> Any:
        """설정값 조회"""
        if section not in self._config:
            return default
        
        data = self._config[section]
        if key is None:
            return data
        return data.get(key, default)
    
    def get_version_history(self) -> list:
        """설정 변경 이력 반환"""
        return self._version_history
    
    def reload(self):
        """설정 리로드"""
        self._config = {}
        self._version_history = []
        self._load_all()
        logger.info("Config reloaded")