from app.services.chat_options import normalize_for_match


def sector_family(sector: str | None) -> str:
    text = normalize_for_match(sector or "")
    if "transport" in text:
        return "transport"
    if "housing" in text:
        return "housing"
    if "energy" in text:
        return "energy"
    return text


def sector_display_name(sector_family_value: str) -> str:
    return {
        "transport": "Transport",
        "housing": "Housing",
        "energy": "Energy",
    }.get(sector_family_value, sector_family_value.title())


def custom_hazard_sector_mismatch_reason(
    *,
    selected_sector: str | None,
    hazard: str,
    reason: str = "",
    evidence: str = "",
) -> str | None:
    selected_family = sector_family(selected_sector)
    if selected_family not in {"energy", "housing", "transport"}:
        return None

    text = " ".join(
        part.strip()
        for part in (hazard, reason, evidence)
        if isinstance(part, str) and part.strip()
    )
    if not text.strip():
        return None

    scores = sector_signal_scores(text)
    selected_score = scores.get(selected_family, 0)
    other_scores = {
        sector: score
        for sector, score in scores.items()
        if sector != selected_family and score > 0
    }
    if not other_scores:
        return None

    strongest_other, strongest_score = max(
        other_scores.items(),
        key=lambda item: item[1],
    )
    if selected_score == 0:
        if strongest_score < 1:
            return None
    elif strongest_score < selected_score + 2:
        return None

    return (
        f"This appears to be mainly a {sector_display_name(strongest_other)} "
        f"hazard, but the selected sector is "
        f"{selected_sector or sector_display_name(selected_family)}. "
        "Please rewrite it so the hazard clearly belongs to the selected sector, "
        "or choose the matching sector before adding it."
    )


def deterministic_custom_hazard_input_review(
    *,
    selected_sector: str | None,
    hazard: str,
) -> dict[str, object] | None:
    normalized = normalize_for_match(hazard)
    if not normalized:
        return _reject_input(
            "Please enter a hazard name before continuing.",
            validation_code="empty_hazard",
        )

    if _is_question_input(normalized, hazard):
        return _reject_input(
            "This is written as a question rather than a hazard. Please rewrite it as a standalone negative consequence caused by a green, digital, or twin-transition measure.",
            validation_code="question_not_hazard",
        )

    if normalized.startswith(
        ("i like ", "i love ", "i prefer ", "i think ", "we like ", "we love ", "we prefer ", "we think ")
    ):
        return _reject_input(
            _missing_negative_hazard_reason(normalized),
            validation_code="personal_preference",
        )

    if len(normalized.split()) <= 2:
        return _reject_input(
            "This is only a keyword or broad topic, so it is too short to validate as a hazard. Please state who is affected, what negative consequence occurs, and which transition measure causes it.",
            validation_code="too_short",
        )

    if _is_request_or_meta_input(normalized):
        return _reject_input(
            "This is a request or comment rather than a hazard. Please rewrite it as a concrete negative impact or risk from the selected transition context.",
            validation_code="not_a_hazard",
        )

    if _is_benefit_or_mitigation_statement(normalized):
        return _reject_input(
            _benefit_or_mitigation_reason(normalized),
            validation_code="mitigation_not_hazard" if _is_mitigation_statement(normalized) else "benefit_not_hazard",
        )

    if _is_generic_socioeconomic_issue(normalized):
        return _reject_input(
            _generic_socioeconomic_reason(normalized, selected_sector),
            validation_code="generic_socioeconomic_issue",
        )

    if _is_general_consumer_price_issue(normalized):
        return _reject_input(
            _general_consumer_price_reason(normalized, selected_sector),
            validation_code="generic_consumer_price_issue",
        )

    if not _has_negative_hazard_signal(normalized) and _has_transition_policy_signal(normalized):
        return _clarify_input(
            _missing_negative_hazard_reason(normalized),
            validation_code="missing_negative_consequence",
            clarification_question=(
                "Who is affected, what concrete harm or risk do they face, and "
                "which green, digital, or twin-transition measure causes or worsens it?"
            ),
        )
    if not _has_negative_hazard_signal(normalized):
        return _reject_input(
            _missing_negative_hazard_reason(normalized),
            validation_code="missing_negative_consequence",
        )

    if not _has_transition_policy_signal(normalized):
        return None

    if _uses_only_generic_population(normalized):
        return _clarify_input(
            "This points to a possible transition-related harm, but the affected population group is too generic.",
            validation_code="unclear_affected_group",
            clarification_question=(
                "Which specific population group is affected, and what concrete "
                "consequence do they experience from this transition measure?"
            ),
        )

    selected_family = sector_family(selected_sector)
    selected_score = sector_signal_scores(hazard).get(selected_family, 0)
    if selected_family in {"energy", "housing", "transport"} and selected_score > 0:
        return {
            "status": "Valid",
            "valid": True,
            "reason": "The input is a concrete transition-related hazard for the selected sector.",
            "suggestions": [],
            "validation_code": "valid_hazard",
            "confidence": 0.9,
            "source": "deterministic_guardrail",
        }

    return None


