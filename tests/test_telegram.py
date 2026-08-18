# test_telegram.py
import asyncio
from report.telegram_sender import TelegramSender

async def test():
    sender = TelegramSender()
    result = await sender.send_raw("🧪 Telegram 테스트 메시지입니다.")
    print(f"전송 결과: {result}")

if __name__ == "__main__":
    asyncio.run(test())