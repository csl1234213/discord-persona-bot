from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import GuildConfig


class GuildRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get(self, guild_id: str) -> GuildConfig | None:
        return await self.db.get(GuildConfig, guild_id)

    async def get_or_create(self, guild_id: str) -> GuildConfig:
        config = await self.get(guild_id)
        if config is None:
            config = GuildConfig(guild_id=guild_id)
            self.db.add(config)
            await self.db.flush()
        return config