def custom_hazard_sector_rewrite_suggestion(
    *,
    selected_sector: str | None,
    hazard: str,
    reason: str = "",
    evidence: str = "",
) -> str:
    selected_family = sector_family(selected_sector)
    if selected_family not in {"energy", "housing", "transport"}:
        return ""

    selected_sector_label = selected_sector or sector_display_name(selected_family)
    normalized = normalize_for_match(" ".join([hazard, reason, evidence]))
    pattern = (
        f"Keep the affected group and harm, but make the mechanism a "
        f"{selected_sector_label} transition mechanism: "
        "`[affected group] face [same disadvantage] because [selected-sector "
        "transition policy or infrastructure change] affects [access, cost, "
        "exposure, or exclusion]`."
    )
    sector_guidance = {
        "transport": (
            "For Transport, anchor the hazard in mobility, EV adoption, charging "
            "access, public transport, road pricing, or transport electrification."
        ),
        "housing": (
            "For Housing, anchor the hazard in tenancy, building retrofit, "
            "renovation, insulation, residential infrastructure, rents, or "
            "landlord decisions."
        ),
        "energy": (
            "For Energy, anchor the hazard in tariffs, bills, grid access, clean "
            "heating, renewables, utility access, or household energy infrastructure."
        ),
    }
    example = ""
    if selected_family == "transport" and any(
        term in normalized
        for term in ("ev", "electric vehicle", "charging", "home charging")
    ):
        example = (
            "Example: `Renters and apartment dwellers face unequal access to EV "
            "adoption because transport electrification relies on private "
            "home-charging access or dedicated parking.`"
        )

    lines = [pattern, sector_guidance[selected_family]]
    if example:
        lines.append(example)
    return "\n\n".join(lines)


def plain_custom_hazard_rejection_reason(
    *,
    selected_sector: str | None,
    hazard: str,
    reason: str = "",
    evidence: str = "",
) -> str | None:
    text = " ".join(
        part.strip()
        for part in (hazard, reason, evidence)
        if isinstance(part, str) and part.strip()
    )
    normalized = normalize_for_match(text)
    if not normalized:
        return None

    has_transition_mechanism = any(
        phrase in normalized
        for phrase in (
            "green transition",
            "digital transition",
            "twin transition",
            "transition policy",
            "climate policy",
            "decarbonisation",
            "decarbonization",
            "net zero",
            "renewable",
            "electrification",
            "retrofit",
            "renovation policy",
            "heating replacement",
            "heat pump",
            "gas boiler ban",
            "gas phase out",
            "fossil fuel phase out",
            "energy efficiency",
            "carbon price",
            "carbon tax",
            "smart meter",
            "digitalisation",
            "digitalization",
        )
    )
    if has_transition_mechanism:
        return None

    household_safety_signals = (
        "carbon monoxide",
        "co poisoning",
        "gas leak",
        "fire hazard",
        "burn injury",
    )
    domestic_source_signals = (
        "domestic heating",
        "home heating",
        "household heating",
        "cooking",
        "stove",
        "boiler",
        "heater",
        "oven",
        "gas appliance",
    )
    if any(signal in normalized for signal in household_safety_signals) and any(
        signal in normalized for signal in domestic_source_signals
    ):
        sector_text = f" in the {selected_sector} sector" if selected_sector else ""
        return (
            "Carbon monoxide poisoning from domestic heating or cooking is a "
            f"general household safety risk{sector_text}. To add it as a hazard, "
            "please rewrite it to show the green or digital transition policy "
            "that creates or increases the risk, such as a heating-replacement, "
            "retrofit, electrification, or energy-efficiency policy."
        )

    structural_housing_signals = (
        "structural housing hazard",
        "structural housing hazards",
        "missing smoke detector",
        "missing smoke detectors",
        "smoke detector",
        "smoke detectors",
        "missing window guard",
        "missing window guards",
        "window guard",
        "window guards",
    )
    if any(signal in normalized for signal in structural_housing_signals):
        sector_text = f" in the {selected_sector} sector" if selected_sector else ""
        return (
            "Missing smoke detectors, window guards, and similar structural housing "
            f"conditions are general housing-safety hazards{sector_text}, not transition-related hazards. "
            "Please describe a specific green or digital transition policy, retrofit, "
            "or infrastructure change that creates or worsens the risk."
        )

    return None


