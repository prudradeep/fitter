"""Compatibility facade for the modular hazard-creation service mixins."""

from app.services.chat_custom_hazard_creation import ChatCustomHazardCreationMixin
from app.services.chat_hazard_catalog import ChatHazardCatalogMixin
from app.services.chat_hazard_generation import ChatHazardGenerationMixin
from app.services.chat_hazard_profiles import ChatHazardProfilesMixin


class ChatHazardCreationMixin(
    ChatCustomHazardCreationMixin,
    ChatHazardProfilesMixin,
    ChatHazardCatalogMixin,
    ChatHazardGenerationMixin,
):
    """Compose hazard creation, profiles, persistence, and generation behavior."""
