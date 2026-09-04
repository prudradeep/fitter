"""Composition layer for custom-hazard creation responsibilities."""

from app.services.chat_custom_hazard_evidence import ChatCustomHazardEvidenceMixin
from app.services.chat_custom_hazard_grounding import ChatCustomHazardGroundingMixin
from app.services.chat_custom_hazard_input import ChatCustomHazardInputMixin


class ChatCustomHazardCreationMixin(
    ChatCustomHazardGroundingMixin,
    ChatCustomHazardInputMixin,
    ChatCustomHazardEvidenceMixin,
):
    """Compose grounding, input/clarification, and evidence workflow behavior."""
