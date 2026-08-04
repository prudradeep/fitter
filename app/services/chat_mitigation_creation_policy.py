# ruff: noqa: F403,F405
from app.services.chat_mitigation_creation_common import *

async def _ask_llm_chat(*args, **kwargs):
    from app.services import chat_mitigation_creation as facade

    return await facade.ask_llm_chat(*args, **kwargs)


class ChatMitigationCreationPolicyMixin:
    def _selected_system_hazard_id(self, session: ChatSession) -> str | None:
        if session.sector_id is None or not session.selected_hazard:
            return None
        hazard_id = self.db.scalar(
            select(SystemHazard.id).where(
                SystemHazard.sector_id == session.sector_id,
                func.lower(SystemHazard.name) == session.selected_hazard.casefold(),
            )
        )
        return str(hazard_id) if hazard_id else None

    def _selected_system_profile_ids(
        self, session: ChatSession, system_hazard_id: str | None
    ) -> list[str]:
        if system_hazard_id is None:
            return []

        selected_profiles = self._selected_hazard_profile_names(session)
        selected_keys = {normalize(profile) for profile in selected_profiles if normalize(profile)}
        selected_variable_keys = {
            normalize(str(profile.get("variable_name") or ""))
            for profile in self._stored_hazard_profiles(
                session,
                session.selected_hazard or session.accepted_custom_hazard or "",
            )
            if normalize(str(profile.get("variable_name") or ""))
        }
        if not selected_keys and not selected_variable_keys:
            return []

        rows = self.db.execute(
            select(
                SystemHazardSocioDemographic.id,
                SystemHazardSocioDemographic.profile,
                SystemHazardSocioDemographic.variable_name,
            ).where(SystemHazardSocioDemographic.system_hazard_id == system_hazard_id)
        ).all()

        profile_ids: list[str] = []
        seen: set[str] = set()
        for row in rows:
            row_id = str(row.id)
            row_keys = {
                normalize(str(row.profile or "")),
                normalize(str(row.variable_name or "")),
            }
            if row_keys & selected_keys or row_keys & selected_variable_keys:
                if row_id not in seen:
                    seen.add(row_id)
                    profile_ids.append(row_id)
        return profile_ids

    def _matched_mitigation_measure_examples(
        self, session: ChatSession, limit: int | None = None
    ) -> str:
        rows = self._matched_mitigation_measure_example_rows(session, limit=limit)

        if not rows:
            return ""

        lines: list[str] = []
        for index, example in enumerate(rows, start=1):
            profile = str(example.profile_label or "Matched profile").strip()
            summary = self._simplify_mitigation_implementation_summary(
                str(example.implementation_summary or "")
            )
            evidence = str(example.evidence or "").strip()
            country = str(example.country_city or "").strip()
            case_study = str(example.policy_case_study or "").strip()
            reference_links = str(example.reference_links or "").strip()
            details: list[str] = [f"measure: {example.measure}"]
            if summary:
                details.append(f"implementation: {summary}")
            if evidence:
                details.append(f"evidence: {evidence}")
            if country:
                details.append(f"implemented country/city: {country}")
            if case_study or country:
                details.append(
                    f"case: {case_study}{f' ({country})' if country else ''}"
                )
            if reference_links:
                details.append(
                    "reference links: "
                    + self._format_mitigation_reference_links(reference_links)
                )
            lines.append(f"{index}. Profile '{profile}' - " + " | ".join(details))
        return "\n".join(lines)

    def _matched_mitigation_measure_example_rows(
        self, session: ChatSession, limit: int | None = None
    ) -> list[MitigationMeasureExample]:
        if session.sector_id is None:
            return []

        system_hazard_id = self._selected_system_hazard_id(session)

        filters = [
            MitigationMeasureExample.sector_id == session.sector_id,
            MitigationMeasureExample.source == "mm_csv",
        ]
        if system_hazard_id is not None:
            filters.append(MitigationMeasureExample.system_hazard_id == system_hazard_id)

        query = (
            select(MitigationMeasureExample)
            .where(*filters)
            .order_by(MitigationMeasureExample.csv_row_number, MitigationMeasureExample.id)
        )
        if limit is not None:
            query = query.limit(limit)
        rows = self.db.scalars(query).all()

        if not rows and system_hazard_id is not None:
            fallback_query = (
                select(MitigationMeasureExample)
                .where(
                    MitigationMeasureExample.sector_id == session.sector_id,
                    MitigationMeasureExample.source == "mm_csv",
                )
                .order_by(MitigationMeasureExample.csv_row_number, MitigationMeasureExample.id)
            )
            if limit is not None:
                fallback_query = fallback_query.limit(limit)
            rows = self.db.scalars(fallback_query).all()

        return list(rows)

    def _current_policy_implementations_section(
        self, session: ChatSession, limit: int | None = None
    ) -> str:
        rows = self._matched_mitigation_measure_example_rows(session, limit=limit)
        heading = self._policy_section_heading(
            "Current Policy Implementations",
            self._current_policy_implementations_intro(),
        )
        if not rows:
            return (
                f"{heading}\n\n"
                "No matching current policy implementations were found for this "
                "sector, hazard, and profile set."
            )

        sections = [heading]
        grouped_examples: dict[str, dict[str, object]] = {}
        for example in rows:
            measure = normalize_markdown_text(str(example.measure or "")).strip()
            if not measure:
                continue
            measure_key = normalize_for_match(measure)
            if not measure_key:
                continue
            group = grouped_examples.setdefault(
                measure_key,
                {
                    "measure": measure,
                    "countries": [],
                    "summaries": [],
                    "evidence": [],
                    "reference_links": [],
                },
            )

            country = normalize_markdown_text(str(example.country_city or "")).strip()
            evidence = normalize_markdown_text(str(example.evidence or "")).strip()
            reference_links = str(example.reference_links or "").strip()
            summary = self._simplify_mitigation_implementation_summary(
                str(example.implementation_summary or "")
            )
            case_study = normalize_markdown_text(str(example.policy_case_study or "")).strip()
            summary_text = summary or case_study

            self._append_unique_text(group["countries"], country)
            self._append_unique_text(group["summaries"], summary_text)
            self._append_unique_text(group["evidence"], evidence)
            for link in self._mitigation_reference_link_values(reference_links):
                self._append_unique_text(group["reference_links"], link)

        for group in list(grouped_examples.values())[:1]:
            measure = str(group["measure"])
            countries = group["countries"]
            summaries = group["summaries"]
            evidence_items = group["evidence"]
            reference_links = group["reference_links"]

            details: list[str] = []
            if countries:
                details.append(
                    "- **Implemented in:** " + "; ".join(str(item) for item in countries)
                )
            if summaries:
                details.append(
                    "- **Summary:** " + " ".join(str(item) for item in summaries)
                )
            if evidence_items:
                details.append(
                    "- **Evidence:** " + " ".join(str(item) for item in evidence_items)
                )
            if reference_links:
                details.append(
                    "- **Reference links:** "
                    + self._format_mitigation_reference_links("; ".join(str(item) for item in reference_links))
                )

            if not details:
                details.append("- No implementation details were provided for this example.")

            sections.append(
                f"### {self._normalize_current_policy_measure_title(measure)}\n\n"
                + "\n".join(details)
            )

        return "\n\n".join(sections)

    @classmethod
    def _ensure_practical_considerations_intro(cls, markdown: str) -> str:
        intro = (
            "This section translates the selected hazard and affected profiles into "
            "practical design considerations for mitigation. It highlights issues to "
            "check before choosing a measure, such as delivery barriers, targeting, "
            "and implementation risks."
        )
        heading = cls._policy_section_heading(
            "General considerations to mitigate the negative effects",
            intro,
        )
        cleaned = str(markdown or "").strip()
        if not cleaned:
            return heading
        cleaned = cls._strip_policy_section_heading(
            cleaned,
            "Practical Considerations",
        )
        cleaned = cls._strip_policy_section_heading(
            cleaned,
            "General considerations to mitigate the negative effects",
        )
        cleaned = cls._strip_section_intro_paragraph(
            cleaned,
            (
                "practical design considerations",
                "delivery barriers",
                "implementation risks",
                "design trade-offs",
            ),
        )
        if not cleaned:
            return heading
        if cleaned.casefold().lstrip().startswith("## practical considerations"):
            cleaned = cls._strip_policy_section_heading(
                cleaned,
                "Practical Considerations",
            )
        if cleaned.casefold().lstrip().startswith(
            "## general considerations to mitigate the negative effects"
        ):
            cleaned = cls._strip_policy_section_heading(
                cleaned,
                "General considerations to mitigate the negative effects",
            )
        return f"{heading}\n\n{cleaned}"

    @staticmethod
    def _current_policy_implementations_intro() -> str:
        return (
            "This section shows real policy implementations mitigating similar twin transition "
            "policy hazards, relevant to the selected sector and socio-demographic "
            "profiles. For each match, it summarizes where it has been implemented, "
            "the available evidence, and any reference links that support the example."
        )

    @staticmethod
    def _normalize_current_policy_measure_title(title: str) -> str:
        return normalize_current_policy_measure_title(title)

    @staticmethod
    def _new_policy_proposals_intro() -> str:
        return (
            "New policy proposals created using the data collection from open labs. The open labs followed a structured co-creation process that began with identifying twin-transition challenges and mapping their systemic causes. Participants then envisioned a fair future transition, translated the required systemic changes into new or improved policy measures, and finally refined and evaluated each proposal for its impact, feasibility, and contribution to an inclusive twin transition."
        )

    @staticmethod
    def _new_policy_proposals_title() -> str:
        return "New policy proposals (Inspiration for the regional mitigation plans)"

    @staticmethod
    def _policy_section_heading(title: str, tooltip: str) -> str:
        safe_title = escape(str(title or "").strip())
        safe_tooltip = escape(str(tooltip or "").strip())
        return (
            f'<h2 class="policy-section-heading">{safe_title} '
            '<span class="policy-section-info" tabindex="0" '
            f'aria-label="{safe_tooltip}" title="{safe_tooltip}">'
            '<span aria-hidden="true">i</span>'
            f'<span class="policy-section-tooltip" aria-hidden="true">{safe_tooltip}</span>'
            "</span></h2>"
        )

    @staticmethod
    def _strip_policy_section_heading(markdown: str, title: str) -> str:
        title_key = normalize_for_match(title)
        kept: list[str] = []
        for line in str(markdown or "").splitlines():
            heading_text = re.sub(r"^\s*#{1,6}\s*", "", line).strip().strip("*_:- ")
            if normalize_for_match(heading_text) == title_key:
                continue
            kept.append(line)
        return "\n".join(kept).strip()

    @staticmethod
    def _strip_section_intro_paragraph(markdown: str, markers: tuple[str, ...]) -> str:
        cleaned = str(markdown or "").strip()
        if not cleaned:
            return ""
        parts = re.split(r"\n\s*\n", cleaned, maxsplit=1)
        first = parts[0].strip()
        first_key = first.casefold()
        if first and any(marker.casefold() in first_key for marker in markers):
            return parts[1].strip() if len(parts) > 1 else ""
        return cleaned

    @classmethod
    def _practical_considerations_json_to_markdown(cls, response: str) -> tuple[str, list[str]]:
        raw = str(response or "").strip()
        if not raw:
            return "", []
        payload = parse_json_object(raw)
        if payload is None:
            return raw, []
        if not isinstance(payload, dict):
            return raw, []

        title = cls._clean_practical_json_text(
            payload.get("title"),
            default="# Practical Considerations",
        )
        sections: list[str] = [title]
        panel_items: list[str] = []
        seen_panel_items: set[str] = set()
        themes = payload.get("themes")
        if not isinstance(themes, list):
            themes = []

        for theme in themes:
            if not isinstance(theme, dict):
                continue
            heading = cls._clean_practical_json_text(theme.get("heading"))
            heading_title = cls._markdown_heading_title(heading)
            if not heading_title or cls._is_practical_placeholder_text(heading_title):
                continue
            heading = f"## {heading_title}"
            panel_key = normalize_for_match(heading_title)
            if panel_key and panel_key not in seen_panel_items:
                seen_panel_items.add(panel_key)
                panel_items.append(heading_title)

            block: list[str] = [heading]
            summary = cls._clean_practical_json_text(theme.get("summary"))
            if summary:
                block.extend(["", summary])

            concerns = theme.get("concerns")
            if isinstance(concerns, list):
                cleaned_concerns = [
                    cls._clean_practical_json_bullet(concern)
                    for concern in concerns
                ]
                cleaned_concerns = [concern for concern in cleaned_concerns if concern]
                if cleaned_concerns:
                    block.extend(["", *cleaned_concerns])

            sections.append("\n".join(block).strip())

        return "\n\n".join(section for section in sections if section.strip()), panel_items

    @staticmethod
    def _clean_practical_json_text(value: object, default: str = "") -> str:
        cleaned = str(value or "").strip()
        cleaned = re.sub(r"^```(?:json|markdown|md)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned or default

    @classmethod
    def _clean_practical_json_bullet(cls, value: object) -> str:
        cleaned = cls._clean_practical_json_text(value)
        if not cleaned:
            return ""
        cleaned = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", cleaned).strip()
        return f"- {cleaned}" if cleaned else ""

    @classmethod
    def _markdown_heading_title(cls, value: object) -> str:
        cleaned = cls._clean_practical_json_text(value)
        cleaned = re.sub(r"^\s*#{1,6}\s*", "", cleaned).strip()
        cleaned = re.sub(r"\*\*(.*?)\*\*", r"\1", cleaned)
        cleaned = re.sub(r"\*(.*?)\*", r"\1", cleaned)
        return cleaned.strip(" -#:\t\r\n")

    @staticmethod
    def _is_practical_placeholder_text(value: object) -> bool:
        normalized = normalize_for_match(str(value or ""))
        return normalized in {
            "dynamic theme heading",
            "markdown paragraph summarising the theme",
            "markdown paragraph summarizing the theme",
            "markdown bullet point",
        } or (
            "dynamic theme heading" in normalized
            or normalized.startswith("markdown paragraph")
            or normalized.startswith("markdown bullet")
        )

    @classmethod
    def _extract_practical_consideration_items(cls, markdown: str) -> list[str]:
        cleaned = str(markdown or "")
        cleaned = re.sub(
            r'<h[1-6][^>]*class="[^"]*\bpolicy-section-heading\b[^"]*"[^>]*>.*?</h[1-6]>',
            "",
            cleaned,
            flags=re.IGNORECASE | re.DOTALL,
        )
        cleaned = re.sub(
            r'<span[^>]*class="[^"]*\bpolicy-section-tooltip\b[^"]*"[^>]*>.*?</span>',
            "",
            cleaned,
            flags=re.IGNORECASE | re.DOTALL,
        )
        cleaned = cls._strip_policy_section_heading(cleaned, "Practical Considerations")
        cleaned = cls._strip_policy_section_heading(
            cleaned,
            "General considerations to mitigate the negative effects",
        )
        cleaned = re.sub(r"<[^>]+>", " ", cleaned)
        items: list[str] = []
        current: list[str] = []

        def flush_current() -> None:
            if not current:
                return
            item = cls._clean_practical_consideration_item(" ".join(current))
            current.clear()
            if item and normalize_for_match(item) not in {
                normalize_for_match(existing) for existing in items
            }:
                items.append(item)

        skipping_nested_bullet = False
        for raw_line in cleaned.splitlines():
            line = raw_line.strip()
            leading_whitespace = len(raw_line) - len(raw_line.lstrip(" \t"))
            if not line:
                flush_current()
                skipping_nested_bullet = False
                continue
            if re.match(r"^\s*#{1,6}\s+", line):
                flush_current()
                skipping_nested_bullet = False
                continue
            bullet_match = re.match(r"^\s*(?:[-*•]|\d+[.)])\s+(.+)$", line)
            if bullet_match:
                if leading_whitespace >= 2:
                    skipping_nested_bullet = True
                    continue
                flush_current()
                skipping_nested_bullet = False
                current.append(bullet_match.group(1).strip())
                continue
            if skipping_nested_bullet:
                continue
            if current:
                current.append(line)
            elif len(line) > 24:
                current.append(line)

        flush_current()
        return items

    @staticmethod
    def _clean_practical_consideration_item(value: str) -> str:
        cleaned = normalize_markdown_text(str(value or ""))
        cleaned = re.sub(r"\*\*(.*?)\*\*", r"\1", cleaned)
        cleaned = re.sub(r"\*(.*?)\*", r"\1", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" -•\t\r\n")
        if normalize_for_match(cleaned).startswith(
            normalize_for_match("Practical Considerations This section translates")
        ):
            return ""
        if normalize_for_match(cleaned).startswith(
            normalize_for_match("Practical Considerations i This section translates")
        ):
            return ""
        colon_index = cleaned.find(":")
        if 4 <= colon_index <= 120:
            cleaned = cleaned[:colon_index]
        else:
            sentence_match = re.match(r"^(.{18,120}?[.!?])\s+", cleaned)
            if sentence_match:
                cleaned = sentence_match.group(1).rstrip(".!?")
        return cleaned.strip(" -•\t\r\n.:")

    async def _new_policy_suggestions_section(
        self,
        session: ChatSession,
        *,
        limit: int = 3,
    ) -> str:
        candidates = self._ranked_new_policy_suggestions(session, limit=limit)
        intro = self._new_policy_proposals_intro()
        heading = self._policy_section_heading(self._new_policy_proposals_title(), intro)
        if not candidates:
            return (
                f"{heading}\n\n"
                "No matching policy proposals were found for this country, sector, "
                "and selected hazard context."
            )

        candidate_context = self._new_policy_suggestion_context(candidates)
        current_policy_context = self._matched_mitigation_measure_examples(session, limit=5)
        context = load_nested_prompt_file("llm/new_policy_suggestion.txt")
        messages = [
            {
                "role": "user",
                "content": (
                    "Create the markdown body for the policy proposal section. "
                    "Do not include the section heading and do not include an "
                    "introductory paragraph; start directly with ONE synthesized "
                    "proposal.\n\n"
                    "Output exactly one proposal, 150-200 words total, using this structure:\n"
                    "### [short proposal title]\n"
                    "- **Proposal:** one clear, user-ready mitigation measure sentence "
                    "tailored to the selected country and region where possible.\n"
                    "- **Top policy basis:** mention that it combines the strongest/top-scored "
                    "MM policy proposals; name only the most relevant policy codes/titles.\n"
                    "- **Target-group mechanisms:** short bullets explaining how each covered "
                    "target group is mitigated.\n"
                    "- **Why this helps:** one short sentence linking the combined measure to "
                    "the selected hazard and high proposal scores.\n\n"
                    "Do not output multiple policy candidates. Do not include a score table. "
                    "Keep it concise and make the proposal sound like a single coherent "
                    "regional mitigation measure that inspires the user to create their own.\n\n"
                    f"Selected country: {session.country or 'Not specified'}\n"
                    f"Selected region: {session.region or 'Not specified'}\n"
                    f"Selected sector: {session.sector or 'Not specified'}\n"
                    f"Selected hazard: {session.selected_hazard or session.accepted_custom_hazard or 'Not specified'}\n"
                    f"Selected socio-demographic profiles:\n{format_all_dgs(session)}\n\n"
                    "Current policy implementation context:\n"
                    f"{current_policy_context or '- No matching current implementation context was found.'}\n\n"
                    f"Candidate policy context:\n{candidate_context}"
                ),
            }
        ]
        for attempt in range(2):
            attempt_messages = messages
            if attempt:
                retry_instruction = {
                    "role": "user",
                    "content": (
                        "Retry once. The previous response could not be used. "
                        "Return only the requested markdown body with the exact "
                        "proposal structure and no introductory paragraph."
                    ),
                }
                attempt_messages = [*messages, retry_instruction]
            response = await _ask_llm_chat(
                context=context,
                messages=attempt_messages,
                temperature=0.2,
                max_tokens=1000,
            )
            if response and not is_llm_unavailable_response(response):
                cleaned = self._strip_new_policy_suggestions_heading(response)
                if cleaned:
                    ensured = self._ensure_new_policy_intro(cleaned)
                    if ensured:
                        return heading + "\n\n" + self._format_new_policy_proposal_body(ensured)

        return (
            f"{heading}\n\n"
            "I could not generate a reliable new policy proposal from the matched "
            "policy basis after retrying. Please try again, or continue by writing "
            "your own regional mitigation measure."
        )

    @classmethod
    def _strip_new_policy_suggestions_heading(cls, markdown: str) -> str:
        lines = []
        heading_keys = {
            normalize_for_match("new policy proposals"),
            normalize_for_match(cls._new_policy_proposals_title()),
        }
        for line in str(markdown or "").strip().splitlines():
            heading_text = re.sub(r"^\s*#{1,6}\s*", "", line).strip()
            heading_text = heading_text.strip("*_:- ")
            if normalize_for_match(heading_text) in heading_keys:
                continue
            lines.append(line)
        return "\n".join(lines).strip()

    def _ranked_new_policy_suggestions(
        self,
        session: ChatSession,
        *,
        limit: int = 3,
    ) -> list[dict[str, object]]:
        if session.country_id is None or session.sector_id is None:
            return []

        selected_system_hazard_id = self._selected_system_hazard_id(session)
        hazard_target_option_ids = self._selected_system_hazard_target_option_ids(
            session,
            selected_system_hazard_id,
        )

        for require_selected_country in (True, False):
            policy_rows = self._new_policy_suggestion_policy_rows(
                session,
                selected_system_hazard_id,
                require_selected_country=require_selected_country,
            )
            if not policy_rows:
                continue
            candidates = self._new_policy_suggestion_candidates_from_rows(
                policy_rows,
                hazard_target_option_ids,
            )
            if candidates:
                return candidates[:limit]
        return []

    def _new_policy_suggestion_policy_rows(
        self,
        session: ChatSession,
        selected_system_hazard_id: str | None,
        *,
        require_selected_country: bool,
    ) -> list[dict[str, object]]:
        query = (
            select(
                MitigationMeasurePolicy.id,
                MitigationMeasurePolicy.policy_code,
                MitigationMeasurePolicy.policy_title,
                MitigationMeasurePolicy.policy_type,
                MitigationMeasurePolicy.short_description,
                MitigationMeasurePolicy.country_id,
                MitigationMeasurePolicySystemHazard.system_hazard_id,
                MitigationMeasurePolicySystemHazard.mitigation_effect,
                SystemHazard.name.label("hazard_name"),
            )
            .join(
                MitigationMeasurePolicySystemHazard,
                MitigationMeasurePolicySystemHazard.mitigation_measure_policy_id
                == MitigationMeasurePolicy.id,
            )
            .join(
                SystemHazard,
                SystemHazard.id == MitigationMeasurePolicySystemHazard.system_hazard_id,
            )
            .where(
                MitigationMeasurePolicy.country_id == session.country_id,
                MitigationMeasurePolicy.sector_id == session.sector_id,
                MitigationMeasurePolicy.source == "xlsx",
            )
        )
        if require_selected_country:
            query = query.where(MitigationMeasurePolicy.country_id == session.country_id)
        if selected_system_hazard_id is not None:
            query = query.where(
                MitigationMeasurePolicySystemHazard.system_hazard_id
                == selected_system_hazard_id
            )

        return list(self.db.execute(query).mappings().all())

    def _new_policy_suggestion_candidates_from_rows(
        self,
        policy_rows: list[dict[str, object]],
        hazard_target_option_ids: set[str],
    ) -> list[dict[str, object]]:
        if not policy_rows:
            return []

        policy_ids = [str(row["id"]) for row in policy_rows]
        target_rows = self.db.execute(
            select(
                MitigationMeasureTargetGroup.mitigation_measure_policy_id.label("policy_id"),
                MitigationMeasureTargetGroup.question_option_id,
                MitigationMeasureTargetGroup.match_value,
                EvaluationQuestion.question,
                QuestionOption.option,
            )
            .join(
                QuestionOption,
                QuestionOption.id == MitigationMeasureTargetGroup.question_option_id,
            )
            .join(
                EvaluationQuestion,
                and_(
                    EvaluationQuestion.id == QuestionOption.question_id,
                    EvaluationQuestion.category == "target_population",
                ),
            )
            .where(MitigationMeasureTargetGroup.mitigation_measure_policy_id.in_(policy_ids))
            .order_by(
                MitigationMeasureTargetGroup.mitigation_measure_policy_id,
                EvaluationQuestion.question,
                QuestionOption.option,
            )
        ).mappings().all()

        targets_by_policy: dict[str, list[dict[str, object]]] = {}
        for row in target_rows:
            policy_id = str(row["policy_id"])
            label = self._target_population_label(
                str(row["question"] or ""),
                str(row["option"] or ""),
            )
            targets_by_policy.setdefault(policy_id, []).append(
                {
                    "question_option_id": str(row["question_option_id"]),
                    "label": label,
                    "match_value": str(row["match_value"] or "").strip(),
                }
            )

        candidates: list[dict[str, object]] = []
        for row in policy_rows:
            policy_id = str(row["id"])
            target_groups = targets_by_policy.get(policy_id, [])
            score_details = self._new_policy_suggestion_score(
                mitigation_effect=str(row["mitigation_effect"] or ""),
                target_groups=target_groups,
                hazard_target_option_ids=hazard_target_option_ids,
            )
            if score_details["score"] <= 0:
                continue
            candidates.append(
                {
                    "policy_id": policy_id,
                    "policy_code": str(row["policy_code"] or ""),
                    "policy_title": normalize_markdown_text(str(row["policy_title"] or "")).strip(),
                    "policy_type": normalize_markdown_text(str(row["policy_type"] or "")).strip(),
                    "short_description": normalize_markdown_text(
                        str(row["short_description"] or "")
                    ).strip(),
                    "hazard_name": normalize_markdown_text(str(row["hazard_name"] or "")).strip(),
                    "mitigation_effect": str(row["mitigation_effect"] or "").strip(),
                    "target_groups": target_groups,
                    **score_details,
                }
            )

        candidates.sort(
            key=lambda candidate: (
                float(candidate.get("score") or 0),
                float(candidate.get("hazard_effect_score") or 0),
                float(candidate.get("target_match_score") or 0),
                str(candidate.get("policy_title") or ""),
            ),
            reverse=True,
        )
        return candidates

    def _selected_system_hazard_target_option_ids(
        self,
        session: ChatSession,
        system_hazard_id: str | None,
    ) -> set[str]:
        if system_hazard_id is None:
            return self._selected_target_population_option_ids(session)

        profile_ids = self._selected_system_profile_ids(session, system_hazard_id)
        if not profile_ids:
            profile_ids = [
                str(row_id)
                for row_id in self.db.scalars(
                    select(SystemHazardSocioDemographic.id).where(
                        SystemHazardSocioDemographic.system_hazard_id
                        == system_hazard_id
                    )
                ).all()
            ]
        if not profile_ids:
            return self._selected_target_population_option_ids(session)

        option_ids = {
            str(option_id)
            for option_id in self.db.scalars(
                select(
                    SystemHazardSocioDemographicTargetPopulation.question_option_id
                ).where(
                    SystemHazardSocioDemographicTargetPopulation.system_hazard_socio_demographic_id.in_(
                        profile_ids
                    )
                )
            ).all()
        }
        return option_ids or self._selected_target_population_option_ids(session)

    def _selected_target_population_option_ids(self, session: ChatSession) -> set[str]:
        answer_pairs: set[tuple[str, str]] = set()
        for answer in session.target_population_answers or []:
            question = normalize_for_match(str(answer.get("question") or ""))
            selected = answer.get("selected")
            labels = selected if isinstance(selected, list) else str(answer.get("answer") or "").split(",")
            for label in labels:
                option = normalize_for_match(str(label or ""))
                if question and option:
                    answer_pairs.add((question, option))
        if not answer_pairs:
            return set()

        rows = self.db.execute(
            select(
                QuestionOption.id,
                EvaluationQuestion.question,
                QuestionOption.option,
            )
            .join(EvaluationQuestion, EvaluationQuestion.id == QuestionOption.question_id)
            .where(EvaluationQuestion.category == "target_population")
        ).all()
        return {
            str(row.id)
            for row in rows
            if (
                normalize_for_match(str(row.question or "")),
                normalize_for_match(str(row.option or "")),
            )
            in answer_pairs
        }

    @staticmethod
    def _new_policy_suggestion_score(
        *,
        mitigation_effect: str,
        target_groups: list[dict[str, object]],
        hazard_target_option_ids: set[str],
    ) -> dict[str, object]:
        effect_key = normalize_for_match(mitigation_effect)
        hazard_effect_score = {
            "high mitigation": 60.0,
            "medium mitigation": 35.0,
            "low mitigation": 15.0,
        }.get(effect_key, 0.0)

        value_scores = {
            "yes": 12.0,
            "partially": 6.0,
        }
        matched_targets: list[dict[str, object]] = []
        target_match_score = 0.0
        if not hazard_target_option_ids:
            return {
                "score": round(hazard_effect_score, 2),
                "hazard_effect_score": hazard_effect_score,
                "target_match_score": 0.0,
                "matched_target_groups": matched_targets,
                "hazard_target_option_count": 0,
            }
        for group in target_groups:
            option_id = str(group.get("question_option_id") or "").strip()
            if not option_id:
                continue
            if option_id not in hazard_target_option_ids:
                continue
            value = str(group.get("match_value") or "").strip()
            value_score = value_scores.get(value.casefold(), 0.0)
            if value_score <= 0:
                continue
            matched_targets.append(group)
            target_match_score += value_score

        target_match_score = min(40.0, target_match_score)
        return {
            "score": round(hazard_effect_score + target_match_score, 2),
            "hazard_effect_score": hazard_effect_score,
            "target_match_score": round(target_match_score, 2),
            "matched_target_groups": matched_targets,
            "hazard_target_option_count": len(hazard_target_option_ids),
        }

    def _new_policy_suggestion_context(self, candidates: list[dict[str, object]]) -> str:
        lines: list[str] = []
        for index, candidate in enumerate(candidates, start=1):
            matched_targets = candidate.get("matched_target_groups")
            target_groups = candidate.get("target_groups")
            matched_labels = self._policy_target_group_summary(
                matched_targets if isinstance(matched_targets, list) else []
            )
            all_target_labels = self._policy_target_group_summary(
                target_groups if isinstance(target_groups, list) else []
            )
            lines.append(
                "\n".join(
                    [
                        f"{index}. Policy code: {candidate.get('policy_code')}",
                        f"   Title: {candidate.get('policy_title')}",
                        f"   Type: {candidate.get('policy_type') or 'Not specified'}",
                        f"   Description: {candidate.get('short_description') or 'Not specified'}",
                        f"   Related system hazard: {candidate.get('hazard_name')}",
                        f"   Hazard mitigation effect: {candidate.get('mitigation_effect')}",
                        f"   Matched target groups: {matched_labels or 'None'}",
                        f"   All policy target groups: {all_target_labels or 'None'}",
                        (
                            f"   Score: {candidate.get('score')}/100 "
                            f"(hazard effect {candidate.get('hazard_effect_score')}, "
                            f"target match {candidate.get('target_match_score')})"
                        ),
                    ]
                )
            )
        return "\n\n".join(lines)

    def _fallback_new_policy_suggestions_section(
        self,
        session: ChatSession,
        candidates: list[dict[str, object]],
    ) -> str:
        sections = [
            self._policy_section_heading(
                self._new_policy_proposals_title(),
                self._new_policy_proposals_intro(),
            )
        ]
        top_candidate = candidates[0]
        matched_targets: list[dict[str, object]] = []
        all_targets: list[dict[str, object]] = []
        source_policy_lines: list[str] = []
        action_parts: list[str] = []
        for candidate in candidates:
            candidate_targets = candidate.get("matched_target_groups")
            if isinstance(candidate_targets, list):
                matched_targets.extend(candidate_targets)
            candidate_all_targets = candidate.get("target_groups")
            if isinstance(candidate_all_targets, list):
                all_targets.extend(candidate_all_targets)
            title = str(candidate.get("policy_title") or "Untitled policy").strip()
            code = str(candidate.get("policy_code") or "Policy").strip()
            effect = str(candidate.get("mitigation_effect") or "relevant mitigation effect").strip()
            description = str(candidate.get("short_description") or "").strip()
            source_policy_lines.append(
                f"{code}: {title}"
                + (f" ({effect})" if effect else "")
            )
            if description:
                self._append_unique_text(action_parts, description)

        target_groups = matched_targets or all_targets
        target_mechanisms = self._fallback_target_group_mechanisms(target_groups)
        policy_basis = "; ".join(source_policy_lines[:3])
        hazard_name = str(top_candidate.get("hazard_name") or "the selected hazard").strip()
        effect_label = str(top_candidate.get("mitigation_effect") or "relevant").strip()
        action_summary = (
            action_parts[:3]
            if action_parts
            else [f"Adapt the strongest scored policy actions to reduce {hazard_name.lower()}."]
        )
        body = (
            "### Integrated Regional Mitigation Support Package\n\n"
            f"- **Proposal:** In {self._session_place_label_for_sentence(session)}, "
            f"combine the top-scored MM policy proposals into a regional support package "
            f"that reduces **{hazard_name}** through targeted assistance, delivery "
            "guidance, and safeguards for affected groups.\n"
            f"- **Top policy basis:** {policy_basis or 'Top-ranked MM policy proposals'}.\n"
            "- **Target-group mechanisms:**\n"
            + "\n".join(f"    - {item}" for item in target_mechanisms)
            + "\n"
            f"- **Why this helps:** The proposal is a strong inspiration because its source "
            f"policies have **{effect_label}** mitigation relevance and combine complementary "
            "actions: "
            + "; ".join(action_summary[:2])
            + "."
        )
        sections.append(self._format_new_policy_proposal_body(body))
        return "\n\n".join(sections)

    @staticmethod
    def _session_place_label_for_sentence(session: ChatSession) -> str:
        region = str(session.region or "").strip()
        country = str(session.country or "").strip()
        if region and country:
            return f"{region}, {country}"
        return region or country or "the selected region"

    def _fallback_target_group_mechanisms(
        self,
        target_groups: list[dict[str, object]],
        *,
        limit: int = 5,
    ) -> list[str]:
        labels: list[str] = []
        seen: set[str] = set()
        for group in target_groups:
            label = str(group.get("label") or "").strip()
            value = str(group.get("match_value") or "").strip().casefold()
            if not label or value == "pp":
                continue
            key = normalize_for_match(label)
            if key and key not in seen:
                seen.add(key)
                labels.append(label)
        if not labels:
            return [
                "Affected profiles — use the selected hazard profiles to target support and prevent exclusion."
            ]
        return [
            f"{label} — receives tailored support, reduced exposure to the hazard, and clearer access to the measure."
            for label in labels[:limit]
        ]

    @classmethod
    def _ensure_new_policy_intro(cls, markdown: str) -> str:
        cleaned = str(markdown or "").strip()
        if not cleaned:
            return ""
        cleaned = cls._strip_section_intro_paragraph(
            cleaned,
            (
                "candidate policies",
                "hazard mitigation effect",
                "target-group overlap",
                "policy database",
            ),
        )
        return cleaned

    @classmethod
    def _format_new_policy_proposal_body(cls, markdown: str) -> str:
        cleaned = cls._normalize_target_group_mechanism_indentation(markdown)
        return cls._append_top_policy_basis_to_proposal(cleaned)

    @staticmethod
    def _normalize_target_group_mechanism_indentation(markdown: str) -> str:
        lines: list[str] = []
        in_target_group_block = False
        for raw_line in str(markdown or "").splitlines():
            line = raw_line.rstrip()
            section_key = normalize_for_match(line)
            if "target group mechanisms" in section_key:
                in_target_group_block = True
                lines.append(line)
                continue
            if in_target_group_block and re.match(r"^\s*[-*]\s+\*\*(?:why this helps|proposal|top policy basis)\s*:", line, flags=re.IGNORECASE):
                in_target_group_block = False
            if in_target_group_block and re.match(r"^\s{0,3}[-*]\s+", line):
                lines.append("    " + line.lstrip())
                continue
            lines.append(line)
        return "\n".join(lines).strip()

    @classmethod
    def _append_top_policy_basis_to_proposal(cls, markdown: str) -> str:
        text = str(markdown or "").strip()
        basis_match = re.search(
            r"(?im)^\s*[-*]\s*\*\*Top policy basis:\*\*\s*(?P<basis>.+?)\s*$",
            text,
        )
        if not basis_match:
            return text

        basis = cls._clean_policy_basis_source(basis_match.group("basis"))
        text_without_basis = (
            text[: basis_match.start()] + text[basis_match.end() :]
        ).strip()
        if not basis:
            return re.sub(r"\n{3,}", "\n\n", text_without_basis)

        def append_source(match: re.Match[str]) -> str:
            proposal = match.group("proposal").rstrip()
            proposal = cls._strip_policy_source_reference(proposal)
            safe_basis = escape(basis)
            return (
                f"{match.group('prefix')}{proposal} "
                '<span class="policy-section-info proposal-source-info" '
                f'tabindex="0" aria-label="{safe_basis}" title="{safe_basis}">'
                '<span aria-hidden="true">i</span>'
                f'<span class="policy-section-tooltip" aria-hidden="true">{safe_basis}</span>'
                "</span>"
            )

        updated, count = re.subn(
            r"(?im)^(?P<prefix>\s*[-*]\s*\*\*Proposal:\*\*\s*)(?P<proposal>.+?)\s*$",
            append_source,
            text_without_basis,
            count=1,
        )
        return re.sub(r"\n{3,}", "\n\n", updated if count else text_without_basis).strip()

    @staticmethod
    def _clean_policy_basis_source(value: str) -> str:
        cleaned = normalize_markdown_text(str(value or "")).strip()
        cleaned = re.sub(r"\*\*(.*?)\*\*", r"\1", cleaned)
        cleaned = re.sub(r"\*(.*?)\*", r"\1", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" .;")
        return cleaned

    @staticmethod
    def _strip_policy_source_reference(value: str) -> str:
        return re.sub(
            r"\s*\[(?:Source|Sources):\s*[^\]]+\]\s*$",
            "",
            str(value or "").strip(),
            flags=re.IGNORECASE,
        ).strip()

    @classmethod
    def _extract_suggested_policy_proposal(cls, markdown: str) -> str:
        text = str(markdown or "")
        title = ""
        title_match = re.search(r"(?im)^\s*###\s+(.+?)\s*$", text)
        if title_match:
            title = normalize_markdown_text(title_match.group(1))
            title = re.sub(r"\*\*(.*?)\*\*", r"\1", title)
            title = re.sub(r"\*(.*?)\*", r"\1", title)
            title = re.sub(r"\s+", " ", title).strip()
        match = re.search(
            r"(?im)^\s*[-*]\s*\*\*Proposal:\*\*\s*(.+?)\s*$",
            text,
        )
        if not match:
            match = re.search(r"(?im)^\s*[-*]\s*Proposal:\s*(.+?)\s*$", text)
        if not match:
            return ""
        proposal = normalize_markdown_text(match.group(1))
        proposal = re.sub(r"\*\*(.*?)\*\*", r"\1", proposal)
        proposal = re.sub(r"\*(.*?)\*", r"\1", proposal)
        proposal = cls._strip_policy_source_reference(proposal)
        proposal = re.sub(r"\s+", " ", proposal).strip()
        if title and proposal and not normalize_for_match(proposal).startswith(
            normalize_for_match(title)
        ):
            return f"{title}: {proposal}"
        return proposal

    @staticmethod
    def _extract_suggested_policy_reason(markdown: str) -> str:
        text = str(markdown or "")
        match = re.search(
            r"(?im)^\s*[-*]\s*\*\*Why this helps:\*\*\s*(.+?)\s*$",
            text,
        )
        if not match:
            match = re.search(r"(?im)^\s*[-*]\s*Why this helps:\s*(.+?)\s*$", text)
        if not match:
            return ""
        reason = normalize_markdown_text(match.group(1))
        reason = re.sub(r"\*\*(.*?)\*\*", r"\1", reason)
        reason = re.sub(r"\*(.*?)\*", r"\1", reason)
        return re.sub(r"\s+", " ", reason).strip()

    @staticmethod
    def _extract_suggested_policy_target_group_mechanisms(markdown: str) -> str:
        text = str(markdown or "")
        lines = text.splitlines()
        captured: list[str] = []
        in_block = False
        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                if in_block and captured:
                    break
                continue
            start_match = re.match(
                r"(?i)^[-*]?\s*\*\*Target-group mechanisms:\*\*\s*(.*)$",
                line,
            ) or re.match(
                r"(?i)^[-*]?\s*Target-group mechanisms:\s*(.*)$",
                line,
            )
            if start_match:
                in_block = True
                remainder = start_match.group(1).strip()
                if remainder:
                    captured.append(remainder)
                continue
            if not in_block:
                continue
            if re.match(r"^#{1,6}\s+", line):
                break
            if re.match(r"^[-*]\s*\*\*[^*]+:\*\*", raw_line):
                break
            if re.match(r"^[-*]\s*[A-Za-z][^:]{1,80}:\s+", raw_line) and captured:
                break
            captured.append(line)
        cleaned_items: list[str] = []
        for item in captured:
            cleaned = normalize_markdown_text(item)
            cleaned = re.sub(r"^\s*[-*]\s*", "", cleaned)
            cleaned = re.sub(r"\*\*(.*?)\*\*", r"\1", cleaned)
            cleaned = re.sub(r"\*(.*?)\*", r"\1", cleaned)
            cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
            if cleaned:
                cleaned_items.append(cleaned)
        return "; ".join(cleaned_items).strip()

    @classmethod
    def _extract_target_groups_from_mechanisms(cls, mechanisms: str) -> list[str]:
        labels: list[str] = []
        seen: set[str] = set()
        for item in re.split(r";|\n", str(mechanisms or "")):
            cleaned = normalize_markdown_text(item)
            cleaned = re.sub(r"^\s*[-*]\s*", "", cleaned)
            cleaned = re.sub(r"\*\*(.*?)\*\*", r"\1", cleaned)
            cleaned = re.sub(r"\*(.*?)\*", r"\1", cleaned)
            if ":" not in cleaned:
                continue
            label = re.sub(r"\s+", " ", cleaned.split(":", 1)[0]).strip(" .,:;")
            key = normalize(label)
            if (
                key
                and key not in seen
                and cls._is_valid_custom_target_population_group(label)
            ):
                seen.add(key)
                labels.append(label)
        return labels

    @staticmethod
    def _policy_target_group_summary(target_groups: list[dict[str, object]]) -> str:
        labels: list[str] = []
        seen: set[str] = set()
        for group in target_groups:
            label = str(group.get("label") or "").strip()
            value = str(group.get("match_value") or "").strip()
            if value.casefold() == "pp":
                continue
            if not label:
                continue
            rendered = f"{label} ({value})" if value else label
            key = normalize_for_match(rendered)
            if key and key not in seen:
                seen.add(key)
                labels.append(rendered)
        return "; ".join(labels)

    @staticmethod
    def _target_population_label(question: str, option: str) -> str:
        question = str(question or "").strip()
        option = str(option or "").strip()
        if question and option:
            return f"{question}: {option}"
        return question or option

    def _current_policy_mitigation_measure(self, session: ChatSession) -> str:
        for example in self._matched_mitigation_measure_example_rows(session, limit=None):
            measure = normalize_markdown_text(str(example.measure or "")).strip()
            if measure:
                return measure
        return ""

    @staticmethod
    def _append_unique_text(items: object, value: str) -> None:
        if not isinstance(items, list):
            return
        cleaned = re.sub(r"\s+", " ", str(value or "")).strip()
        if not cleaned:
            return
        if normalize_for_match(cleaned) not in {normalize_for_match(str(item)) for item in items}:
            items.append(cleaned)

    @staticmethod
    def _mitigation_reference_link_values(reference_links: str) -> list[str]:
        return mitigation_reference_link_values(reference_links)

    @staticmethod
    def _simplify_mitigation_implementation_summary(summary: str) -> str:
        return simplify_mitigation_implementation_summary(summary)

    @staticmethod
    def _format_mitigation_reference_links(reference_links: str) -> str:
        return format_mitigation_reference_links(reference_links)




    def _existing_mitigation_records_for_selected_hazard(
        self, session: ChatSession
    ) -> list[UserMitigationMeasure]:
        hazard_name = session.selected_hazard or session.accepted_custom_hazard
        if not hazard_name:
            return []
        try:
            if self._is_saved_custom_hazard(session, hazard_name):
                query = (
                    select(UserMitigationMeasure)
                    .join(UserHazard, UserHazard.id == UserMitigationMeasure.user_hazard_id)
                    .join(UserSession, UserSession.id == UserHazard.user_session_id)
                    .where(
                        UserHazard.name == hazard_name,
                        UserHazard.sector_id == session.sector_id,
                        UserSession.country_id == session.country_id,
                        UserHazard.region_id.is_(None)
                        if session.region_id is None
                        else UserHazard.region_id == session.region_id,
                    )
                    .order_by(UserMitigationMeasure.id)
                )
            else:
                system_hazard_id = None
                additional_hazard_id = None
                if self._is_additional_hazard(session, hazard_name):
                    additional_hazard_id = self._selected_additional_hazard_id(
                        session, hazard_name
                    )
                else:
                    system_hazard_id = self.db.scalar(
                        select(SystemHazard.id).where(
                            SystemHazard.sector_id == session.sector_id,
                            func.lower(SystemHazard.name) == hazard_name.casefold(),
                        )
                    )
                if system_hazard_id is None and additional_hazard_id is None:
                    return []
                query = (
                    select(UserMitigationMeasure)
                    .join(UserSession, UserSession.id == UserMitigationMeasure.user_session_id)
                    .where(
                        UserSession.country_id == session.country_id,
                        UserSession.region_id.is_(None)
                        if session.region_id is None
                        else UserSession.region_id == session.region_id,
                        UserSession.sector_id == session.sector_id,
                        UserMitigationMeasure.system_hazard_id == system_hazard_id,
                        UserMitigationMeasure.additional_hazard_id == additional_hazard_id,
                    )
                    .order_by(UserMitigationMeasure.id)
                )
            if self.user_id is not None:
                query = query.where(
                    or_(
                        UserSession.user_id == self.user_id,
                        and_(
                            UserMitigationMeasure.validation_mode == "strict",
                            UserMitigationMeasure.is_crowd_sourced.is_(True),
                        ),
                    )
                )
            rows = self.db.scalars(query).all()
        except Exception:
            logger.exception("Failed to load mitigation measures for duplicate check")
            return []
        records: list[UserMitigationMeasure] = []
        seen: set[str] = set()
        for row in rows:
            measure = str(row.measure or "").strip()
            key = normalize(measure)
            if not measure or key in seen:
                continue
            seen.add(key)
            records.append(row)
        return records

    def _existing_mitigation_measures_for_selected_hazard(
        self, session: ChatSession
    ) -> list[str]:
        return [
            record.measure
            for record in self._existing_mitigation_records_for_selected_hazard(session)
            if str(record.measure or "").strip()
        ]






    def _suggested_mitigation_record(self, session: ChatSession) -> UserMitigationMeasure | None:
        if session.suggested_mitigation_measure_id is None:
            return None
        try:
            return self.db.scalar(
                select(UserMitigationMeasure).where(
                    UserMitigationMeasure.id == session.suggested_mitigation_measure_id,
                )
            )
        except Exception:
            logger.exception("Failed to load suggested mitigation measure")
            return None

    def _suggested_mitigation_reason(self, session: ChatSession) -> str:
        record = self._suggested_mitigation_record(session)
        if record is None:
            return "No saved reason was found for this mitigation measure."
        return record.reason or "No saved reason was found for this mitigation measure."

    def _suggested_mitigation_evaluation_report(self, session: ChatSession) -> str:
        if session.suggested_mitigation_measure_id is None:
            return "- No evaluation report was found for this mitigation measure."
        try:
            rows = self.db.execute(
                select(
                    UserQuestionResponse.category,
                    EvaluationQuestion.question,
                    UserQuestionResponse.response_text,
                    UserQuestionResponse.score,
                    UserQuestionResponse.reason,
                    UserQuestionResponse.evidence,
                )
                .outerjoin(
                    EvaluationQuestion,
                    EvaluationQuestion.id == UserQuestionResponse.question_id,
                )
                .where(
                    UserQuestionResponse.mitigation_measure_id
                    == session.suggested_mitigation_measure_id
                )
                .order_by(UserQuestionResponse.id)
            ).all()
        except Exception:
            logger.exception("Failed to load suggested mitigation evaluation report")
            return "- No evaluation report was found for this mitigation measure."

        if not rows:
            return "- No evaluation report was found for this mitigation measure."

        lines: list[str] = []
        for category, question, response_text, score, reason, evidence in rows:
            category_label = str(category or "Evaluation")
            question_label = normalize_markdown_text(str(question or category_label))
            lines.append(f"- **{category_label}: {question_label}**")
            if score is not None:
                lines.append(f"  - Score: **{score} / 10**")
            elif response_text:
                lines.append(f"  - Response: {response_text}")
            if reason:
                lines.append(f"  - Reason: {reason}")
            if evidence:
                lines.append(f"  - Evidence: {evidence}")
        return "\n".join(lines)

    def _suggested_mitigation_system_inquiry_report(self, session: ChatSession) -> str:
        record = self._suggested_mitigation_record(session)
        if record is None:
            return "- No system inquiry reflections were found for this mitigation measure."
        payload = self._system_inquiry_existing_payload(record.system_inquiry_json)
        if not payload:
            return "- No system inquiry reflections were found for this mitigation measure."
        if bool(payload.get("skipped")):
            return "- System inquiry was skipped for this mitigation measure."

        annotations = [
            item
            for item in payload.get("annotations", [])
            if isinstance(item, dict) and str(item.get("status") or "current") == "current"
        ]
        if not annotations:
            return "- No current system inquiry reflections were found for this mitigation measure."

        profile = payload.get("profile") if isinstance(payload.get("profile"), dict) else {}
        completion = profile.get("completion_score")
        superseded = payload.get("superseded_annotations")
        coverage = (
            payload.get("coverage_summary")
            if isinstance(payload.get("coverage_summary"), dict)
            else {}
        )

        lines: list[str] = []
        if completion is not None:
            lines.append(f"- Completion score: **{completion}**")
        lines.append(f"- Current reflection responses: **{len(annotations)}**")
        if isinstance(superseded, list) and superseded:
            lines.append(f"- Superseded reflection responses retained: **{len(superseded)}**")
        if isinstance(coverage, dict):
            uncovered = [
                str(item).strip()
                for item in (coverage.get("uncovered_hazards") or [])
                if str(item).strip()
            ]
            untargeted = [
                str(item).strip()
                for item in (coverage.get("untargeted_groups") or [])
                if str(item).strip()
            ]
            if uncovered:
                lines.append(
                    f"- D4 hazard coverage: the selected hazard has no linked mitigation measure: {uncovered[0]}."
                )
            if untargeted:
                lines.append(
                    f"- D5 group coverage: {len(untargeted)} affected group"
                    f"{' is' if len(untargeted) == 1 else 's are'} not named in the mitigation target population: "
                    + "; ".join(untargeted[:5])
                    + "."
                )

        for annotation in annotations:
            title = (
                str(annotation.get("title") or "").strip()
                or str(annotation.get("lens_id") or "").strip()
                or str(annotation.get("probe_id") or "System inquiry").strip()
            )
            state = str(annotation.get("resolution_state") or "open").strip()
            corpus_label = str(annotation.get("corpus_label") or "unproven").strip()
            observation = normalize_markdown_text(
                str(annotation.get("observation_text") or "")
            )
            question = normalize_markdown_text(str(annotation.get("question_text") or ""))
            response = normalize_markdown_text(str(annotation.get("user_response") or ""))
            followup_question = normalize_markdown_text(
                str(annotation.get("followup_question") or "")
            )
            followup_response = normalize_markdown_text(
                str(annotation.get("followup_response") or "")
            )

            lines.append(f"- **{title}** ({corpus_label}; {state})")
            if observation:
                lines.append(f"  - Observation: {observation}")
            if question:
                lines.append(f"  - Question: {question}")
            if response:
                lines.append(f"  - Response: {response}")
            if followup_question:
                lines.append(f"  - Follow-up question: {followup_question}")
            if followup_response:
                lines.append(f"  - Follow-up response: {followup_response}")
        return "\n".join(lines)
