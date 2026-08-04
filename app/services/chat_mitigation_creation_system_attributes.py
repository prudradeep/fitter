# ruff: noqa: F403,F405
from app.services.chat_mitigation_creation_common import *

async def _ask_llm_chat(*args, **kwargs):
    from app.services import chat_mitigation_creation as facade

    return await facade.ask_llm_chat(*args, **kwargs)


class ChatMitigationCreationSystemAttributesMixin:
    async def _system_inquiry_measure_attributes_with_llm(
        self,
        session: ChatSession,
        groups: list[str],
    ) -> dict[str, object]:
        fallback = self._system_inquiry_measure_attributes(
            session.mitigation_measure,
            session.mitigation_reason,
            groups,
        )
        context = (
            "Extract MeasureAttributes for a system inquiry pipeline. Return only JSON. "
            "Use only the supplied mitigation measure, reason, target population, hazard, "
            "sector, country, and region. Do not infer facts not present in the input. "
            "Allowed values: action_type subsidy|grant|tariff|regulation|mandate|service|"
            "information|infrastructure|procurement|tax|other; leverage_depth parameter|"
            "rules|goals|paradigm; delivery_channel automatic|application|means_tested|"
            "universal|intermediary|unknown; cost_incidence no_user_cost|upfront_user_cost|"
            "ongoing_user_cost|unknown; time_to_benefit immediate|months|years|unknown; "
            "capacity_type installers|inspectors|advisors|grid|housing_stock|"
            "administrative|none|unknown."
        )
        messages = [
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "mitigation_measure": session.mitigation_measure or "",
                        "mitigation_reason": session.mitigation_reason or "",
                        "target_population": groups,
                        "selected_hazard": session.selected_hazard or "",
                        "country": session.country or "",
                        "region": session.region or "",
                        "sector": session.sector or "",
                        "schema": {
                            "action_type": "string",
                            "leverage_depth": "string",
                            "delivery_channel": "string",
                            "cost_incidence": "string",
                            "time_to_benefit": "string",
                            "eligibility_basis": ["string"],
                            "named_group_ids": ["string"],
                            "named_sectors": ["string"],
                            "requires_capacity": "boolean",
                            "capacity_type": "string",
                        },
                    },
                    ensure_ascii=True,
                ),
            }
        ]
        response = await _ask_llm_chat(
            context=context,
            messages=messages,
            temperature=0.0,
            max_tokens=550,
            response_format=self._system_inquiry_measure_attributes_schema(),
        )
        if is_llm_unavailable_response(response):
            fallback["extraction_method"] = "deterministic_v1_llm_unavailable"
            return fallback
        parsed = parse_json_object(response) or {}
        attributes = self._sanitize_system_inquiry_attributes(parsed, fallback, groups)
        attributes["extraction_method"] = "llm_constrained_v1"
        return attributes

    async def _system_inquiry_screen_candidates_with_llm(
        self,
        session: ChatSession,
        candidates: list[dict[str, object]],
    ) -> None:
        payload = self._system_inquiry_candidate_llm_payload(session, candidates)
        response = await _ask_llm_chat(
            context=(
                "Screen system-inquiry probe candidates. Return only a JSON array. "
                "For each candidate_id decide whether the detection question is relevant "
                "to the supplied dossier. Keep screen_result true only when the question "
                "follows from the candidate's anchors and session context."
            ),
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=True)}],
            temperature=0.0,
            max_tokens=900,
            response_format=self._system_inquiry_candidate_review_schema("screen"),
        )
        if is_llm_unavailable_response(response):
            return
        reviews = self._system_inquiry_review_map(parse_json_array(response))
        for candidate in candidates:
            review = reviews.get(str(candidate.get("candidate_id") or ""))
            if not review:
                continue
            candidate["screen_result"] = self._coerce_bool(
                review.get("screen_result"),
                default=True,
            )
            candidate["screen_reason"] = str(review.get("reason") or "")[:500]
            candidate["screen_method"] = "llm_constrained_v1"

    async def _system_inquiry_verify_candidates_with_llm(
        self,
        session: ChatSession,
        candidates: list[dict[str, object]],
    ) -> None:
        payload = self._system_inquiry_candidate_llm_payload(session, candidates)
        response = await _ask_llm_chat(
            context=(
                "Verify system-inquiry probe candidates. Return only a JSON array. "
                "For each candidate_id provide verify_votes from 0 to 3. Award votes "
                "only when the observation names real anchors in the dossier and does "
                "not overclaim beyond those anchors."
            ),
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=True)}],
            temperature=0.0,
            max_tokens=900,
            response_format=self._system_inquiry_candidate_review_schema("verify"),
        )
        if is_llm_unavailable_response(response):
            return
        reviews = self._system_inquiry_review_map(parse_json_array(response))
        for candidate in candidates:
            review = reviews.get(str(candidate.get("candidate_id") or ""))
            if not review:
                continue
            candidate["verify_votes"] = self._bounded_int(review.get("verify_votes"), 0, 3)
            candidate["verify_reason"] = str(review.get("reason") or "")[:500]
            candidate["verify_method"] = "llm_constrained_v1"

    async def _system_inquiry_adjudicate_corpus_with_llm(
        self,
        session: ChatSession,
        candidates: list[dict[str, object]],
    ) -> None:
        payload = self._system_inquiry_candidate_llm_payload(session, candidates)
        response = await _ask_llm_chat(
            context=(
                "Adjudicate corpus support for system-inquiry candidates. Return only "
                "a JSON array with candidate_id, corpus_label, and reason. corpus_label "
                "must be one of evidenced, unproven, refuted. Use evidenced only when "
                "the supplied structured citations or session evidence directly support "
                "the observation. Use refuted only when supplied facts directly contradict "
                "it. Otherwise use unproven."
            ),
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=True)}],
            temperature=0.0,
            max_tokens=900,
            response_format=self._system_inquiry_candidate_review_schema("corpus"),
        )
        if is_llm_unavailable_response(response):
            return
        reviews = self._system_inquiry_review_map(parse_json_array(response))
        allowed_labels = {"evidenced", "unproven", "refuted"}
        for candidate in candidates:
            review = reviews.get(str(candidate.get("candidate_id") or ""))
            if not review:
                continue
            label = str(review.get("corpus_label") or "").strip().casefold()
            if label not in allowed_labels:
                label = "unproven"
            if label == "evidenced" and not candidate.get("citations"):
                label = "unproven"
            candidate["corpus_label"] = label
            candidate["corpus_adjudication_reason"] = str(review.get("reason") or "")[:500]
            candidate["corpus_adjudication_method"] = "llm_constrained_v1"

    def _system_inquiry_candidate_llm_payload(
        self,
        session: ChatSession,
        candidates: list[dict[str, object]],
    ) -> dict[str, object]:
        return {
            "dossier": {
                "country": session.country or "",
                "region": session.region or "",
                "sector": session.sector or "",
                "selected_hazard": session.selected_hazard or "",
                "mitigation_measure": session.mitigation_measure or "",
                "mitigation_reason": session.mitigation_reason or "",
                "target_population": self._system_inquiry_group_labels(session),
                "affected_groups": self._system_inquiry_affected_group_labels(session),
                "evaluation_answers": session.evaluation_answers or [],
                "attributes": session.system_inquiry_attributes or {},
            },
            "candidates": [
                {
                    "candidate_id": item.get("candidate_id"),
                    "probe_id": item.get("probe_id"),
                    "title": item.get("title"),
                    "family": item.get("family"),
                    "detection_question": item.get("detection_question"),
                    "observation": item.get("observation"),
                    "question": item.get("question"),
                    "anchors": item.get("anchors"),
                    "required_anchors": item.get("required_anchors"),
                    "anchor_counts": item.get("anchor_counts"),
                    "citations": item.get("citations") or [],
                    "corpus_label": item.get("corpus_label") or "unproven",
                }
                for item in candidates
            ],
        }

    @staticmethod
    def _system_inquiry_measure_attributes_schema() -> dict[str, object]:
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "action_type": {
                    "type": "string",
                    "enum": [
                        "subsidy", "grant", "tariff", "regulation", "mandate",
                        "service", "information", "infrastructure", "procurement",
                        "tax", "other",
                    ],
                },
                "leverage_depth": {
                    "type": "string",
                    "enum": ["parameter", "rules", "goals", "paradigm"],
                },
                "delivery_channel": {
                    "type": "string",
                    "enum": [
                        "automatic", "application", "means_tested", "universal",
                        "intermediary", "unknown",
                    ],
                },
                "cost_incidence": {
                    "type": "string",
                    "enum": [
                        "no_user_cost", "upfront_user_cost", "ongoing_user_cost",
                        "unknown",
                    ],
                },
                "time_to_benefit": {
                    "type": "string",
                    "enum": ["immediate", "months", "years", "unknown"],
                },
                "eligibility_basis": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": [
                            "tenure", "income", "dwelling_condition", "location",
                            "age", "employment", "none", "unknown",
                        ],
                    },
                },
                "named_group_ids": {"type": "array", "items": {"type": "string"}},
                "named_sectors": {"type": "array", "items": {"type": "string"}},
                "requires_capacity": {"type": "boolean"},
                "capacity_type": {
                    "type": "string",
                    "enum": [
                        "installers", "inspectors", "advisors", "grid",
                        "housing_stock", "administrative", "none", "unknown",
                    ],
                },
            },
            "required": [
                "action_type",
                "leverage_depth",
                "delivery_channel",
                "cost_incidence",
                "time_to_benefit",
                "eligibility_basis",
                "named_group_ids",
                "named_sectors",
                "requires_capacity",
                "capacity_type",
            ],
        }

    @staticmethod
    def _system_inquiry_candidate_review_schema(kind: str) -> dict[str, object]:
        properties: dict[str, object] = {
            "candidate_id": {"type": "string"},
            "reason": {"type": "string"},
        }
        required = ["candidate_id", "reason"]
        if kind == "screen":
            properties["screen_result"] = {"type": "boolean"}
            required.append("screen_result")
        elif kind == "verify":
            properties["verify_votes"] = {"type": "integer", "minimum": 0, "maximum": 3}
            required.append("verify_votes")
        else:
            properties["corpus_label"] = {
                "type": "string",
                "enum": ["evidenced", "unproven", "refuted"],
            }
            required.append("corpus_label")
        return {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": properties,
                "required": required,
            },
        }

    @staticmethod
    def _system_inquiry_response_adjudication_schema() -> dict[str, object]:
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "resolution_state": {
                    "type": "string",
                    "enum": [
                        "addressed",
                        "partially_addressed",
                        "not_applicable_reasoned",
                        "acknowledged_unresolved",
                        "open",
                    ],
                },
                "evaluation": {"type": "string"},
                "needs_followup": {"type": "boolean"},
                "followup_type": {
                    "type": "string",
                    "enum": ["specify_mechanism", "name_group", "state_timeframe"],
                },
            },
            "required": [
                "resolution_state",
                "evaluation",
                "needs_followup",
                "followup_type",
            ],
        }

    @staticmethod
    def _system_inquiry_review_map(items: list[object] | None) -> dict[str, dict[str, object]]:
        reviews: dict[str, dict[str, object]] = {}
        for item in items or []:
            if not isinstance(item, dict):
                continue
            candidate_id = str(item.get("candidate_id") or "").strip()
            if candidate_id:
                reviews[candidate_id] = item
        return reviews

    @staticmethod
    def _bounded_int(value: object, minimum: int, maximum: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return minimum
        return max(minimum, min(maximum, parsed))

    def _sanitize_system_inquiry_attributes(
        self,
        parsed: dict[str, object],
        fallback: dict[str, object],
        groups: list[str],
    ) -> dict[str, object]:
        allowed = {
            "action_type": {
                "subsidy", "grant", "tariff", "regulation", "mandate", "service",
                "information", "infrastructure", "procurement", "tax", "other",
            },
            "leverage_depth": {"parameter", "rules", "goals", "paradigm"},
            "delivery_channel": {
                "automatic", "application", "means_tested", "universal",
                "intermediary", "unknown",
            },
            "cost_incidence": {
                "no_user_cost", "upfront_user_cost", "ongoing_user_cost", "unknown",
            },
            "time_to_benefit": {"immediate", "months", "years", "unknown"},
            "capacity_type": {
                "installers", "inspectors", "advisors", "grid", "housing_stock",
                "administrative", "none", "unknown",
            },
        }
        attributes = dict(fallback)
        for key, values in allowed.items():
            value = str(parsed.get(key) or "").strip().casefold()
            attributes[key] = value if value in values else fallback.get(key)
        bases = parsed.get("eligibility_basis")
        if isinstance(bases, list):
            cleaned_bases = [str(item).strip() for item in bases if str(item).strip()]
            if cleaned_bases:
                attributes["eligibility_basis"] = cleaned_bases[:6]
        sectors = parsed.get("named_sectors")
        if isinstance(sectors, list):
            attributes["named_sectors"] = [
                str(item).strip().casefold()
                for item in sectors
                if str(item).strip()
            ][:5]
        attributes["named_group_ids"] = list(groups or [])
        attributes["requires_capacity"] = self._coerce_bool(
            parsed.get("requires_capacity"),
            default=bool(fallback.get("requires_capacity")),
        )
        return attributes

    @staticmethod
    def _coerce_bool(value: object, *, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        cleaned = str(value or "").strip().casefold()
        if cleaned in {"true", "yes", "1"}:
            return True
        if cleaned in {"false", "no", "0"}:
            return False
        return default

    def _system_inquiry_measure_attributes(
        self,
        measure: str | None,
        reason: str | None = None,
        groups: list[str] | None = None,
    ) -> dict[str, object]:
        text = normalize_for_match(
            " ".join([str(measure or ""), str(reason or ""), " ".join(groups or [])])
        )
        return {
            "action_type": self._system_inquiry_action_type(text),
            "leverage_depth": self._system_inquiry_leverage_depth(text),
            "delivery_channel": self._system_inquiry_delivery_channel(text),
            "cost_incidence": self._system_inquiry_cost_incidence(text),
            "time_to_benefit": self._system_inquiry_time_to_benefit(text),
            "eligibility_basis": self._system_inquiry_eligibility_basis(text),
            "named_group_ids": list(groups or []),
            "named_sectors": self._system_inquiry_named_sectors(text),
            "requires_capacity": self._system_inquiry_requires_capacity(text),
            "capacity_type": self._system_inquiry_capacity_type(text),
            "extraction_method": "deterministic_v1",
        }

    @staticmethod
    def _system_inquiry_action_type(text: str) -> str:
        mapping = (
            ("subsidy", ("subsidy", "rebate", "voucher", "discount")),
            ("grant", ("grant",)),
            ("tariff", ("tariff", "rate")),
            ("regulation", ("regulation", "standard", "rule", "ban")),
            ("mandate", ("mandate", "require", "requirement", "obligation")),
            ("service", ("service", "support", "advice", "advisor", "helpline")),
            ("information", ("information", "campaign", "awareness", "guidance")),
            ("infrastructure", ("infrastructure", "grid", "retrofit", "construction")),
            ("procurement", ("procurement", "purchase", "contract")),
            ("tax", ("tax", "levy", "fine")),
        )
        for label, tokens in mapping:
            if any(token in text for token in tokens):
                return label
        return "other"

    def _system_inquiry_leverage_depth(self, text: str) -> str:
        if any(token in text for token in ("paradigm", "culture", "norm", "mindset")):
            return "paradigm"
        if any(token in text for token in ("goal", "mission", "target outcome")):
            return "goals"
        if any(
            token in text
            for token in (
                "eligibility rule",
                "governance",
                "institution",
                "rights",
                "standard",
                "mandate",
                "regulation",
            )
        ):
            return "rules"
        return "parameter"

    @staticmethod
    def _system_inquiry_delivery_channel(text: str) -> str:
        if any(token in text for token in ("automatic", "auto enrol", "auto-enrol")):
            return "automatic"
        if any(token in text for token in ("means tested", "means-tested", "income test")):
            return "means_tested"
        if any(token in text for token in ("intermediary", "advisor", "case worker")):
            return "intermediary"
        if any(token in text for token in ("apply", "application", "portal", "form")):
            return "application"
        if any(token in text for token in ("universal", "all households", "everyone")):
            return "universal"
        return "unknown"

    @staticmethod
    def _system_inquiry_cost_incidence(text: str) -> str:
        if any(
            token in text
            for token in (
                "upfront",
                "co pay",
                "copay",
                "co-payment",
                "contribution",
                "reimbursement",
                "loan",
            )
        ):
            return "upfront_user_cost"
        if any(token in text for token in ("tariff", "bill", "fee", "tax", "fine")):
            return "ongoing_user_cost"
        if any(token in text for token in ("free", "no cost", "fully funded")):
            return "no_user_cost"
        if any(token in text for token in ("grant", "subsidy", "rebate", "voucher")):
            return "upfront_user_cost"
        return "unknown"

    @staticmethod
    def _system_inquiry_time_to_benefit(text: str) -> str:
        if any(token in text for token in ("year", "years", "12 months", "24 months")):
            return "years"
        if any(token in text for token in ("month", "months", "phase", "rollout")):
            return "months"
        if any(token in text for token in ("immediate", "now", "instant")):
            return "immediate"
        return "unknown"

    @staticmethod
    def _system_inquiry_eligibility_basis(text: str) -> list[str]:
        bases: list[str] = []
        checks = (
            ("tenure", ("tenant", "homeowner", "renter", "landlord")),
            ("income", ("low income", "income", "means tested", "poverty")),
            ("dwelling_condition", ("damp", "draught", "insulation", "dwelling")),
            ("location", ("region", "rural", "urban", "postcode", "local")),
            ("age", ("older", "elderly", "age")),
            ("employment", ("worker", "employment", "job", "unemployed")),
        )
        for label, tokens in checks:
            if any(token in text for token in tokens):
                bases.append(label)
        return bases or ["unknown"]

    @staticmethod
    def _system_inquiry_named_sectors(text: str) -> list[str]:
        sectors = [
            sector
            for sector in ("energy", "housing", "transport")
            if sector in text
        ]
        return sectors

    @staticmethod
    def _system_inquiry_requires_capacity(text: str) -> bool:
        return any(
            token in text
            for token in (
                "installer",
                "inspector",
                "advisor",
                "grid",
                "housing stock",
                "administrator",
                "case worker",
                "contractor",
            )
        )

    @staticmethod
    def _system_inquiry_capacity_type(text: str) -> str:
        checks = (
            ("installers", ("installer", "contractor")),
            ("inspectors", ("inspector", "survey")),
            ("advisors", ("advisor", "advice", "case worker")),
            ("grid", ("grid",)),
            ("housing_stock", ("housing stock", "dwelling", "retrofit")),
            ("administrative", ("administrator", "application", "portal", "form")),
        )
        for label, tokens in checks:
            if any(token in text for token in tokens):
                return label
        return "none"

    @staticmethod
    def _system_inquiry_has_cost_incidence(text: str) -> bool:
        return any(
            token in text
            for token in (
                "cost",
                "payment",
                "pay",
                "copay",
                "co payment",
                "tariff",
                "fee",
                "grant",
                "subsidy",
                "loan",
                "reimbursement",
                "bill",
            )
        )

    @staticmethod
    def _system_inquiry_has_procedural_access(text: str) -> bool:
        return any(
            token in text
            for token in (
                "apply",
                "application",
                "eligibility",
                "eligible",
                "means tested",
                "means test",
                "documentation",
                "document",
                "digital",
                "portal",
                "intermediary",
                "advisor",
            )
        )

    @staticmethod
    def _system_inquiry_has_delay(text: str) -> bool:
        return any(
            token in text
            for token in (
                "month",
                "months",
                "year",
                "years",
                "delay",
                "rollout",
                "phase",
                "before",
                "while",
                "until",
                "implementation period",
            )
        )

    @staticmethod
    def _system_inquiry_has_feedback_loop_signal(text: str) -> bool:
        return any(
            token in text
            for token in (
                "feedback loop",
                "feedback",
                "uptake",
                "take up",
                "take-up",
                "demand",
                "response",
                "adoption",
                "participation",
                "price",
                "prices",
                "behavior",
                "behaviour",
                "crowd out",
                "crowd in",
                "crowd-in",
                "usage",
                "utilisation",
                "utilization",
            )
        )

    @staticmethod
    def _system_inquiry_validation_gaps(
        session: ChatSession,
    ) -> list[dict[str, str]]:
        validation = session.mitigation_validation or {}
        dimensions = validation.get("dimensions") if isinstance(validation, dict) else {}
        gaps: list[dict[str, str]] = []
        if not isinstance(dimensions, dict):
            return gaps
        for name, value in dimensions.items():
            if not isinstance(value, dict):
                continue
            status = str(value.get("status") or "").strip()
            if not status:
                continue
            if status.casefold() == "supported":
                continue
            explanation = str(value.get("explanation") or value.get("reason") or "").strip()
            gaps.append(
                {
                    "name": str(name).replace("_", " ").title(),
                    "status": status,
                    "explanation": explanation,
                }
            )
        return gaps

    @staticmethod
    def _system_inquiry_defines_criteria(
        text: str,
        attributes: dict[str, object],
    ) -> bool:
        bases = attributes.get("eligibility_basis")
        if isinstance(bases, list) and any(
            normalize_for_match(str(item)) and normalize_for_match(str(item)) != "unknown"
            for item in bases
        ):
            return True
        return any(
            token in text
            for token in (
                "eligibility",
                "eligible",
                "qualify",
                "qualification",
                "criteria",
                "criterion",
                "threshold",
                "priority",
                "screening",
                "selection",
                "success criteria",
                "success condition",
            )
        )

    @staticmethod
    def _system_inquiry_delegates_to_intermediary(
        text: str,
        attributes: dict[str, object],
    ) -> bool:
        if str(attributes.get("delivery_channel") or "").strip() == "intermediary":
            return True
        return any(
            token in text
            for token in (
                "through an intermediary",
                "via an intermediary",
                "case worker",
                "advisor",
                "local authority",
                "employer",
                "landlord",
                "utility",
                "partner",
                "contractor",
                "third party",
            )
        )

    @staticmethod
    def _system_inquiry_is_bare_dismissal(text: str) -> bool:
        dismissals = (
            "no",
            "none",
            "n/a",
            "na",
            "not applicable",
            "not relevant",
            "does not apply",
            "doesnt apply",
            "doesn't apply",
            "already covered",
        )
        has_dismissal = any(text == phrase or phrase in text for phrase in dismissals)
        has_reason = any(
            marker in text
            for marker in (
                "because",
                "since",
                "due to",
                "as ",
                "already",
                "covered by",
                "handled by",
                "provided through",
            )
        )
        return has_dismissal and not has_reason

    @staticmethod
    def _system_inquiry_is_reasoned_not_applicable(text: str) -> bool:
        not_applicable = any(
            phrase in text
            for phrase in (
                "not applicable",
                "not relevant",
                "does not apply",
                "doesnt apply",
                "doesn't apply",
                "already covered",
            )
        )
        reasoned = any(
            marker in text
            for marker in (
                "because",
                "since",
                "due to",
                "covered by",
                "handled by",
                "provided through",
                "already addressed",
            )
        )
        return not_applicable and reasoned and len(compact_for_match(text)) >= 50

    @staticmethod
    def _system_inquiry_acknowledges_unresolved(text: str) -> bool:
        return any(
            phrase in text
            for phrase in (
                "not sure",
                "don't know",
                "dont know",
                "do not know",
                "need to check",
                "cannot answer",
                "can't answer",
                "unresolved",
                "unknown",
            )
        )

    @staticmethod
    def _system_inquiry_has_concrete_response_marker(text: str) -> bool:
        return any(
            marker in text
            for marker in (
                "because",
                "since",
                "due to",
                "through",
                "with",
                "without",
                "will",
                "should",
                "must",
                "automatic",
                "eligibility",
                "fund",
                "budget",
                "owner",
                "timeline",
                "monitor",
                "include",
                "provide",
                "support",
                "target",
                "audit",
                "report",
            )
        ) or bool(re.search(r"\b\d+\b", text))

    @staticmethod
    def _system_inquiry_is_parameter_measure(text: str) -> bool:
        return any(
            token in text
            for token in (
                "grant",
                "subsidy",
                "payment",
                "support",
                "tariff",
                "tax",
                "fee",
                "rebate",
                "discount",
                "voucher",
                "loan",
            )
        )

    @staticmethod
    def _system_inquiry_systemic_score(session: ChatSession) -> int:
        scores: list[int] = []
        for answer in session.evaluation_answers or []:
            category = normalize_for_match(
                " ".join(
                    [
                        str(answer.get("category") or ""),
                        str(answer.get("question") or ""),
                        str(answer.get("chart_title") or ""),
                    ]
                )
            )
            if "systemic" not in category and "structural" not in category:
                continue
            try:
                scores.append(int(answer.get("score") or 0))
            except (TypeError, ValueError):
                continue
        return max(scores or [0])
