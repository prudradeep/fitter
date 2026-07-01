from app.llm import ask_llm_chat
from app.schemas import ChatResponse
from app.services.chat_formatters import format_all_dgs, normalize_markdown_text
from app.services.chat_options import normalize
from app.services.chat_parsers import is_llm_unavailable_response
from app.services.chat_session import ChatSession
from app.services.prompt_loader import render_prompt_template


class ChatAutoUserMixin:
    async def _auto_user_message_from_llm(
        self,
        session: ChatSession,
        current_response: ChatResponse,
        history: list[dict[str, str]],
    ) -> str:
        options = [option.label for option in current_response.options]
        other_options = list(current_response.other_options or [])
        field_mode = current_response.input_mode in {
            "mitigation_measure",
            "reason_evidence",
            "textarea",
            "evaluation_question",
            "mitigation_review",
        }
        prompt_options = [] if field_mode else options
        prompt_other_options = [] if field_mode else other_options
        mode_instruction = (
            "The current step expects typed field input. Do NOT choose an option or navigation action; "
            "write the field content the form expects."
            if field_mode
            else "The current step expects an option or short answer. Prefer primary options when available."
        )
        context = render_prompt_template(
            "llm/auto_user_message.txt",
            mode_instruction=mode_instruction,
            country=session.country or "Not selected",
            region=session.region or "Not selected",
            sector=session.sector or "Not selected",
            selected_hazard=(
                session.selected_hazard
                or session.accepted_custom_hazard
                or "Not selected"
            ),
            step=current_response.step,
            input_mode=current_response.input_mode,
        )
        recent_conversation = (
            "\n".join(
                f"{item['role']}: {normalize_markdown_text(item['content'])[:900]}"
                for item in history
            )
            or "- No prior messages."
        )
        messages = [
            {
                "role": "user",
                "content": render_prompt_template(
                    "llm/auto_user_message_user.txt",
                    recent_conversation=recent_conversation,
                    current_assistant_message=normalize_markdown_text(
                        current_response.bot_message
                    )[:1200]
                    or "- Empty.",
                    primary_options="\n".join(f"- {option}" for option in prompt_options)
                    or "- None",
                    other_options="\n".join(
                        f"- {option}" for option in prompt_other_options
                    )
                    or "- None",
                ),
            }
        ]
        response = await ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0.35,
            max_tokens=260,
        )
        if is_llm_unavailable_response(response):
            return ""
        return self._clean_auto_user_message(
            response,
            current_response.input_mode,
            options,
            other_options,
            session,
        )

    def _clean_auto_user_message(
        self,
        response: str,
        input_mode: str,
        options: list[str],
        other_options: list[str],
        session: ChatSession,
    ) -> str:
        cleaned = response.strip().strip("`").strip()
        if cleaned.casefold().startswith("user:"):
            cleaned = cleaned.split(":", 1)[1].strip()
        cleaned = self._strip_wrapping_quotes(cleaned)
        allowed = [*options, *other_options]
        for option in allowed:
            if normalize(cleaned) == normalize(option):
                fallback = self._auto_user_fallback_for_input_mode(input_mode, session)
                if fallback:
                    return fallback
                return option
        return cleaned[:2000]

    @staticmethod
    def _auto_user_fallback_for_input_mode(input_mode: str, session: ChatSession) -> str:
        hazard = session.selected_hazard or session.accepted_custom_hazard or "the selected hazard"
        dgs = format_all_dgs(session)
        if input_mode == "mitigation_measure":
            return (
                "Mitigation measure: Provide targeted subsidies and advisory support "
                f"so affected groups can adapt to {hazard} without bearing disproportionate costs."
            )
        if input_mode == "reason_evidence":
            return (
                "Reason: This measure reduces the negative impact by lowering upfront "
                f"costs and giving practical support to the affected groups: {dgs[:400]}."
            )
        if input_mode == "textarea":
            return (
                "The cost coverage applies to the affected target groups by paying "
                "or reimbursing upfront adaptation costs directly for them, with "
                "guidance and implementation support so they can use the measure in practice."
            )
        if input_mode == "evaluation_question":
            return (
                "Score: 7\n"
                "Reason: The mitigation is relevant and practical, though it may need stronger "
                "funding and monitoring to reach every affected group."
            )
        if input_mode == "mitigation_review":
            return "Move to next step"
        return ""