def _reject_input(reason: str, *, validation_code: str = "not_a_hazard") -> dict[str, object]:
    return {
        "status": "invalid",
        "valid": False,
        "is_valid": False,
        "validation_code": validation_code,
        "confidence": 0.95,
        "reason": reason,
        "suggestions": [],
        "source": "deterministic_guardrail",
    }


def _clarify_input(
    reason: str,
    *,
    validation_code: str = "unclear_hazard",
    clarification_question: str,
) -> dict[str, object]:
    return {
        "status": "needs_clarification",
        "valid": False,
        "is_valid": False,
        "validation_code": validation_code,
        "confidence": 0.8,
        "reason": reason,
        "clarification_question": clarification_question,
        "suggestions": [],
        "source": "deterministic_guardrail",
    }


def _is_question_input(normalized: str, raw_text: str) -> bool:
    question_starts = (
        "what",
        "why",
        "how",
        "when",
        "where",
        "who",
        "can",
        "could",
        "would",
        "should",
        "does",
        "do",
        "is",
        "are",
    )
    return str(raw_text or "").strip().endswith("?") or normalized.startswith(
        tuple(f"{word} " for word in question_starts)
    )


def _is_request_or_meta_input(normalized: str) -> bool:
    request_starts = (
        "tell me",
        "what is",
        "what are",
        "how do",
        "how does",
        "can you",
        "please add",
        "add more data",
        "show me",
        "explain",
    )
    return normalized.startswith(request_starts)


def _is_mitigation_statement(normalized: str) -> bool:
    mitigation_starts = (
        "install",
        "provide",
        "subsidize",
        "subsidise",
        "fund",
        "support",
        "promote",
        "improve",
        "increase access",
        "reduce",
        "develop",
        "build",
        "create",
        "offer",
        "expand",
    )
    return normalized.startswith(mitigation_starts)


def _is_benefit_or_mitigation_statement(normalized: str) -> bool:
    benefit_terms = (
        "benefit",
        "benefits",
        "reduce",
        "reduces",
        "reduced",
        "improve",
        "improves",
        "improved",
        "help",
        "helps",
        "support",
        "supports",
        "subsidy",
        "subsidies",
        "grant",
        "grants",
        "better",
    )
    return (
        _is_mitigation_statement(normalized)
        or any(term in normalized.split() for term in benefit_terms)
    ) and not _has_negative_hazard_signal(normalized)


def _benefit_or_mitigation_reason(normalized: str) -> str:
    if _is_mitigation_statement(normalized):
        return (
            "This is written as a policy action or mitigation measure, not as a hazard. "
            "Please describe the negative consequence that could occur when a transition measure is implemented."
        )
    return (
        "This describes a benefit or desired outcome rather than a hazard. Please rewrite it as a concrete negative impact or risk from the selected transition context."
    )


