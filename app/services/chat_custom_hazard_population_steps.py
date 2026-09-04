import html
import logging
import re

from sqlalchemy import delete, select

from app.llm import ask_llm_chat
from app.models import QuestionOption, Region, UserHazardSocioDemographic, UserQuestionResponse
from app.schemas import ChatResponse
from app.services.chat_formatters import (
    evidence_for_display,
    evidence_is_provided,
    normalize_markdown_text,
)
from app.services.chat_json import parse_json_object
from app.services.chat_options import (
    CUSTOM_HAZARD_FINAL_OPTIONS,
    HAZARD_POPULATION_REVIEW_OPTIONS,
    exact_option_label,
    normalize,
    option_list,
)
from app.services.chat_parsers import is_llm_unavailable_response
from app.services.chat_population_edits import (
    clean_population_edit_items,
    fallback_population_edits,
)
from app.services.chat_session import ChatSession
from app.services.custom_hazard_validation import (
    build_custom_hazard_grounding_status,
    custom_hazard_validation_details,
    frontend_custom_hazard_payload,
    normalize_custom_group,
)
from app.services.custom_hazard_state_machine import transition_custom_hazard
from app.services.enums import ChatPhase, CustomHazardAction, CustomHazardStatus
from app.services.knowledge_base import VALIDATED_EVIDENCE_SCOPE
from app.services.message_renderer import render_message
from app.services.prompt_loader import load_nested_prompt_file, render_prompt_template

logger = logging.getLogger(__name__)


