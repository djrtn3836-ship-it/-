# test_telegram.py
import sys
from pathlib import Path

# 프로젝트 루트를 PYTHONPATH에 추가
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import asyncio

from report.telegram_sender import TelegramSender


async def test():
    sender = TelegramSender()
    result = await sender.send_raw("🧪 Telegram 테스트 메시지입니다.")
    print(f"전송 결과: {result}")


if __name__ == "__main__":
    asyncio.run(test())
