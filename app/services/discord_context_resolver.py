from collections.abc import Iterable

from app.db.models import AddressRule
from app.schemas.chat import DiscordContext

RELATIONSHIP_DEFAULTS = {
    "close_friend": "朋友",
    "family": "家人",
    "colleague": "同事",
    "student": "同学",
}


class DiscordContextResolver:
    def resolve_address(
        self,
        *,
        context: DiscordContext,
        rules: Iterable[AddressRule],
        persona_fallback: str | None = None,
    ) -> tuple[str, str]:
        if context.preferred_address and context.preferred_address.strip():
            return context.preferred_address.strip(), "session_preferred"
        enabled = [rule for rule in rules if rule.enabled is not False]
        user_rules = [
            rule
            for rule in enabled
            if rule.discord_user_id == context.discord_user_id
            and (rule.guild_id is None or rule.guild_id == context.guild_id)
        ]
        if user_rules:
            rule = max(user_rules, key=lambda item: (item.priority, str(item.id)))
            return rule.address, "discord_user_rule"
        role_rules = [
            rule
            for rule in enabled
            if rule.discord_role_id in context.role_ids
            and (rule.guild_id is None or rule.guild_id == context.guild_id)
        ]
        if role_rules:
            rule = max(role_rules, key=lambda item: (item.priority, str(item.id)))
            return rule.address, "discord_role_rule"
        relationship_rules = [
            rule
            for rule in enabled
            if rule.discord_user_id is None
            and rule.discord_role_id is None
            and (rule.relationship is None or rule.relationship == context.relationship)
            and (rule.audience_type is None or rule.audience_type == context.audience_type)
        ]
        if relationship_rules:
            rule = max(relationship_rules, key=lambda item: (item.priority, str(item.id)))
            return rule.address, "relationship_rule"
        if context.relationship in RELATIONSHIP_DEFAULTS:
            return RELATIONSHIP_DEFAULTS[context.relationship], "relationship_default"
        if persona_fallback:
            return persona_fallback, "persona_fallback"
        return "你", "global_fallback"
