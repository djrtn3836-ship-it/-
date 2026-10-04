"""
config/secure_config.py - 환경 변수 암호화 로더 (선택 기능)

용도
----
`.env.encrypted`(Fernet 암호문)를 복호화해 프로세스 환경변수로 주입한다.
평소에는 `bootstrap`이 `.env`를 직접 읽으므로 **사용하지 않아도 된다.**

⚠️ 2026-10-01 수정(P8-6)
    기존 구현은 키가 없을 때 `input()`으로 물어봤다 → 무인(스케줄러/서비스) 실행에서
    **프로세스가 입력 대기로 멈추는 치명적 위험**이 있었다. 대화형 입력을 제거하고
    키가 없으면 명확한 오류로 즉시 실패하도록 변경했다.
    (감사에서 '고아 모듈'로 발견된 계기로 안전화)

사용 예::
    ENCRYPTION_KEY=<fernet-key> python -c "from config.secure_config import load_encrypted_env; load_encrypted_env()"
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


def load_encrypted_env(env_file: str = ".env.encrypted", key_env_var: str = "ENCRYPTION_KEY") -> None:
    project_root = Path(__file__).parent.parent
    encrypted_path = project_root / env_file
    env_path = project_root / ".env"

    if encrypted_path.exists() and CRYPTO_AVAILABLE:
        encryption_key = os.getenv(key_env_var)
        if not encryption_key:
            # 🔴 무인 실행 보호: 대화형 입력 금지(입력 대기로 프로세스 정지 방지)
            logger.error(
                f"🔑 {key_env_var} 환경 변수가 없어 암호화 .env를 복호화할 수 없습니다. "
                f"기존 .env(평문)를 사용하거나 {key_env_var}를 설정하세요."
            )
            return

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
