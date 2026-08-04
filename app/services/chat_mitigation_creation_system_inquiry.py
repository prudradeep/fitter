from app.services.chat_mitigation_creation_system_attributes import (
    ChatMitigationCreationSystemAttributesMixin,
)
from app.services.chat_mitigation_creation_system_flow import (
    ChatMitigationCreationSystemFlowMixin,
)
from app.services.chat_mitigation_creation_system_observations import (
    ChatMitigationCreationSystemObservationsMixin,
)


class ChatMitigationCreationSystemInquiryMixin(
    ChatMitigationCreationSystemFlowMixin,
    ChatMitigationCreationSystemObservationsMixin,
    ChatMitigationCreationSystemAttributesMixin,
):
    pass
