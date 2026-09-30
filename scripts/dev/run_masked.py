# scripts/dev/run_masked.py
# app/main.py 를 실행하되, .env 의 시크릿 값을 stdout/stderr 에서 마스킹한다.
# 용도: 자격증명을 화면/로그에 노출하지 않고 실 부팅을 관찰.
# 사용: python scripts/dev/run_masked.py
import os
import subprocess
import sys

from dotenv import load_dotenv

SECRET_KEYS = [
    "KIWOOM_APP_KEY",
    "KIWOOM_APP_SECRET",
    "KIWOOM_API_KEY",
    "KIWOOM_SECRET_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "TELEGRAM_ADMIN_ID",
    "TELEGRAM_ALERT_GROUP_ID",
    "DART_API_KEY",
    "NAVER_CLIENT_ID",
    "NAVER_CLIENT_SECRET",
]


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    load_dotenv(".env", override=True)
    secrets = []
    for key in SECRET_KEYS:
        value = os.getenv(key)
        if value and len(value) >= 6:
            secrets.append(value)
    secrets.sort(key=len, reverse=True)

    proc = subprocess.Popen(
        [sys.executable, "app/main.py"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            for secret in secrets:
                line = line.replace(secret, "***MASKED***")
            sys.stdout.write(line)
            sys.stdout.flush()
    except KeyboardInterrupt:
        proc.terminate()
    return proc.wait()


if __name__ == "__main__":
    raise SystemExit(main())