class ChatCustomHazardPopulationStepsMixin:
    def _custom_hazard_population_review_step(
        self,
        session_id: str,
        session: ChatSession,
        error_reason: str | None = None,
    ) -> ChatResponse:
        hazard = session.accepted_custom_hazard or "the new hazard"
        generated_title = str(session.generated_custom_hazard_title or "").strip()
        if isinstance(session.custom_hazard, dict):
            state = self._custom_hazard_state(session)
            generated_title = generated_title or str(
                state.get("generated_title") or ""
            ).strip()
            groups = state.get("affected_groups") or []
            profiles = [
                {
                    "name": self._clean_affected_group_label(str(group.get("group") or "")),
                    "profile": self._clean_affected_group_label(str(group.get("group") or "")),
                    "explanation": str(group.get("reason") or "").strip(),
                    "source": str(group.get("source") or "custom_hazard_grounding").strip(),
                }
                for group in groups
                if isinstance(group, dict)
                and self._clean_affected_group_label(str(group.get("group") or ""))
            ]
        else:
            profiles = self._stored_hazard_profiles(session, hazard)
        session.pending_affected_population_profiles = [dict(profile) for profile in profiles]
        transition_custom_hazard(
            session,
            ChatPhase.CUSTOM_HAZARD_GROUP_REVIEW
            if isinstance(session.custom_hazard, dict)
            else ChatPhase.CUSTOM_HAZARD_POPULATION_REVIEW,
        )
        message = render_message(
            "hazard_population_review.md",
            hazard=hazard,
            generated_title=generated_title,
            profiles=self._format_population_profiles_for_review(profiles),
            error_reason=error_reason or "",
            visibility_notice=self._crowd_sourcing_visibility_notice(
                session,
                "hazard",
            ),
        )
        if generated_title and "Generated title" not in message:
            generated_line = (
                "<p><strong>Generated title:</strong> "
                f"{html.escape(generated_title)}</p>"
            )
            updated_message = re.sub(
                r"(Hazard to be co-created:</p>\s*<ul>.*?</ul>)",
                rf"\1{generated_line}",
                message,
                count=1,
                flags=re.DOTALL,
            )
            message = (
                updated_message
                if updated_message != message
                else f"{message}{generated_line}"
            )
        if isinstance(session.custom_hazard, dict):
            return self._custom_hazard_response(
                session_id=session_id,
                session=session,
                step="custom_hazard_group_review",
                bot_message=message,
                options=HAZARD_POPULATION_REVIEW_OPTIONS,
                error=bool(error_reason),
            )
        return ChatResponse(
            session_id=session_id,
            step="custom_hazard_population_review",
            bot_message=message,
            options=HAZARD_POPULATION_REVIEW_OPTIONS,
            session=session.summary(),
            error=bool(error_reason),
        )

    async def _handle_custom_hazard_population_review(
        self, session_id: str, session: ChatSession, message: str
    ) -> ChatResponse:
        exact_label = exact_option_label(message, HAZARD_POPULATION_REVIEW_OPTIONS)
        action = normalize(exact_label or message)
        if isinstance(session.custom_hazard, dict):
            state = self._custom_hazard_state(session)
            if session.phase == "custom_hazard_profile_reason":
                pending_group = str(state.get("pending_profile_reason_group") or "").strip()
                reason = message.strip()
                if not pending_group or not reason:
                    return self._custom_hazard_response(
                        session_id=session_id,
                        session=session,
                        step="custom_hazard_profile_reason",
                        bot_message=f"How does this hazard affect '{pending_group or 'this group'}'?",
                        options=[],
                        input_mode="textarea",
                        error=not bool(reason),
                    )
                pending_group = self._clean_affected_group_label(pending_group)
                reason_review = await self._validate_custom_affected_group_reason(
                    session,
                    pending_group,
                    reason,
                )
                if reason_review is not None and not reason_review["valid"]:
                    return self._custom_hazard_response(
                        session_id=session_id,
                        session=session,
                        step="custom_hazard_profile_reason",
                        bot_message=(
                            f"How does this hazard affect '{pending_group}'?\n\n"
                            f"{reason_review['reason']}"
                        ),
                        options=[],
                        input_mode="textarea",
                        error=True,
                    )
                added = list(state.get("added_affected_groups") or [])
                added_group = normalize_custom_group(pending_group, reason)
                added.append(added_group)
                state["added_affected_groups"] = added
                groups = list(state.get("affected_groups") or [])
                groups.append(added_group)
                state["affected_groups"] = groups
                pending_queue = [
                    self._clean_affected_group_label(str(group))
                    for group in list(state.get("pending_profile_reason_queue") or [])
                    if self._clean_affected_group_label(str(group))
                ]
                if pending_queue:
                    next_group = pending_queue.pop(0)
                    state["pending_profile_reason_group"] = next_group
                    state["pending_profile_reason_queue"] = pending_queue
                    return self._custom_hazard_response(
                        session_id=session_id,
                        session=session,
                        step="custom_hazard_profile_reason",
                        bot_message=f"How does this hazard affect '{next_group}'?",
                        options=[],
                        input_mode="textarea",
                        error=False,
                    )
                state["pending_profile_reason_group"] = ""
                state["pending_profile_reason_queue"] = []
                transition_custom_hazard(session, ChatPhase.CUSTOM_HAZARD_GROUP_REVIEW)
                return self._custom_hazard_population_review_step(session_id, session)

            if action in {
                normalize("Confirm affected groups"),
                normalize("Continue"),
                normalize("Looks good"),
                normalize("Done"),
            }:
                if not list(state.get("affected_groups") or []):
                    state["confirmed_affected_groups"] = []
                    return self._custom_hazard_population_review_step(
                        session_id,
                        session,
                        error_reason=(
                            "No affected groups are selected. Add an affected group before confirming, "
                            "or edit the hazard clarification so affected groups can be extracted."
                        ),
                    )
                state["confirmed_affected_groups"] = list(state.get("affected_groups") or [])
                state["status"] = CustomHazardStatus.READY.value
                state["next_action"] = CustomHazardAction.VALIDATE.value
                return await self._route_custom_hazard_next_action(session_id, session)

            conversational_edits = self._parse_custom_affected_group_edit_message(message)
            remove_items = conversational_edits.get("remove", [])
            add_items = conversational_edits.get("add", [])
            if remove_items or add_items:
                for group in add_items:
                    group_error = self._custom_affected_group_label_error(group)
                    if group_error:
                        return self._custom_hazard_population_review_step(
                            session_id,
                            session,
                            error_reason=group_error,
                        )
                for target in remove_items:
                    removal_error = self._remove_custom_affected_group(state, target)
                    if removal_error:
                        return self._custom_hazard_population_review_step(
                            session_id,
                            session,
                            error_reason=removal_error,
                        )
                if add_items:
                    state["pending_profile_reason_group"] = add_items[0]
                    state["pending_profile_reason_queue"] = add_items[1:]
                    state["awaiting_group_add"] = False
                    transition_custom_hazard(session, ChatPhase.CUSTOM_HAZARD_PROFILE_REASON)
                    return self._custom_hazard_response(
                        session_id=session_id,
                        session=session,
                        step="custom_hazard_profile_reason",
                        bot_message=f"How does this hazard affect '{add_items[0]}'?",
                        options=[],
                        input_mode="textarea",
                        error=False,
                    )
                return self._custom_hazard_population_review_step(session_id, session)

            is_add_group_action = action == normalize("Add affected group") or re.match(
                r"^add(?: (?:affected )?group)?\b\s*:?",
                message.strip(),
                flags=re.IGNORECASE,
            )
            if is_add_group_action:
                group = re.sub(
                    r"^(add affected group|add group|add)\s*:?",
                    "",
                    message.strip(),
                    flags=re.IGNORECASE,
                ).strip()
                group = self._clean_affected_group_label(group)
                if not group or normalize(group) == normalize("Add affected group"):
                    state["awaiting_group_add"] = True
                    return self._custom_hazard_response(
                        session_id=session_id,
                        session=session,
                        step="custom_hazard_group_review",
                        bot_message="Which affected group should I add?",
                        options=HAZARD_POPULATION_REVIEW_OPTIONS,
                        input_mode="textarea",
                        error=False,
                    )
                group_error = self._custom_affected_group_label_error(group)
                if group_error:
                    return self._custom_hazard_population_review_step(
                        session_id,
                        session,
                        error_reason=group_error,
                    )
                state["pending_profile_reason_group"] = group
                state["awaiting_group_add"] = False
                transition_custom_hazard(session, ChatPhase.CUSTOM_HAZARD_PROFILE_REASON)
                return self._custom_hazard_response(
                    session_id=session_id,
                    session=session,
                    step="custom_hazard_profile_reason",
                    bot_message=f"How does this hazard affect '{group}'?",
                    options=[],
                    input_mode="textarea",
                    error=False,
                )

            is_remove_group_action = action == normalize("Remove affected group") or re.match(
                r"^remove (?:affected )?group\s*:",
                message.strip(),
                flags=re.IGNORECASE,
            )
            if is_remove_group_action:
                target = re.sub(
                    r"^(remove affected group|remove group)\s*:?",
                    "",
                    message.strip(),
                    flags=re.IGNORECASE,
                ).strip()
                if not target or normalize(target) == normalize("Remove affected group"):
                    return self._custom_hazard_response(
                        session_id=session_id,
                        session=session,
                        step="custom_hazard_group_review",
                        bot_message="Which affected group should I remove?",
                        options=HAZARD_POPULATION_REVIEW_OPTIONS,
                        input_mode="textarea",
                        error=False,
                    )
                removal_error = self._remove_custom_affected_group(state, target)
                return self._custom_hazard_population_review_step(
                    session_id,
                    session,
                    error_reason=removal_error,
                )

            if action == normalize("Edit group reason"):
                return self._custom_hazard_response(
                    session_id=session_id,
                    session=session,
                    step="custom_hazard_group_review",
                    bot_message="Tell me the affected group and the revised reason, for example: `low-income households: higher upfront retrofit costs`.",
                    options=HAZARD_POPULATION_REVIEW_OPTIONS,
                    input_mode="textarea",
                    error=False,
                )

            if ":" in message:
                group_label, reason = [part.strip() for part in message.split(":", 1)]
                groups = []
                updated = False
                for group in state.get("affected_groups") or []:
                    if isinstance(group, dict) and self._profiles_are_similar(
                        str(group.get("group") or ""),
                        group_label,
                    ):
                        next_group = dict(group)
                        next_group["reason"] = reason
                        next_group["needs_review"] = False
                        groups.append(next_group)
                        updated = True
                    else:
                        groups.append(group)
                state["affected_groups"] = groups
                return self._custom_hazard_population_review_step(
                    session_id,
                    session,
                    None if updated else "I could not find that affected group to edit.",
                )

            if state.get("awaiting_group_add"):
                group = message.strip()
                if group:
                    group = self._clean_affected_group_label(group)
                    group_error = self._custom_affected_group_label_error(group)
                    if group_error:
                        return self._custom_hazard_population_review_step(
                            session_id,
                            session,
                            error_reason=group_error,
                        )
                    state["pending_profile_reason_group"] = group
                    state["awaiting_group_add"] = False
                    transition_custom_hazard(session, ChatPhase.CUSTOM_HAZARD_PROFILE_REASON)
                    return self._custom_hazard_response(
                        session_id=session_id,
                        session=session,
                        step="custom_hazard_profile_reason",
                        bot_message=f"How does this hazard affect '{group}'?",
                        options=[],
                        input_mode="textarea",
                        error=False,
                    )

            if self._custom_affected_group_matches(state, message):
                removal_error = self._remove_custom_affected_group(state, message)
                return self._custom_hazard_population_review_step(
                    session_id,
                    session,
                    error_reason=removal_error,
                )

        if action in {
            normalize("Continue"),
            normalize("Looks good"),
            normalize("Done"),
            normalize("Confirm affected groups"),
        }:
            return await self._custom_hazard_added_step(session_id, session)

        edits = await self._extract_affected_population_edits(session, message)
        remove_items = edits.get("remove", [])
        add_items = edits.get("add", [])
        if not remove_items and not add_items:
            return self._custom_hazard_population_review_step(
                session_id,
                session,
                error_reason=(
                    "Tell me which affected groups to add or remove, or choose Continue."
                ),
            )

        hazard = session.accepted_custom_hazard or "New hazard"
        profiles = [dict(profile) for profile in self._stored_hazard_profiles(session, hazard)]
        if remove_items:
            profiles = [
                profile
                for profile in profiles
                if not any(
                    self._profiles_are_similar(
                        str(profile.get("name") or profile.get("profile") or ""),
                        item,
                    )
                    for item in remove_items
                )
            ]
        for item in add_items:
            label = re.sub(r"\s+", " ", normalize_markdown_text(item)).strip("`*_ #.-")
            if not label:
                continue
            group_error = self._custom_affected_group_label_error(label)
            if group_error:
                return self._custom_hazard_population_review_step(
                    session_id,
                    session,
                    error_reason=group_error,
                )
            if any(
                self._profiles_are_similar(
                    label,
                    str(profile.get("name") or profile.get("profile") or ""),
                )
                for profile in profiles
            ):
                continue
            profiles.append(
                {
                    "name": label[:120],
                    "profile": label[:120],
                    "variable_name": "user_review_affected_population",
                    "explanation": "Affected population group added during user review.",
                    "statistical_basis": "User-entered affected population review.",
                    "source": "user_review",
                }
            )

        if not profiles:
            if remove_items and not add_items:
                if session.hazard_profiles is None:
                    session.hazard_profiles = {}
                session.hazard_profiles[hazard] = []
                session.socio_demographic_profiles = []
                return self._custom_hazard_population_review_step(
                    session_id,
                    session,
                    error_reason=(
                        "No affected groups remain. Add an affected group before confirming, "
                        "or continue editing the affected group list."
                    ),
                )
            return self._custom_hazard_population_review_step(
                session_id,
                session,
                error_reason="At least one affected population group is required.",
            )

        if session.hazard_profiles is None:
            session.hazard_profiles = {}
        profiles = self._attach_target_population_matches_to_profiles(profiles)
        session.hazard_profiles[hazard] = profiles
        session.socio_demographic_profiles = [
            str(profile.get("name") or profile.get("profile") or "").strip()
            for profile in profiles
            if str(profile.get("name") or profile.get("profile") or "").strip()
        ]
        self._record_activity(
            session_id,
            session,
            "affected_population_profiles_reviewed",
            message,
        )
        return self._custom_hazard_population_review_step(session_id, session)

    async def _extract_affected_population_edits(
        self, session: ChatSession, message: str
    ) -> dict[str, list[str]]:
        current = [
            str(profile.get("name") or profile.get("profile") or "").strip()
            for profile in self._stored_hazard_profiles(
                session,
                session.accepted_custom_hazard or "New hazard",
            )
            if str(profile.get("name") or profile.get("profile") or "").strip()
        ]
        context = load_nested_prompt_file("llm/affected_population_edits.txt")
        response = await ask_llm_chat(
            context=context,
            messages=[
                {
                    "role": "user",
                    "content": render_prompt_template(
                        "llm/affected_population_edits_user.txt",
                        current_groups="\n".join(f"- {item}" for item in current)
                        or "- None",
                        message=message,
                    ),
                }
            ],
            temperature=0,
            max_tokens=220,
        )
        if not is_llm_unavailable_response(response):
            parsed = parse_json_object(response) or {}
            if isinstance(parsed, dict):
                add = self._clean_population_edit_items(parsed.get("add"))
                remove = self._clean_population_edit_items(parsed.get("remove"))
                if add or remove:
                    return {"add": add, "remove": remove}
        return self._fallback_population_edits(message)

    @staticmethod
    def _clean_population_edit_items(value: object) -> list[str]:
        return clean_population_edit_items(value)

    @staticmethod
    def _fallback_population_edits(message: str) -> dict[str, list[str]]:
        return fallback_population_edits(message)

    @staticmethod
    def _format_population_profiles_for_review(profiles: list[dict[str, str]]) -> str:
        lines: list[str] = []
        for profile in profiles:
            name = str(profile.get("name") or profile.get("profile") or "").strip()
            explanation = str(profile.get("explanation") or "").strip()
            if not name:
                continue
            line = f"- **{name}**"
            if explanation:
                line += f": {explanation}"
            lines.append(line)
        return "\n".join(lines) or "- No affected population groups identified yet."

    def _format_population_profiles_for_final(
        self,
        profiles: list[dict[str, str]],
    ) -> str:
        rows = self._hazard_profile_table_rows(profiles)
        if not rows:
            return self._format_population_profiles_for_review(profiles)
        return self._hazard_profile_table_html(
            rows,
            show_admin_details=self._show_profile_admin_details(),
        )

    def _population_comparison_regions(self, session: ChatSession) -> list[Region]:
        if session.country_id is None:
            return []
        regions = self.db.scalars(
            select(Region)
            .where(Region.country_id == session.country_id)
            .order_by(Region.name)
        ).all()
        return [
            region
            for region in regions
            if str(region.id) != str(session.region_id)
            and normalize(str(region.name)) != normalize(str(session.region or ""))
        ]

    def _custom_hazard_population_region_comparison_step(
        self,
        session_id: str,
        session: ChatSession,
        *,
        error_reason: str = "",
    ) -> ChatResponse:
        regions = self._population_comparison_regions(session)
        if not regions:
            session.phase = ChatPhase.HAZARDS.value
            return ChatResponse(
                session_id=session_id,
                step=ChatPhase.HAZARDS.value,
                bot_message=(
                    "No other regions are available for comparison within "
                    f"**{session.country or 'the selected country'}**."
                ),
                options=CUSTOM_HAZARD_FINAL_OPTIONS,
                session=session.summary(),
                error=True,
            )
        session.phase = ChatPhase.HAZARD_POPULATION_REGION_COMPARISON.value
        message = (
            f"## Compare regional affected population\n\n"
            f"Current region: **{session.region or 'Selected region'}**\n\n"
            f"Select another region in **{session.country or 'the selected country'}** "
            "to compare affected population percentages."
        )
        if error_reason:
            message = f"**{error_reason}**\n\n{message}"
        return ChatResponse(
            session_id=session_id,
            step=ChatPhase.HAZARD_POPULATION_REGION_COMPARISON.value,
            bot_message=message,
            options=option_list(regions),
            session=session.summary(),
            error=bool(error_reason),
        )

    async def _profiles_for_population_region_comparison(
        self,
        session: ChatSession,
        hazard: str,
        profiles: list[dict[str, str]],
        region: Region,
    ) -> list[dict[str, str]]:
        compared_profiles: list[dict[str, str]] = []
        for profile in profiles:
            updated = dict(profile)
            lookup_labels = [
                str(label).strip()
                for label in self._list_from_profile_or_metadata(
                    profile,
                    "population_lookup_labels",
                )
                if str(label).strip()
            ]
            if not lookup_labels:
                lookup_labels = self._additional_profile_population_lookup_labels(profile)
            population_values: list[dict[str, object]] = []
            for label in lookup_labels:
                try:
                    prevalence = await self.eurostat.get_prevalence(
                        label,
                        country_code=str(session.country or ""),
                        nuts_code=str(region.name),
                        sector=str(session.sector or ""),
                        hazard=hazard,
                        confirmed_predictor_category=label,
                    )
                except Exception:
                    logger.exception(
                        "Failed to fetch affected population for comparison region"
                    )
                    continue
                if prevalence is not None:
                    population_values.append(
                        {
                            "population_pct": prevalence.get("population_pct"),
                            "national_population_pct": prevalence.get(
                                "national_population_pct"
                            ),
                        }
                    )
            percentages = (
                self._population_context_percentages(population_values)
                if population_values
                else None
            )
            updated["regional_population_pct"] = (
                percentages[0] if percentages is not None else None
            )
            if updated.get("national_population_pct") is None and percentages is not None:
                updated["national_population_pct"] = percentages[1]
            compared_profiles.append(updated)
        return compared_profiles

    async def _handle_custom_hazard_population_region_comparison(
        self,
        session_id: str,
        session: ChatSession,
        message: str,
    ) -> ChatResponse:
        regions = self._population_comparison_regions(session)
        selected_region = next(
            (
                region
                for region in regions
                if str(region.id) == str(message).strip()
                or normalize(str(region.name)) == normalize(message)
            ),
            None,
        )
        if selected_region is None:
            fuzzy_region = self._fuzzy_row_by_name(regions, message)
            if fuzzy_region is not None:
                selected_region = fuzzy_region
        if selected_region is None:
            return self._custom_hazard_population_region_comparison_step(
                session_id,
                session,
                error_reason="Please select one of the available comparison regions.",
            )

        hazard = str(session.accepted_custom_hazard or "").strip()
        profiles = self._stored_hazard_profiles(session, hazard)
        compared_profiles = await self._profiles_for_population_region_comparison(
            session,
            hazard,
            profiles,
            selected_region,
        )
        table = self._hazard_profile_region_comparison_table_html(
            profiles,
            compared_profiles,
            current_region=str(session.region or "Current region"),
            selected_region=str(selected_region.name),
            show_admin_details=self._show_profile_admin_details(),
        )
        session.phase = ChatPhase.HAZARDS.value
        return ChatResponse(
            session_id=session_id,
            step="hazard_population_region_comparison_result",
            bot_message=(
                "## Regional affected population comparison\n\n"
                f"{table}\n\n"
                "Choose **Compare regional population** to compare another region."
            ),
            options=CUSTOM_HAZARD_FINAL_OPTIONS,
            session=session.summary(),
            error=False,
        )

    def _record_target_population_answer(
        self,
        session_id: str,
        session: ChatSession,
        question: dict[str, object],
        selected_labels: list[str],
    ) -> None:
        if session.target_population_answers is None:
            session.target_population_answers = []
        answer_text = ", ".join(selected_labels)
        question_id = str(question["id"])
        session.target_population_answers = [
            answer
            for answer in session.target_population_answers
            if str(answer.get("question_id") or "") != question_id
        ]
        session.target_population_answers.append(
            {
                "question_id": question_id,
                "question": str(question["question"]),
                "answer": answer_text,
                "selected": list(selected_labels),
            }
        )
        hazard_id = session.accepted_custom_hazard_record_id or session.selected_hazard_record_id
        custom_hazard_id = session.accepted_custom_hazard_id
        if custom_hazard_id is None and (session.accepted_custom_hazard or session.selected_hazard):
            custom_hazard_id = self._custom_hazard_id_for_context(
                session,
                session.accepted_custom_hazard or session.selected_hazard or "",
            )
        if hazard_id is not None or custom_hazard_id is not None:
            filters = [
                UserQuestionResponse.question_id == question_id,
                UserQuestionResponse.category == "target_population",
            ]
            if custom_hazard_id is not None:
                filters.append(UserQuestionResponse.custom_hazard_id == custom_hazard_id)
            else:
                filters.append(UserQuestionResponse.user_hazard_id == hazard_id)
            self.db.execute(delete(UserQuestionResponse).where(*filters))
            self.db.commit()
        for selected in selected_labels:
            question_option_id = self.db.scalar(
                select(QuestionOption.id).where(
                    QuestionOption.question_id == question_id,
                    QuestionOption.option == selected,
                )
            )
            self._store_question_response(
                session_id,
                session,
                question_id=question_id,
                category="target_population",
                response_text=selected,
                question_option_id=question_option_id,
                hazard_id=hazard_id,
                custom_hazard_id=custom_hazard_id,
            )
        self._record_activity(
            session_id,
            session,
            "target_population_question_answered",
            f"{question['question']} -> {answer_text}",
        )

    def _prepare_custom_hazard_added_profiles(
        self, session_id: str, session: ChatSession
    ) -> str:
        transition_custom_hazard(session, ChatPhase.HAZARDS)
        accepted_hazard = session.accepted_custom_hazard or "New hazard"
        if not self._stored_hazard_profiles(session, accepted_hazard):
            self._set_custom_hazard_profiles_from_target_population(session)
        stored_profiles = self._stored_hazard_profiles(session, accepted_hazard)
        profile_items = stored_profiles or [
            {"name": profile, "profile": profile}
            for profile in (session.socio_demographic_profiles or [])
        ]
        profile_items = self._attach_target_population_matches_to_profiles(
            [
                dict(profile)
                if isinstance(profile, dict)
                else {"name": str(profile), "profile": str(profile)}
                for profile in profile_items
            ]
        )
        hazard_record_id = (
            session.accepted_custom_hazard_record_id or session.selected_hazard_record_id
        )
        custom_hazard_id = session.accepted_custom_hazard_id
        shared_hazard = self._ensure_custom_hazard(
            session,
            accepted_hazard,
            reason=session.accepted_custom_hazard_reason,
            evidence=session.accepted_custom_hazard_evidence,
            summary=session.accepted_custom_hazard_summary,
        )
        if shared_hazard is not None:
            custom_hazard_id = shared_hazard.id
        elif custom_hazard_id is None:
            custom_hazard_id = self._custom_hazard_id_for_context(
                session,
                accepted_hazard,
            )
        session.accepted_custom_hazard_id = custom_hazard_id
        if accepted_hazard and not any(
            normalize(item) == normalize(accepted_hazard)
            for item in (session.custom_hazards or [])
        ):
            if session.custom_hazards is None:
                session.custom_hazards = []
            session.custom_hazards.append(accepted_hazard)
        if session.custom_hazard_evidence_statuses is None:
            session.custom_hazard_evidence_statuses = {}
        session.custom_hazard_evidence_statuses[normalize(accepted_hazard)] = (
            evidence_is_provided(session.accepted_custom_hazard_evidence)
        )
        self._record_activity(session_id, session, "custom_hazard_added", accepted_hazard)
        if session.accepted_custom_hazard_evidence not in {None, "", "Not provided"}:
            self._promote_temporary_evidence(
                session,
                target_scope=VALIDATED_EVIDENCE_SCOPE,
                provenance="validated_user_evidence",
            )
        if hazard_record_id is not None:
            self._clear_target_population_profiles(hazard_record_id)
        for profile in profile_items:
            profile_payload = (
                dict(profile)
                if isinstance(profile, dict)
                else {"name": str(profile), "profile": str(profile)}
            )
            profile_source = str(profile_payload.get("source") or "custom_hazard_extraction").strip()
            profile_payload["source"] = profile_source[:40] or "custom_hazard_extraction"
            self._store_custom_hazard_profile(custom_hazard_id, profile_payload)
            if hazard_record_id is not None:
                self._store_socio_demographic(
                    session,
                    str(profile_payload.get("name") or profile_payload.get("profile") or ""),
                    user_hazard_id=hazard_record_id,
                    source=profile_source[:40] or "custom_hazard_extraction",
                    variable_name=str(profile_payload.get("variable_name") or "") or None,
                    explanation=str(profile_payload.get("explanation") or "") or None,
                    statistical_basis=str(profile_payload.get("statistical_basis") or "") or None,
                    metadata=profile_payload,
                )
        return accepted_hazard

    @staticmethod
    def _ensure_custom_hazard_summary_visible(message: str, summary: str) -> str:
        """Keep required output visible when a DB prompt predates this field."""
        summary = str(summary or "").strip()
        if not summary or re.search(
            r"<strong>\s*Summary\s*:</strong>", message, flags=re.IGNORECASE
        ):
            return message

        summary_html = (
            '<p><strong>Summary:</strong> '
            f"{html.escape(summary)}</p>"
        )
        for marker in (
            r"(?=<p><strong>\s*Reason\s*:</strong>)",
            r"(?=<h[1-6][^>]*>\s*Affected Population Groups\s*</h[1-6]>)",
        ):
            updated = re.sub(
                marker,
                summary_html,
                message,
                count=1,
                flags=re.IGNORECASE,
            )
            if updated != message:
                return updated
        return f"{message}{summary_html}"

    def _custom_hazard_added_step_sync(self, session_id: str, session: ChatSession) -> ChatResponse:
        if not str(session.accepted_custom_hazard_summary or "").strip():
            hazard = session.accepted_custom_hazard or "New hazard"
            state = session.custom_hazard if isinstance(session.custom_hazard, dict) else {}
            clarifications = [
                item
                for item in state.get("clarifications") or []
                if isinstance(item, dict)
            ]
            groups = [
                {
                    "group": str(profile.get("name") or profile.get("profile") or "").strip(),
                    "reason": str(profile.get("explanation") or "").strip(),
                }
                for profile in self._stored_hazard_profiles(session, hazard)
                if isinstance(profile, dict)
            ]
            session.accepted_custom_hazard_summary = self._custom_hazard_summary_fallback(
                session,
                hazard,
                session.generated_custom_hazard_title or hazard,
                clarifications,
                groups,
            )
        accepted_hazard = self._prepare_custom_hazard_added_profiles(session_id, session)
        added_message = render_message(
            "hazard_added.md",
            hazard=accepted_hazard,
            original_hazard=accepted_hazard,
            summary=session.accepted_custom_hazard_summary or "",
            reason=session.accepted_custom_hazard_reason or "Not provided",
            evidence=evidence_for_display(session.accepted_custom_hazard_evidence),
            affected_population_groups=self._format_population_profiles_for_final(
                self._stored_hazard_profiles(session, accepted_hazard)
            ),
            visibility_notice=self._crowd_sourcing_visibility_notice(
                session,
                "saved_hazard",
            ),
        )
        added_message = self._ensure_custom_hazard_summary_visible(
            added_message,
            session.accepted_custom_hazard_summary or "",
        )
        return ChatResponse(
            session_id=session_id,
            step="hazards",
            bot_message=added_message,
            options=CUSTOM_HAZARD_FINAL_OPTIONS,
            session=session.summary(),
            error=False,
        )

    async def _custom_hazard_added_step(
        self, session_id: str, session: ChatSession
    ) -> ChatResponse:
        original_hazard = session.accepted_custom_hazard or "New hazard"
        generated_hazard = await self._ensure_custom_hazard_generated_title(
            session,
            original_hazard,
        )
        await self._ensure_custom_hazard_summary(
            session,
            original_hazard,
            generated_hazard,
        )
        if generated_hazard and generated_hazard != original_hazard:
            if session.hazard_profiles and original_hazard in session.hazard_profiles:
                session.hazard_profiles[generated_hazard] = session.hazard_profiles.pop(
                    original_hazard
                )
            session.accepted_custom_hazard = generated_hazard
        accepted_hazard = self._prepare_custom_hazard_added_profiles(session_id, session)
        profiles = self._stored_hazard_profiles(session, accepted_hazard)
        enriched_profiles = await self._additional_profiles_with_population_context(
            session,
            accepted_hazard,
            profiles,
        )
        if enriched_profiles:
            if session.hazard_profiles is None:
                session.hazard_profiles = {}
            session.hazard_profiles[accepted_hazard] = enriched_profiles
        added_message = render_message(
            "hazard_added.md",
            hazard=accepted_hazard,
            original_hazard=original_hazard,
            summary=session.accepted_custom_hazard_summary or "",
            reason=session.accepted_custom_hazard_reason or "Not provided",
            evidence=evidence_for_display(session.accepted_custom_hazard_evidence),
            affected_population_groups=self._format_population_profiles_for_final(
                self._stored_hazard_profiles(session, accepted_hazard)
            ),
            visibility_notice=self._crowd_sourcing_visibility_notice(
                session,
                "saved_hazard",
            ),
        )
        added_message = self._ensure_custom_hazard_summary_visible(
            added_message,
            session.accepted_custom_hazard_summary or "",
        )
        response = ChatResponse(
            session_id=session_id,
            step="hazards",
            bot_message=added_message,
            options=CUSTOM_HAZARD_FINAL_OPTIONS,
            session=session.summary(),
            error=False,
        )
        if isinstance(session.custom_hazard, dict):
            response.validation_details = custom_hazard_validation_details(session.custom_hazard)
            response.custom_hazard = frontend_custom_hazard_payload(session.custom_hazard)
            response.custom_hazard_grounding_status = build_custom_hazard_grounding_status(
                session.custom_hazard
            )
        return response

    def _clear_target_population_profiles(self, hazard_id: str | None) -> None:
        if hazard_id is None:
            return
        try:
            self.db.execute(
                delete(UserHazardSocioDemographic).where(
                    UserHazardSocioDemographic.user_hazard_id == hazard_id,
                    UserHazardSocioDemographic.source == "target_population",
                )
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception("Failed to clear prior target-population profiles")
