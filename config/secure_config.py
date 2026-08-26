"""
config/secure_config.py - 환경 변수 암호화 로더 (D)
"""

import os
from pathlib import Path

from dotenv import load_dotenv

from core.logger import setup_logger

logger = setup_logger("secure_config")

try:
    from cryptography.fernet import Fernet

    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False
    logger.warning("⚠️ cryptography 패키지 미설치 → 암호화 비활성화 (pip install cryptography)")


def load_encrypted_env(env_file=".env.encrypted", key_env_var="ENCRYPTION_KEY"):
    project_root = Path(__file__).parent.parent
    encrypted_path = project_root / env_file
    env_path = project_root / ".env"

    if encrypted_path.exists() and CRYPTO_AVAILABLE:
        encryption_key = os.getenv(key_env_var)
        if not encryption_key:
            logger.warning(f"🔑 {key_env_var} 환경 변수가 없습니다. 수동 입력 필요.")
            try:
                encryption_key = input("🔑 암호화 키를 입력하세요: ").strip()
            except:
                encryption_key = None

        if encryption_key:
            try:
                f = Fernet(encryption_key.encode())
                with open(encrypted_path, "rb") as f_enc:
                    encrypted_data = f_enc.read()
                decrypted_data = f.decrypt(encrypted_data).decode()
                for line in decrypted_data.splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    key, value = line.split("=", 1)
                    os.environ[key] = value
                logger.info(f"✅ 암호화된 환경 변수 로드 완료 ({env_file})")
                return
            except Exception as e:
                logger.error(f"❌ 암호 해독 실패: {e} → .env로 폴백")

    if env_path.exists():
        load_dotenv(env_path)
        logger.info("✅ 일반 환경 변수 로드 완료 (.env)")
    else:
        logger.warning("⚠️ .env 파일을 찾을 수 없습니다.")
