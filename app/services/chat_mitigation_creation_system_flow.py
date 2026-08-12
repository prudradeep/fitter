# ruff: noqa: F403,F405
from app.services.chat_mitigation_creation_common import *

async def _ask_llm_chat(*args, **kwargs):
    from app.services import chat_mitigation_creation as facade

    return await facade.ask_llm_chat(*args, **kwargs)


class ChatMitigationCreationSystemFlowMixin:
    def _system_inquiry_intro_step(
        self,
        session_id: str,
        session: ChatSession,
        *,
        prefix_message: str = "",
    ) -> ChatResponse:
        observations = self._system_inquiry_observations(session)
        session.system_inquiry_observations = observations
        session.system_inquiry_index = 0
        session.system_inquiry_annotations = []
        session.system_inquiry_pending_followup = None
        session.system_inquiry_coverage_summary = self._system_inquiry_coverage_summary(session)
        session.system_inquiry_coverage_completion_done = False
        session.system_inquiry_skipped = False
        session.phase = "system_inquiry_intro"

        count = len(observations)
        coverage_summary = self._format_system_inquiry_coverage_summary(
            session.system_inquiry_coverage_summary,
        )
        intro = render_message(
            "system_inquiry_intro.md",
            count=count,
            plural_suffix="" if count == 1 else "s",
            coverage_summary=coverage_summary,
            stale_notice=self._system_inquiry_stale_notice(session),
            boundary_note=self._format_system_inquiry_boundary_note(
                session.system_inquiry_held_observations,
            ),
        )
        bot_message = "\n\n".join(part for part in (prefix_message, intro) if part)
        return ChatResponse(
            session_id=session_id,
            step="system_inquiry_intro",
            bot_message=bot_message,
            options=SYSTEM_INQUIRY_INTRO_OPTIONS,
            session=session.summary(),
            error=False,
        )

    async def _system_inquiry_intro_step_with_llm(
        self,
        session_id: str,
        session: ChatSession,
        *,
        prefix_message: str = "",
    ) -> ChatResponse:
        observations = await self._system_inquiry_observations_with_llm(session)
        session.system_inquiry_observations = observations
        session.system_inquiry_index = 0
        session.system_inquiry_annotations = []
        session.system_inquiry_pending_followup = None
        session.system_inquiry_coverage_summary = self._system_inquiry_coverage_summary(session)
        session.system_inquiry_coverage_completion_done = False
        session.system_inquiry_skipped = False
        session.phase = "system_inquiry_intro"

        count = len(observations)
        coverage_summary = self._format_system_inquiry_coverage_summary(
            session.system_inquiry_coverage_summary,
        )
        intro = render_message(
            "system_inquiry_intro.md",
            count=count,
            plural_suffix="" if count == 1 else "s",
            coverage_summary=coverage_summary,
            stale_notice=self._system_inquiry_stale_notice(session),
            boundary_note=self._format_system_inquiry_boundary_note(
                session.system_inquiry_held_observations,
            ),
        )
        bot_message = "\n\n".join(part for part in (prefix_message, intro) if part)
        return ChatResponse(
            session_id=session_id,
            step="system_inquiry_intro",
            bot_message=bot_message,
            options=SYSTEM_INQUIRY_INTRO_OPTIONS,
            session=session.summary(),
            error=False,
        )

    async def _handle_system_inquiry_intro(
        self,
        session_id: str,
        session: ChatSession,
        message: str,
    ) -> ChatResponse:
        exact_label = exact_option_label(message, SYSTEM_INQUIRY_INTRO_OPTIONS)
        if exact_label is None:
            fuzzy_label = match_option_label(message, SYSTEM_INQUIRY_INTRO_OPTIONS)
            if fuzzy_label is not None:
                exact_label = fuzzy_label
        action = normalize(exact_label or message)
        if action == normalize("Skip system inquiry"):
            session.system_inquiry_skipped = True
            return self._system_inquiry_complete_step(
                session_id,
                session,
                skipped=True,
            )
        if action == normalize("Start system inquiry"):
            if not session.system_inquiry_observations:
                return self._system_inquiry_complete_step(session_id, session)
            return self._system_inquiry_observation_step(session_id, session)
        return ChatResponse(
            session_id=session_id,
            step="system_inquiry_intro",
            bot_message=getattr(
                self,
                "invalid_message",
                "I could not understand your selection. Please choose from the available options.",
            ),
            options=SYSTEM_INQUIRY_INTRO_OPTIONS,
            session=session.summary(),
            error=True,
        )

    def _system_inquiry_observation_step(
        self,
        session_id: str,
        session: ChatSession,
        *,
        error_reason: str = "",
    ) -> ChatResponse:
        observations = session.system_inquiry_observations or []
        index = max(0, session.system_inquiry_index)
        if index >= len(observations):
            return self._system_inquiry_complete_step(session_id, session)
        observation = observations[index]
        session.phase = "system_inquiry_observation"
        message = render_message(
            "system_inquiry_observation.md",
            current=index + 1,
            total=len(observations),
            title=str(observation.get("title") or "System inquiry"),
            corpus_label=str(observation.get("corpus_label") or "unproven"),
            observation=str(observation.get("observation") or "").strip(),
            why_it_matters=str(observation.get("why_it_matters") or "").strip(),
            question=str(observation.get("question") or "").strip(),
        )
        if error_reason:
            message = (
                render_message("input_validation_failed.md", reason=error_reason)
                + "\n\n"
                + message
            )
        return ChatResponse(
            session_id=session_id,
            step="system_inquiry_observation",
            bot_message=message,
            options=SYSTEM_INQUIRY_OBSERVATION_OPTIONS,
            session=session.summary(),
            input_mode="textarea",
            error=bool(error_reason),
        )

    async def _handle_system_inquiry_observation(
        self,
        session_id: str,
        session: ChatSession,
        message: str,
    ) -> ChatResponse:
        exact_label = exact_option_label(message, SYSTEM_INQUIRY_OBSERVATION_OPTIONS)
        if exact_label is None:
            fuzzy_label = match_option_label(message, SYSTEM_INQUIRY_OBSERVATION_OPTIONS)
            if fuzzy_label is not None:
                exact_label = fuzzy_label
        action = normalize(exact_label or message)
        if action == normalize("End system inquiry"):
            return self._system_inquiry_complete_step(session_id, session)

        observations = session.system_inquiry_observations or []
        index = max(0, session.system_inquiry_index)
        if index >= len(observations):
            return self._system_inquiry_complete_step(session_id, session)

        observation = observations[index]
        if action == normalize("Skip this question"):
            self._append_system_inquiry_annotation(
                session,
                observation,
                user_response="",
                resolution_state="open",
            )
        else:
            if self._is_invalid_user_text(message) or len(compact_for_match(message)) < 4:
                return self._system_inquiry_observation_step(
                    session_id,
                    session,
                    error_reason="Please add a short response, or choose Skip this question.",
                )
            adjudication = await self._adjudicate_system_inquiry_response_with_llm(
                session,
                observation,
                message,
            )
            if adjudication["needs_followup"]:
                session.system_inquiry_pending_followup = {
                    "index": index,
                    "observation": observation,
                    "user_response": message.strip(),
                    "adjudication": adjudication,
                }
                return self._system_inquiry_followup_step(session_id, session)
            self._append_system_inquiry_annotation(
                session,
                observation,
                user_response=message.strip(),
                resolution_state=str(adjudication["resolution_state"]),
                evaluation=str(adjudication["evaluation"]),
            )

        session.system_inquiry_index = index + 1
        if session.system_inquiry_index >= len(observations):
            return self._system_inquiry_complete_step(session_id, session)
        return self._system_inquiry_observation_step(session_id, session)

    def _system_inquiry_followup_step(
        self,
        session_id: str,
        session: ChatSession,
        *,
        error_reason: str = "",
    ) -> ChatResponse:
        pending = session.system_inquiry_pending_followup or {}
        adjudication = (
            pending.get("adjudication")
            if isinstance(pending.get("adjudication"), dict)
            else {}
        )
        session.phase = "system_inquiry_followup"
        message = render_message(
            "system_inquiry_followup.md",
            evaluation=str(adjudication.get("evaluation") or "").strip(),
            followup_question=str(adjudication.get("followup_question") or "").strip(),
        )
        if error_reason:
            message = (
                render_message("input_validation_failed.md", reason=error_reason)
                + "\n\n"
                + message
            )
        return ChatResponse(
            session_id=session_id,
            step="system_inquiry_followup",
            bot_message=message,
            options=SYSTEM_INQUIRY_FOLLOWUP_OPTIONS,
            session=session.summary(),
            input_mode="textarea",
            error=bool(error_reason),
        )

    def _system_inquiry_coverage_completion_step(
        self,
        session_id: str,
        session: ChatSession,
        *,
        error_reason: str = "",
    ) -> ChatResponse:
        groups = self._system_inquiry_untargeted_coverage_groups(session)
        observation = {
            "probe_id": "D5-COVERAGE",
            "lens_id": "D5",
            "family": "D_portfolio",
            "tier": "optional",
            "title": "Affected group completeness",
            "corpus_label": "unproven",
            "observation": "Some affected groups are not named in the current mitigation coverage.",
            "question": "Add an optional completeness note for the left-out affected groups.",
            "followup_types": ["coverage_completion"],
            "anchors": {
                "measure": session.mitigation_measure or "the measure",
                "hazard": session.selected_hazard or "the selected hazard",
                "omitted_groups": groups,
            },
            "salience_score": 0.0,
        }
        session.phase = "system_inquiry_followup"
        session.system_inquiry_pending_followup = {
            "index": len(session.system_inquiry_observations or []),
            "observation": observation,
            "user_response": "",
            "adjudication": {
                "resolution_state": "open",
                "evaluation": "Optional affected-group completeness note requested.",
                "followup_question": self._system_inquiry_coverage_completion_question(groups),
                "followup_type": "coverage_completion",
            },
        }
        message = render_message(
            "system_inquiry_followup.md",
            evaluation="",
            followup_question=self._system_inquiry_coverage_completion_question(groups),
        )
        if error_reason:
            message = (
                render_message("input_validation_failed.md", reason=error_reason)
                + "\n\n"
                + message
            )
        return ChatResponse(
            session_id=session_id,
            step="system_inquiry_followup",
            bot_message=message,
            options=[Option(id=1, label="Skip"), Option(id=2, label="End system inquiry")],
            session=session.summary(),
            input_mode="textarea",
            error=bool(error_reason),
        )

    async def _handle_system_inquiry_followup(
        self,
        session_id: str,
        session: ChatSession,
        message: str,
    ) -> ChatResponse:
        exact_label = exact_option_label(message, SYSTEM_INQUIRY_FOLLOWUP_OPTIONS)
        if exact_label is None:
            fuzzy_label = match_option_label(message, SYSTEM_INQUIRY_FOLLOWUP_OPTIONS)
            if fuzzy_label is not None:
                exact_label = fuzzy_label
        action = normalize(exact_label or message)
        pending = session.system_inquiry_pending_followup or {}
        observation = (
            pending.get("observation")
            if isinstance(pending.get("observation"), dict)
            else {}
        )
        if not observation:
            session.system_inquiry_pending_followup = None
            return self._system_inquiry_complete_step(session_id, session)
        index = int(pending.get("index") or session.system_inquiry_index)
        user_response = str(pending.get("user_response") or "").strip()
        adjudication = (
            pending.get("adjudication")
            if isinstance(pending.get("adjudication"), dict)
            else {}
        )
        followup_type = str(adjudication.get("followup_type") or "").strip()

        if followup_type == "coverage_completion":
            if action in {normalize("Skip"), normalize("Skip follow-up"), normalize("End system inquiry")}:
                session.system_inquiry_coverage_completion_done = True
                session.system_inquiry_pending_followup = None
                return self._system_inquiry_complete_step(session_id, session)
            if self._is_invalid_user_text(message) or len(compact_for_match(message)) < 4:
                return self._system_inquiry_coverage_completion_step(
                    session_id,
                    session,
                    error_reason="Please add a short completeness note, or choose Skip.",
                )
            self._append_system_inquiry_annotation(
                session,
                observation,
                user_response=message.strip(),
                resolution_state="addressed",
                evaluation="Optional affected-group completeness note recorded.",
                followup_question=str(adjudication.get("followup_question") or ""),
                followup_type=followup_type,
            )
            session.system_inquiry_coverage_completion_done = True
            session.system_inquiry_pending_followup = None
            return self._system_inquiry_complete_step(session_id, session)

        if action == normalize("End system inquiry"):
            self._append_system_inquiry_annotation(
                session,
                observation,
                user_response=user_response,
                resolution_state=str(adjudication.get("resolution_state") or "open"),
                evaluation=str(adjudication.get("evaluation") or ""),
                followup_question=str(adjudication.get("followup_question") or ""),
                followup_type=str(adjudication.get("followup_type") or ""),
            )
            session.system_inquiry_pending_followup = None
            return self._system_inquiry_complete_step(session_id, session)

        followup_response = ""
        final_state = str(adjudication.get("resolution_state") or "open")
        final_evaluation = str(adjudication.get("evaluation") or "").strip()
        if action in {normalize("Skip follow-up"), normalize("Skip")}:
            final_evaluation = (
                final_evaluation
                or "The original response did not fully resolve the system inquiry."
            )
        else:
            if self._is_invalid_user_text(message) or len(compact_for_match(message)) < 4:
                return self._system_inquiry_followup_step(
                    session_id,
                    session,
                    error_reason="Please add a short follow-up, or choose Skip.",
                )
            followup_response = message.strip()
            combined = f"{user_response}\n\n{followup_response}".strip()
            final_adjudication = await self._adjudicate_system_inquiry_response_with_llm(
                session,
                observation,
                combined,
                allow_followup=False,
            )
            final_state = str(final_adjudication["resolution_state"])
            if final_state == "open":
                final_state = "partially_addressed"
            final_evaluation = str(final_adjudication["evaluation"])

        self._append_system_inquiry_annotation(
            session,
            observation,
            user_response=user_response,
            resolution_state=final_state,
            evaluation=final_evaluation,
            followup_question=str(adjudication.get("followup_question") or ""),
            followup_type=str(adjudication.get("followup_type") or ""),
            followup_response=followup_response,
        )
        session.system_inquiry_pending_followup = None
        session.system_inquiry_index = index + 1
        observations = session.system_inquiry_observations or []
        if session.system_inquiry_index >= len(observations):
            return self._system_inquiry_complete_step(session_id, session)
        return self._system_inquiry_observation_step(session_id, session)

    def _system_inquiry_complete_step(
        self,
        session_id: str,
        session: ChatSession,
        *,
        skipped: bool = False,
    ) -> ChatResponse:
        if (
            not skipped
            and not session.system_inquiry_coverage_completion_done
            and self._system_inquiry_untargeted_coverage_groups(session)
        ):
            return self._system_inquiry_coverage_completion_step(session_id, session)
        session.phase = "system_inquiry_complete"
        annotations = session.system_inquiry_annotations or []
        if skipped:
            summary = "System inquiry was skipped."
        elif annotations:
            state_counts: dict[str, int] = {}
            for item in annotations:
                state = str(item.get("resolution_state") or "open")
                state_counts[state] = state_counts.get(state, 0) + 1
            addressed = state_counts.get("addressed", 0)
            partial = state_counts.get("partially_addressed", 0)
            reasoned_na = state_counts.get("not_applicable_reasoned", 0)
            unresolved = state_counts.get("acknowledged_unresolved", 0)
            open_count = state_counts.get("open", 0)
            summary = (
                f"{len(annotations)} reflection response"
                f"{' was' if len(annotations) == 1 else 's were'} recorded: "
                f"{addressed} addressed, {partial} partially addressed, "
                f"{reasoned_na} reasoned not-applicable, {unresolved} unresolved, "
                f"and {open_count} open."
            )
        else:
            summary = "No system inquiry reflections were recorded."
        session.system_inquiry_profile = self._system_inquiry_profile(session)
        self._persist_system_inquiry_result(session, summary)
        return ChatResponse(
            session_id=session_id,
            step="system_inquiry_complete",
            bot_message=render_message(
                "system_inquiry_complete.md",
                summary=summary,
                coverage_summary=self._format_system_inquiry_coverage_summary(
                    session.system_inquiry_coverage_summary,
                ),
            ),
            options=SYSTEM_INQUIRY_COMPLETE_OPTIONS,
            session=session.summary(),
            error=False,
        )

    @staticmethod
    def _system_inquiry_coverage_completion_question(groups: list[str]) -> str:
        group_list = "; ".join(groups[:5])
        return (
            "Affected groups not yet covered: "
            f"{group_list}. "
            "You can add a completeness note for these groups, or skip this optional step."
        )

    @staticmethod
    def _system_inquiry_untargeted_coverage_groups(session: ChatSession) -> list[str]:
        coverage = session.system_inquiry_coverage_summary
        if not isinstance(coverage, dict):
            return []
        return [
            str(item).strip()
            for item in (coverage.get("untargeted_groups") or [])
            if str(item).strip()
        ]

    def _append_system_inquiry_annotation(
        self,
        session: ChatSession,
        observation: dict[str, object],
        *,
        user_response: str,
        resolution_state: str,
        evaluation: str = "",
        followup_question: str = "",
        followup_type: str = "",
        followup_response: str = "",
    ) -> None:
        annotations = list(session.system_inquiry_annotations or [])
        context_fingerprint = self._system_inquiry_context_fingerprint(session)
        annotations.append(
            {
                "annotation_id": f"si-{len(annotations) + 1:03d}",
                "schema_version": 1,
                "version": 1,
                "created_at": self._system_inquiry_created_at(),
                "probe_id": str(observation.get("probe_id") or ""),
                "lens_id": str(observation.get("lens_id") or ""),
                "family": str(observation.get("family") or ""),
                "tier": str(observation.get("tier") or ""),
                "library_version": str(observation.get("library_version") or ""),
                "candidate_id": str(observation.get("candidate_id") or ""),
                "candidate_status": str(observation.get("candidate_status") or ""),
                "trigger_basis": str(observation.get("trigger_basis") or ""),
                "screen_result": bool(observation.get("screen_result")),
                "verify_votes": observation.get("verify_votes"),
                "salience_score": float(observation.get("salience_score") or 0.0),
                "citations": (
                    list(observation.get("citations"))
                    if isinstance(observation.get("citations"), list)
                    else []
                ),
                "source_refs": (
                    list(observation.get("source_refs"))
                    if isinstance(observation.get("source_refs"), list)
                    else []
                ),
                "required_anchors": (
                    observation.get("required_anchors")
                    if isinstance(observation.get("required_anchors"), dict)
                    else {}
                ),
                "anchor_counts": (
                    observation.get("anchor_counts")
                    if isinstance(observation.get("anchor_counts"), dict)
                    else {}
                ),
                "observation_text": str(observation.get("observation") or ""),
                "question_text": str(observation.get("question") or ""),
                "corpus_label": str(observation.get("corpus_label") or "unproven"),
                "followup_types": (
                    list(observation.get("followup_types"))
                    if isinstance(observation.get("followup_types"), list)
                    else []
                ),
                "user_response": user_response,
                "resolution_state": resolution_state,
                "evaluation": evaluation,
                "followup_question": followup_question,
                "followup_type": followup_type,
                "followup_response": followup_response,
                "status": "current",
                "context_fingerprint": context_fingerprint,
                "superseded_by": None,
                "anchors": (
                    observation.get("anchors")
                    if isinstance(observation.get("anchors"), dict)
                    else {}
                ),
            }
        )
        session.system_inquiry_annotations = annotations

    def _system_inquiry_payload(
        self,
        session: ChatSession,
        summary: str = "",
    ) -> dict[str, object]:
        context_snapshot = self._system_inquiry_context_snapshot(session)
        context_fingerprint = self._system_inquiry_context_fingerprint(
            session,
            snapshot=context_snapshot,
        )
        profile = session.system_inquiry_profile or self._system_inquiry_profile(session)
        return {
            "schema_version": 1,
            "library_version": self._system_inquiry_library_version(),
            "skipped": bool(session.system_inquiry_skipped),
            "summary": summary,
            "selected_hazard": session.selected_hazard or "",
            "mitigation_measure": session.mitigation_measure or "",
            "context_fingerprint": context_fingerprint,
            "context_snapshot": context_snapshot,
            "attributes": session.system_inquiry_attributes
            or self._system_inquiry_measure_attributes(
                session.mitigation_measure,
                session.mitigation_reason,
                session.mitigation_target_population,
            ),
            "coverage_summary": session.system_inquiry_coverage_summary or {},
            "held_observations": list(session.system_inquiry_held_observations or []),
            "candidate_audit": self._system_inquiry_candidate_audit_payload(session),
            "profile": profile,
            "telemetry": self._system_inquiry_telemetry(session, profile),
            "annotations": self._system_inquiry_payload_annotations(
                session,
                context_fingerprint,
            ),
        }

    def _persist_system_inquiry_result(
        self,
        session: ChatSession,
        summary: str = "",
    ) -> None:
        mitigation_record_id = str(session.mitigation_record_id or "").strip()
        db = getattr(self, "db", None)
        if not mitigation_record_id or db is None:
            return
        try:
            row = db.scalar(
                select(UserMitigationMeasure).where(
                    UserMitigationMeasure.id == mitigation_record_id
                )
            )
            if row is None:
                return
            payload = self._system_inquiry_payload(session, summary)
            existing_payload = self._system_inquiry_existing_payload(
                row.system_inquiry_json,
            )
            payload["superseded_annotations"] = self._system_inquiry_superseded_annotations(
                existing_payload,
                str(payload.get("context_fingerprint") or ""),
            )
            row.system_inquiry_json = json.dumps(
                payload,
                ensure_ascii=False,
                default=str,
            )
            telemetry = payload.get("telemetry")
            if isinstance(telemetry, dict):
                enqueue_system_inquiry_telemetry(db, telemetry)
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Failed to persist system inquiry annotations")

    @staticmethod
    def _system_inquiry_existing_payload(value: str | None) -> dict[str, object]:
        try:
            decoded = json.loads(value or "{}")
        except (TypeError, ValueError):
            return {}
        return decoded if isinstance(decoded, dict) else {}

    def _system_inquiry_stale_notice(self, session: ChatSession) -> str:
        mitigation_record_id = str(session.mitigation_record_id or "").strip()
        db = getattr(self, "db", None)
        if not mitigation_record_id or db is None:
            return ""
        try:
            row = db.scalar(
                select(UserMitigationMeasure).where(
                    UserMitigationMeasure.id == mitigation_record_id
                )
            )
        except Exception:
            logger.exception("Failed to load prior system inquiry state")
            return ""
        if row is None:
            return ""
        payload = self._system_inquiry_existing_payload(
            getattr(row, "system_inquiry_json", None),
        )
        if not payload:
            return ""
        existing_fingerprint = str(payload.get("context_fingerprint") or "")
        current_fingerprint = self._system_inquiry_context_fingerprint(session)
        if not existing_fingerprint or existing_fingerprint == current_fingerprint:
            return ""
        annotations = payload.get("annotations")
        count = len(annotations) if isinstance(annotations, list) else 0
        if count <= 0:
            return ""
        return (
            "Re-run note: this mitigation context has changed since the previous "
            f"system inquiry, so {count} reflection response"
            f"{'' if count == 1 else 's'} may no longer apply. Start system "
            "inquiry to revisit them, or skip this optional step."
        )

    def _system_inquiry_superseded_annotations(
        self,
        existing_payload: dict[str, object],
        new_context_fingerprint: str,
    ) -> list[dict[str, object]]:
        if not existing_payload:
            return []
        existing_fingerprint = str(existing_payload.get("context_fingerprint") or "")
        existing_annotations = existing_payload.get("annotations")
        previous_superseded = existing_payload.get("superseded_annotations")
        superseded: list[dict[str, object]] = []
        if isinstance(previous_superseded, list):
            for item in previous_superseded:
                if isinstance(item, dict):
                    superseded.append(dict(item))
        if (
            not existing_fingerprint
            or existing_fingerprint == new_context_fingerprint
            or not isinstance(existing_annotations, list)
        ):
            return superseded
        for annotation in existing_annotations:
            if not isinstance(annotation, dict):
                continue
            item = dict(annotation)
            item["status"] = "superseded"
            item["superseded_by"] = new_context_fingerprint
            superseded.append(item)
        return superseded

    def _system_inquiry_context_snapshot(self, session: ChatSession) -> dict[str, object]:
        return {
            "selected_hazard": session.selected_hazard or "",
            "mitigation_measure": session.mitigation_measure or "",
            "mitigation_reason": session.mitigation_reason or "",
            "target_population": list(session.mitigation_target_population or []),
            "evaluation_answers": [
                {
                    "category": str(answer.get("category") or ""),
                    "question": str(answer.get("question") or ""),
                    "score": answer.get("score"),
                }
                for answer in (session.evaluation_answers or [])
                if isinstance(answer, dict)
            ],
        }

    def _system_inquiry_context_fingerprint(
        self,
        session: ChatSession,
        *,
        snapshot: dict[str, object] | None = None,
    ) -> str:
        payload = snapshot or self._system_inquiry_context_snapshot(session)
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _system_inquiry_payload_annotations(
        session: ChatSession,
        context_fingerprint: str,
    ) -> list[dict[str, object]]:
        normalized: list[dict[str, object]] = []
        for index, annotation in enumerate(session.system_inquiry_annotations or [], start=1):
            if not isinstance(annotation, dict):
                continue
            item = dict(annotation)
            item.setdefault("annotation_id", f"si-{index:03d}")
            item.setdefault("schema_version", 1)
            item.setdefault("version", 1)
            item.setdefault(
                "created_at",
                ChatMitigationCreationSystemFlowMixin._system_inquiry_created_at(),
            )
            item.setdefault("status", "current")
            item.setdefault("context_fingerprint", context_fingerprint)
            item.setdefault("superseded_by", None)
            item.setdefault("citations", [])
            item.setdefault("source_refs", [])
            normalized.append(item)
        return normalized

    @staticmethod
    def _system_inquiry_created_at() -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
            "+00:00",
            "Z",
        )

    @staticmethod
    def _system_inquiry_candidate_audit_payload(
        session: ChatSession,
    ) -> list[dict[str, object]]:
        normalized: list[dict[str, object]] = []
        for candidate in session.system_inquiry_candidate_audit or []:
            if not isinstance(candidate, dict):
                continue
            item = {
                "candidate_id": str(candidate.get("candidate_id") or ""),
                "probe_id": str(candidate.get("probe_id") or ""),
                "measure_id": str(candidate.get("measure_id") or ""),
                "lens_id": str(candidate.get("lens_id") or ""),
                "family": str(candidate.get("family") or ""),
                "tier": str(candidate.get("tier") or ""),
                "library_version": str(candidate.get("library_version") or ""),
                "trigger_basis": str(candidate.get("trigger_basis") or ""),
                "anchors": (
                    candidate.get("anchors")
                    if isinstance(candidate.get("anchors"), dict)
                    else {}
                ),
                "required_anchors": (
                    candidate.get("required_anchors")
                    if isinstance(candidate.get("required_anchors"), dict)
                    else {}
                ),
                "anchor_counts": (
                    candidate.get("anchor_counts")
                    if isinstance(candidate.get("anchor_counts"), dict)
                    else {}
                ),
                "screen_result": bool(candidate.get("screen_result")),
                "verify_votes": candidate.get("verify_votes"),
                "corpus_label": str(candidate.get("corpus_label") or "unproven"),
                "citations": (
                    list(candidate.get("citations"))
                    if isinstance(candidate.get("citations"), list)
                    else []
                ),
                "source_refs": (
                    list(candidate.get("source_refs"))
                    if isinstance(candidate.get("source_refs"), list)
                    else []
                ),
                "salience_score": float(candidate.get("salience_score") or 0.0),
                "status": str(candidate.get("candidate_status") or ""),
            }
            normalized.append(item)
        return normalized

    def _system_inquiry_profile(self, session: ChatSession) -> dict[str, object]:
        annotations = list(session.system_inquiry_annotations or [])
        weights = {
            "addressed": 1.0,
            "not_applicable_reasoned": 1.0,
            "partially_addressed": 0.5,
            "acknowledged_unresolved": 0.0,
            "open": 0.0,
        }
        state_counts: dict[str, int] = {}
        status_counts: dict[str, int] = {}
        family_counts: dict[str, int] = {}
        family_scores: dict[str, float] = {}
        per_family = self._empty_system_inquiry_per_family()
        for annotation in annotations:
            state = str(annotation.get("resolution_state") or "open")
            status = str(annotation.get("status") or "current")
            family = str(annotation.get("family") or "unknown")
            score = weights.get(state, 0.0)
            state_counts[state] = state_counts.get(state, 0) + 1
            status_counts[status] = status_counts.get(status, 0) + 1
            family_counts[family] = family_counts.get(family, 0) + 1
            family_scores[family] = family_scores.get(family, 0.0) + score
            family_profile = per_family.setdefault(
                family,
                self._empty_system_inquiry_family_profile(),
            )
            family_profile["surfaced"] += 1
            if state == "partially_addressed":
                family_profile["partially"] += 1
            elif state == "acknowledged_unresolved":
                family_profile["acknowledged"] += 1
            elif state in family_profile:
                family_profile[state] += 1
            else:
                family_profile["open"] += 1
            family_profile["_score"] += score

        for item in session.system_inquiry_held_observations or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("candidate_status") or "") != "held_cap":
                continue
            family = str(item.get("family") or "unknown")
            family_profile = per_family.setdefault(
                family,
                self._empty_system_inquiry_family_profile(),
            )
            family_profile["unexamined_held_by_cap"] += 1

        for family_profile in per_family.values():
            surfaced = int(family_profile.get("surfaced") or 0)
            score = float(family_profile.pop("_score", 0.0))
            family_profile["coverage"] = round(score / surfaced, 3) if surfaced else 0.0

        total = len(annotations)
        addressed_score = sum(
            weights.get(str(item.get("resolution_state") or "open"), 0.0)
            for item in annotations
        )
        family_completion = {
            family: round(family_scores.get(family, 0.0) / count, 3)
            for family, count in family_counts.items()
            if count
        }
        return {
            "session_id_anon": self._system_inquiry_session_id_anon(session),
            "library_version": self._system_inquiry_library_version(),
            "annotation_count": total,
            "state_counts": state_counts,
            "status_counts": status_counts,
            "per_family": per_family,
            "completion_score": round(addressed_score / total, 3) if total else 0.0,
            "family_counts": family_counts,
            "family_completion": family_completion,
            "leverage_distribution": self._system_inquiry_leverage_distribution(session),
            "trajectory": self._system_inquiry_trajectory(session, per_family),
            "followup_used": any(
                str(item.get("followup_response") or "").strip()
                for item in annotations
            ),
        }

    @classmethod
    def _empty_system_inquiry_per_family(cls) -> dict[str, dict[str, float | int]]:
        return {
            family: cls._empty_system_inquiry_family_profile()
            for family in ("A_structure", "B_framing", "C_justice", "D_portfolio")
        }

    @staticmethod
    def _system_inquiry_library_version() -> str:
        return system_inquiry_library_version()

    def _system_inquiry_session_id_anon(self, session: ChatSession) -> str:
        stable_value = (
            str(session.session_key or "").strip()
            or str(session.mitigation_record_id or "").strip()
            or self._system_inquiry_context_fingerprint(session)
        )
        return hashlib.sha256(stable_value.encode("utf-8")).hexdigest()[:16]

    def _system_inquiry_telemetry(
        self,
        session: ChatSession,
        profile: dict[str, object],
    ) -> dict[str, object]:
        per_family = profile.get("per_family") if isinstance(profile.get("per_family"), dict) else {}
        family_coverage = self._system_inquiry_family_coverage(per_family)
        return {
            "session_id_anon": str(profile.get("session_id_anon") or ""),
            "sector": session.sector or "",
            "country": session.country or "",
            "measure_ordinal": len(self._system_inquiry_prior_measure_rows(session)) + 1,
            "library_version": self._system_inquiry_library_version(),
            "model_version": self._system_inquiry_model_version(),
            "probes": []
            if session.system_inquiry_skipped
            else self._system_inquiry_probe_telemetry_records(session),
            "skip_event": bool(session.system_inquiry_skipped),
            "family_coverage": family_coverage,
            "leverage_distribution": (
                profile.get("leverage_distribution")
                if isinstance(profile.get("leverage_distribution"), dict)
                else {}
            ),
            "timings_ms": {},
        }

    def _system_inquiry_probe_telemetry_records(
        self,
        session: ChatSession,
    ) -> list[dict[str, object]]:
        annotations_by_probe: dict[str, dict[str, object]] = {}
        for annotation in session.system_inquiry_annotations or []:
            if not isinstance(annotation, dict):
                continue
            probe_id = str(annotation.get("probe_id") or "").strip()
            if probe_id and probe_id not in annotations_by_probe:
                annotations_by_probe[probe_id] = annotation

        candidates: list[dict[str, object]] = []
        seen: set[str] = set()
        candidate_sources = (
            [session.system_inquiry_candidate_audit or []]
            if session.system_inquiry_candidate_audit
            else [
                session.system_inquiry_observations or [],
                session.system_inquiry_held_observations or [],
                session.system_inquiry_annotations or [],
            ]
        )
        for source_items in candidate_sources:
            for source in source_items:
                if not isinstance(source, dict):
                    continue
                probe_id = str(source.get("probe_id") or "").strip()
                if not probe_id or probe_id in seen:
                    continue
                seen.add(probe_id)
                candidates.append(source)

        records: list[dict[str, object]] = []
        for candidate in candidates:
            probe_id = str(candidate.get("probe_id") or "").strip()
            annotation = annotations_by_probe.get(probe_id, {})
            status = str(
                candidate.get("candidate_status")
                or annotation.get("candidate_status")
                or ("selected" if annotation else "")
            )
            anchor_counts = (
                candidate.get("anchor_counts")
                if isinstance(candidate.get("anchor_counts"), dict)
                else annotation.get("anchor_counts")
                if isinstance(annotation.get("anchor_counts"), dict)
                else {}
            )
            records.append(
                {
                    "probe_id": probe_id,
                    "triggered": True,
                    "screened": bool(
                        candidate.get("screen_result")
                        if "screen_result" in candidate
                        else annotation.get("screen_result", True)
                    ),
                    "verify_votes": candidate.get(
                        "verify_votes",
                        annotation.get("verify_votes"),
                    ),
                    "anchor_valid": self._system_inquiry_telemetry_anchor_valid(
                        status,
                        anchor_counts,
                    ),
                    "corpus_label": str(
                        candidate.get("corpus_label")
                        or annotation.get("corpus_label")
                        or "unproven"
                    ),
                    "surfaced": bool(annotation) or status == "selected",
                    "resolution_state": str(
                        annotation.get("resolution_state")
                        or ("open" if bool(annotation) else "")
                    ),
                    "response_length_bucket": self._system_inquiry_response_length_bucket(
                        str(annotation.get("user_response") or "")
                    )
                    if annotation
                    else "",
                    "followup_used": bool(
                        str(annotation.get("followup_response") or "").strip()
                    ),
                }
            )
        return records

    @staticmethod
    def _system_inquiry_telemetry_anchor_valid(
        status: str,
        anchor_counts: dict[str, object],
    ) -> bool:
        if status == "discarded_no_anchor":
            return False
        return any(int(value or 0) > 0 for value in anchor_counts.values()) or status in {
            "selected",
            "held_cap",
        }

    @staticmethod
    def _system_inquiry_response_length_bucket(response: str) -> str:
        word_count = len([word for word in response.split() if word.strip()])
        if word_count <= 0:
            return "empty"
        if word_count < 8:
            return "short"
        if word_count < 35:
            return "medium"
        return "long"

    def _system_inquiry_model_version(self) -> str:
        settings = getattr(self, "settings", None)
        return str(getattr(settings, "ollama_model", "") or "")

    @staticmethod
    def _empty_system_inquiry_family_profile() -> dict[str, float | int]:
        return {
            "surfaced": 0,
            "addressed": 0,
            "partially": 0,
            "acknowledged": 0,
            "not_applicable_reasoned": 0,
            "open": 0,
            "unexamined_held_by_cap": 0,
            "_score": 0.0,
        }

    async def _adjudicate_system_inquiry_response_with_llm(
        self,
        session: ChatSession,
        observation: dict[str, object],
        user_response: str,
        *,
        allow_followup: bool = True,
    ) -> dict[str, object]:
        fallback = self._adjudicate_system_inquiry_response(
            session,
            observation,
            user_response,
            allow_followup=allow_followup,
        )
        attributes = (
            session.system_inquiry_attributes
            if isinstance(session.system_inquiry_attributes, dict)
            else {}
        )
        if str(attributes.get("extraction_method") or "") != "llm_constrained_v1":
            return fallback

        followup_type = self._system_inquiry_followup_type(observation)
        response = await _ask_llm_chat(
            context=(
                "Adjudicate a user's response to one system inquiry question. Return "
                "only JSON with resolution_state, evaluation, needs_followup, and "
                "followup_type. resolution_state must be one of addressed, "
                "partially_addressed, not_applicable_reasoned, acknowledged_unresolved, "
                "open. needs_followup may be true only for partially_addressed or open. "
                "Do not introduce new policy claims."
            ),
            messages=[
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "observation": {
                                "title": observation.get("title"),
                                "observation": observation.get("observation"),
                                "question": observation.get("question"),
                                "anchors": observation.get("anchors"),
                                "followup_types": observation.get("followup_types"),
                            },
                            "user_response": user_response,
                            "allow_followup": allow_followup,
                        },
                        ensure_ascii=True,
                    ),
                }
            ],
            temperature=0.0,
            max_tokens=450,
            response_format=self._system_inquiry_response_adjudication_schema(),
        )
        if is_llm_unavailable_response(response):
            return fallback
        parsed = parse_json_object(response) or {}
        state = str(parsed.get("resolution_state") or "").strip().casefold()
        allowed_states = {
            "addressed",
            "partially_addressed",
            "not_applicable_reasoned",
            "acknowledged_unresolved",
            "open",
        }
        if state not in allowed_states:
            return fallback
        needs_followup = (
            bool(parsed.get("needs_followup"))
            and allow_followup
            and state in {"partially_addressed", "open"}
        )
        evaluation = str(parsed.get("evaluation") or fallback["evaluation"]).strip()
        selected_followup_type = str(parsed.get("followup_type") or followup_type).strip()
        if selected_followup_type not in {
            "specify_mechanism",
            "name_group",
            "state_timeframe",
        }:
            selected_followup_type = followup_type
        return {
            "resolution_state": state,
            "evaluation": evaluation[:700],
            "followup_question": (
                self._system_inquiry_followup_question(
                    session,
                    observation,
                    selected_followup_type,
                    fallback=str(fallback.get("followup_question") or ""),
                )
                if needs_followup
                else ""
            ),
            "followup_type": selected_followup_type,
            "needs_followup": needs_followup,
            "adjudication_method": "llm_constrained_v1",
        }

    def _adjudicate_system_inquiry_response(
        self,
        session: ChatSession,
        observation: dict[str, object],
        user_response: str,
        *,
        allow_followup: bool = True,
    ) -> dict[str, object]:
        response = str(user_response or "").strip()
        normalized = normalize_for_match(response)
        compacted = compact_for_match(response)
        question = str(observation.get("question") or "this system inquiry").strip()
        title = str(observation.get("title") or "system inquiry").strip()
        groups = " / ".join(self._system_inquiry_group_labels(session)) or "the target group"
        followup_type = self._system_inquiry_followup_type(observation)

        if self._system_inquiry_is_reasoned_not_applicable(normalized):
            return {
                "resolution_state": "not_applicable_reasoned",
                "evaluation": "The response gives a reasoned basis for treating this inquiry as not applicable.",
                "followup_question": "",
                "needs_followup": False,
            }
        if self._system_inquiry_acknowledges_unresolved(normalized):
            return {
                "resolution_state": "acknowledged_unresolved",
                "evaluation": "The response acknowledges that this inquiry remains unresolved.",
                "followup_question": "",
                "needs_followup": False,
            }
        if self._system_inquiry_is_bare_dismissal(normalized):
            return {
                "resolution_state": "open",
                "evaluation": (
                    "The response dismisses the inquiry but does not explain why it is resolved "
                    "or not applicable."
                ),
                "followup_question": self._system_inquiry_followup_question(
                    session,
                    observation,
                    followup_type,
                    fallback=(
                        f"Briefly explain why {title.casefold()} does not apply here, "
                        f"or name the adjustment needed for {groups}."
                    ),
                ),
                "followup_type": followup_type,
                "needs_followup": allow_followup,
            }
        if len(compacted) < 40 or not self._system_inquiry_has_concrete_response_marker(
            normalized,
        ):
            return {
                "resolution_state": "partially_addressed",
                "evaluation": (
                    "The response is relevant, but it needs one concrete detail to close the inquiry."
                ),
                "followup_question": self._system_inquiry_followup_question(
                    session,
                    observation,
                    followup_type,
                    fallback=(
                        f"Add one specific implementation detail for this question: {question}"
                    ),
                ),
                "followup_type": followup_type,
                "needs_followup": allow_followup,
            }
        return {
            "resolution_state": "addressed",
            "evaluation": "The response provides a substantive reflection on the inquiry.",
            "followup_question": "",
            "needs_followup": False,
        }

    @staticmethod
    def _system_inquiry_followup_type(observation: dict[str, object]) -> str:
        followup_types = observation.get("followup_types")
        if isinstance(followup_types, list):
            for item in followup_types:
                cleaned = str(item or "").strip()
                if cleaned:
                    return cleaned
        return "specify_mechanism"

    def _system_inquiry_followup_question(
        self,
        session: ChatSession,
        observation: dict[str, object],
        followup_type: str,
        *,
        fallback: str,
    ) -> str:
        anchors = observation.get("anchors") if isinstance(observation.get("anchors"), dict) else {}
        groups = self._system_inquiry_group_labels(session)
        group_label = (
            str(anchors.get("shared_group") or "").strip()
            or (groups[0] if groups else "the target group")
        )
        hazard = str(anchors.get("hazard") or session.selected_hazard or "the hazard").strip()
        measure = str(
            anchors.get("measure") or session.mitigation_measure or "the measure"
        ).strip()
        if followup_type == "state_timeframe":
            return (
                f"Name the timeframe or ordering decision: what happens for "
                f"{group_label} before this measure is working, and when does that "
                "change?"
            )
        if followup_type == "name_group":
            omitted = anchors.get("omitted_groups")
            if isinstance(omitted, list) and omitted:
                group_label = str(omitted[0] or group_label).strip()
            return (
                f"Optional coverage check: would you like to include a mitigation "
                f"plan for {group_label} in {measure}, or keep the current target "
                "population as is?"
            )
        if followup_type == "specify_mechanism":
            return (
                f"Specify the mechanism: what rule, delivery route, funding source, "
                f"or responsible actor changes so {measure} addresses {hazard}?"
            )
        return fallback
