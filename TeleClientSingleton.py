import asyncio
from telethon import TelegramClient
from Config import Config


class TeleClientSingleton:
    _instance: TelegramClient | None = None
    _lock = asyncio.Lock()
    # One download at a time: shared MTProto client; overlapping GetFile/download_media
    # causes errors and flood-wait churn if the user triggers another publish while one runs.
    download_lock = asyncio.Lock()

    @classmethod
    async def get_client(cls) -> TelegramClient:
        if cls._instance and cls._instance.is_connected():
            return cls._instance

        async with cls._lock:
            if cls._instance is None:
                cls._instance = TelegramClient(
                    session="tele_client",
                    api_id=Config.API_ID,
                    api_hash=Config.API_HASH,
                )
            if not cls._instance.is_connected():
                await cls._instance.start(bot_token=Config.BOT_TOKEN)
        return cls._instance
