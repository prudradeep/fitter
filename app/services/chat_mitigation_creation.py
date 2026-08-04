from app.llm import ask_llm_chat  # noqa: F401
from app.services.chat_mitigation_creation_common import (
    _d23_conceptual_review_page_texts_impl,
)
from app.services.chat_mitigation_creation_evaluation import (
    ChatMitigationCreationEvaluationMixin,
)
from app.services.chat_mitigation_creation_implementation import (
    ChatMitigationCreationImplementationMixin,
)
from app.services.chat_mitigation_creation_policy import (
    ChatMitigationCreationPolicyMixin,
)
from app.services.chat_mitigation_creation_storage import (
    ChatMitigationCreationStorageMixin,
)
from app.services.chat_mitigation_creation_system_inquiry import (
    ChatMitigationCreationSystemInquiryMixin,
)
from app.services.chat_mitigation_creation_workflow import (
    ChatMitigationCreationWorkflowMixin,
)


def _d23_conceptual_review_page_texts() -> tuple[tuple[int, str], ...]:
    return _d23_conceptual_review_page_texts_impl()


class ChatMitigationCreationMixin(
    ChatMitigationCreationWorkflowMixin,
    ChatMitigationCreationImplementationMixin,
    ChatMitigationCreationEvaluationMixin,
    ChatMitigationCreationSystemInquiryMixin,
    ChatMitigationCreationStorageMixin,
    ChatMitigationCreationPolicyMixin,
):
    pass
