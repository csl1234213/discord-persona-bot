"""All style/address/utterance writes validate authority and resource ownership here."""

from app.core.authorization import admin_required
from app.core.errors import AppError
from app.db.models import AddressRule, PersonalityProfile, StyleProfile, Utterance
from app.schemas.persona import PersonalityValues, UtteranceCreate
from app.services.persona_update_service import PersonaUpdateService


class PersonaConfigurationService:
    def __init__(self, db):
        self.db = db

    @admin_required
    async def update(self, persona_id, version_id, kind, values, *, record_id=None, guild_id=None):
        await PersonaUpdateService(self.db).require_draft(persona_id, version_id)
        if kind == "personality":
            if not values.keys() <= PersonalityValues.model_fields.keys():
                raise AppError("invalid_fields", "Unknown personality fields.")
            checked = PersonalityValues(**values).model_dump()
            values = {key: checked[key] for key in values}
            target = await self.db.get(PersonalityProfile, version_id)
        elif kind == "style":
            allowed = {
                "sentence_length_preference",
                "paragraph_length_preference",
                "emoji_policy",
                "opening_patterns",
            }
            if not values.keys() <= allowed:
                raise AppError("invalid_fields", "Unknown style fields.")
            for key, value in values.items():
                if key == "opening_patterns":
                    if (
                        not isinstance(value, list)
                        or len(value) > 20
                        or any(not isinstance(item, str) or len(item) > 400 for item in value)
                    ):
                        raise AppError("invalid_style", "Opening patterns exceed limits.")
                elif value not in {"short", "medium", "long", "never", "rare", "allowed"}:
                    raise AppError("invalid_style", "Unsupported style value.")
            target = await self.db.get(StyleProfile, version_id)
        elif kind == "utterance":
            if not values.keys() <= {"text", "context", "topic"}:
                raise AppError("invalid_fields", "Unknown utterance fields.")
            UtteranceCreate(**values)
            target = await self.db.get(Utterance, record_id)
        elif kind == "address":
            if not values.keys() <= {
                "address",
                "discord_user_id",
                "discord_role_id",
                "relationship",
                "priority",
            }:
                raise AppError("invalid_fields", "Unknown address fields.")
            for key, value in values.items():
                if key == "priority":
                    if not isinstance(value, int) or not -1000 <= value <= 1000:
                        raise AppError("invalid_address", "Invalid address priority.")
                elif value is not None and (not isinstance(value, str) or len(value) > 120):
                    raise AppError("invalid_address", "Address value exceeds limits.")
            if record_id is None:
                if self.db.info.get("admin_guild", guild_id) != guild_id:
                    raise AppError("forbidden", "Address guild ownership mismatch.")
                target = AddressRule(persona_version_id=version_id, guild_id=guild_id)
                self.db.add(target)
            else:
                target = await self.db.get(AddressRule, record_id)
        else:
            raise AppError("invalid_resource", "Unsupported resource type.")
        if target is None or target.persona_version_id != version_id:
            raise AppError("resource_not_found", "Resource does not belong to this version.")
        if (
            kind == "address"
            and self.db.info.get("admin_guild")
            and target.guild_id not in (None, self.db.info["admin_guild"])
        ):
            raise AppError("forbidden", "Address guild ownership mismatch.")
        for key, value in values.items():
            setattr(target, key, value)
        await self.db.commit()
        return target

    @admin_required
    async def delete_address(self, persona_id, version_id, record_id):
        await PersonaUpdateService(self.db).require_draft(persona_id, version_id)
        target = await self.db.get(AddressRule, record_id)
        if target is None or target.persona_version_id != version_id:
            raise AppError("resource_not_found", "Resource does not belong to this version.")
        if self.db.info.get("admin_guild") and target.guild_id not in (
            None,
            self.db.info["admin_guild"],
        ):
            raise AppError("forbidden", "Address guild ownership mismatch.")
        await self.db.delete(target)
        await self.db.commit()