def _is_generic_socioeconomic_issue(normalized: str) -> bool:
    words = set(normalized.split())
    general_issue_terms = {
        "inflation",
        "poverty",
        "unemployment",
        "recession",
        "austerity",
        "inequality",
    }
    if not (words & general_issue_terms):
        return False
    transition_terms = {
        "grid",
        "smart",
        "meter",
        "meters",
        "renewable",
        "renewables",
        "coal",
        "fossil",
        "phase",
        "phaseout",
        "electrification",
        "decarbonisation",
        "decarbonization",
        "retrofit",
        "renovation",
        "ev",
        "charging",
        "digital",
        "dynamic",
        "tariff",
        "tariffs",
        "network",
        "emission",
        "emissions",
    }
    return not bool(words & transition_terms)


def _generic_socioeconomic_reason(normalized: str, selected_sector: str | None) -> str:
    sector_text = f"{selected_sector}-sector " if selected_sector else ""
    if "inflation" in normalized:
        return (
            f"The statement describes a general inflation problem, but it does not "
            f"identify any {sector_text}green or digital transition measure causing "
            "or worsening the price increase."
        )
    if "unemployment" in normalized:
        return (
            f"The statement describes unemployment in general, but it does not "
            f"identify any {sector_text}green or digital transition measure causing "
            "the job loss."
        )
    return (
        f"The statement describes a general socioeconomic problem, but it does not "
        f"identify any {sector_text}green or digital transition measure causing or "
        "worsening it."
    )


def _is_general_consumer_price_issue(normalized: str) -> bool:
    words = set(normalized.split())
    consumer_price_terms = {
        "grocery",
        "groceries",
        "food",
        "supermarket",
        "shopping",
    }
    price_terms = {
        "price",
        "prices",
        "cost",
        "costs",
        "expensive",
        "affordability",
        "purchasing",
    }
    if words & consumer_price_terms and words & price_terms:
        return not _has_transition_policy_signal(normalized)
    if {"purchasing", "power"} <= words and words & {"reduce", "reduces", "reduced", "lower", "lowers"}:
        return not _has_transition_policy_signal(normalized)
    return False


def _general_consumer_price_reason(normalized: str, selected_sector: str | None) -> str:
    sector_text = f"{selected_sector}-sector " if selected_sector else ""
    if any(term in normalized for term in ("grocery", "groceries", "food", "supermarket")):
        return (
            "The statement describes general grocery or food-price pressure, but it "
            f"does not identify any {sector_text}green or digital transition measure "
            "causing or worsening the harm."
        )
    return (
        "The statement describes a general household purchasing-power problem, but it "
        f"does not identify any {sector_text}green or digital transition measure "
        "causing or worsening it."
    )


def _missing_negative_hazard_reason(normalized: str) -> str:
    preference_starts = (
        "i like",
        "i love",
        "i prefer",
        "i think",
        "we like",
        "we love",
        "we prefer",
        "we think",
    )
    if normalized.startswith(preference_starts):
        return (
            "This reads as a personal preference or opinion, not a policy hazard. "
            "Please rewrite it to name the affected population group, the concrete harm, "
            "and the green, digital, or twin-transition mechanism that creates or increases the risk."
        )

    words = normalized.split()
    if len(words) <= 4:
        return (
            "This is too broad to validate as a hazard because it does not state "
            "who is harmed or what negative impact occurs. Please rewrite it as "
            "`[affected group] face [harm] because [transition policy mechanism]`."
        )

    return (
        "This does not state a concrete negative impact. Please rewrite it to "
        "identify the affected population group, the harm or risk they face, and "
        "how a green, digital, or twin-transition policy causes or worsens it."
    )


def _has_negative_hazard_signal(normalized: str) -> bool:
    negative_phrases = (
        "leave behind",
        "leaves behind",
        "left behind",
    )
    if any(phrase in normalized for phrase in negative_phrases):
        return True
    if "behind" in normalized.split() and set(normalized.split()) & {"leave", "leaves", "left"}:
        return True
    negative_terms = (
        "risk",
        "hazard",
        "harm",
        "burden",
        "higher",
        "expensive",
        "costly",
        "rising",
        "rise",
        "increases",
        "increase",
        "unaffordable",
        "arrears",
        "outage",
        "outages",
        "congestion",
        "displacement",
        "eviction",
        "exclusion",
        "excluded",
        "exclude",
        "excludes",
        "excluding",
        "hurt",
        "hurts",
        "harmed",
        "lose",
        "loss",
        "lost",
        "shortage",
        "barrier",
        "barriers",
        "gap",
        "gaps",
        "scarce",
        "scarcity",
        "shock",
        "shocks",
        "unequal",
        "pressure",
        "disruption",
        "downtime",
        "cost",
        "costs",
        "price",
        "prices",
        "fees",
        "tariffs",
        "penalty",
        "penalties",
        "poverty",
        "vulnerable",
    )
    return any(term in normalized.split() for term in negative_terms)


