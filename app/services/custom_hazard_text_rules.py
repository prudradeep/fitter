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

    return None


def sector_signal_scores(text: str) -> dict[str, int]:
    normalized = f" {normalize_for_match(text)} "
    phrase_groups: dict[str, tuple[str, ...]] = {
        "transport": (
            "transport",
            "mobility",
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
            if f" {normalize_for_match(phrase)} " in normalized
        )
        for sector, phrases in phrase_groups.items()
    }