def _uses_only_generic_population(normalized: str) -> bool:
    words = set(normalized.split())
    generic_population_terms = {
        "people",
        "users",
        "citizens",
        "public",
        "communities",
        "consumers",
    }
    if not (words & generic_population_terms):
        return False
    specific_modifiers = {
        "low",
        "income",
        "elderly",
        "older",
        "disabled",
        "rural",
        "remote",
        "renters",
        "tenants",
        "workers",
        "commuters",
        "drivers",
        "residents",
        "small",
        "businesses",
        "vulnerable",
        "migrant",
        "minority",
        "households",
        "apartment",
        "dwellers",
    }
    return not bool(words & specific_modifiers)


def _has_transition_policy_signal(normalized: str) -> bool:
    transition_phrases = (
        "green transition",
        "digital transition",
        "twin transition",
        "transition policy",
        "climate policy",
        "net zero",
        "low emission",
        "clean vehicle",
        "clean heating",
        "energy",
        "energy performance",
        "smart grid",
        "smart meter",
        "electric vehicle",
        "energy standard",
        "energy standards",
        "building energy standard",
        "building energy standards",
        "residential energy performance",
        "renovation requirement",
        "renovation requirements",
    )
    if any(phrase in normalized for phrase in transition_phrases):
        return True
    transition_terms = {
        "renewable",
        "renewables",
        "solar",
        "wind",
        "grid",
        "electricity",
        "tariff",
        "tariffs",
        "electrification",
        "electric",
        "retrofit",
        "retrofits",
        "renovation",
        "renovations",
        "insulation",
        "digitalisation",
        "digitalization",
        "digital",
        "ev",
        "charging",
        "decarbonisation",
        "decarbonization",
    }
    return bool(set(normalized.split()) & transition_terms)


def sector_signal_scores(text: str) -> dict[str, int]:
    normalized = f" {normalize_for_match(text)} "
    normalized_for_energy = normalized.replace(" purchasing power ", " ")
    phrase_groups: dict[str, tuple[str, ...]] = {
        "transport": (
            "transport",
            "mobility",
            "travel",
            "commuter",
            "commuters",
            "public transport",
            "public transit",
            "transit",
            "bus",
            "buses",
            "rail",
            "train",
            "trains",
            "tram",
            "metro",
            "vehicle",
            "vehicles",
            "electric vehicle",
            "ev",
            "charging station",
            "charging stations",
            "road",
            "roads",
            "traffic",
            "low emission zone",
            "low emission zones",
            "car",
            "cars",
            "cycling",
            "bicycle",
            "bike",
            "pedestrian",
            "freight",
            "aviation",
        ),
        "energy": (
            "energy",
            "electricity",
            "electric",
            "power",
            "grid",
            "renewable",
            "renewables",
            "solar",
            "wind",
            "utility bill",
            "utility bills",
            "utility arrears",
            "energy bill",
            "energy bills",
            "tariff",
            "tariffs",
            "fuel poverty",
            "energy poverty",
            "heat pump",
            "heat pumps",
            "clean heating",
        ),
        "housing": (
            "housing",
            "home",
            "homes",
            "house",
            "houses",
            "building",
            "buildings",
            "dwelling",
            "dwellings",
            "apartment",
            "apartments",
            "residential",
            "retrofit",
            "retrofits",
            "renovation",
            "renovations",
            "insulation",
            "tenant",
            "tenants",
            "renter",
            "renters",
            "landlord",
            "landlords",
            "rent",
            "rents",
            "housing cost",
            "housing costs",
            "energy inefficient homes",
            "poorly insulated",
        ),
    }
    return {
        sector: sum(
            1
            for phrase in phrases
            if f" {normalize_for_match(phrase)} "
            in (normalized_for_energy if sector == "energy" else normalized)
        )
        for sector, phrases in phrase_groups.items()
    }
