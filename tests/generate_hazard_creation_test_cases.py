from __future__ import annotations

from collections import Counter
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


OUTPUT_FILE = "hazard_creation_test_cases.xlsx"
TEST_SHEET = "Test Cases"
SUMMARY_SHEET = "Summary"

COUNTRY = "Germany"
REGION = "Bavaria"
SECTORS = ["Energy", "Housing", "Transport"]

COLUMNS = [
    "Test Case ID",
    "Category",
    "Selected Country",
    "Selected Region",
    "Selected Sector",
    "User Hazard",
    "Clarification Answer 1",
    "Clarification Answer 2",
    "Expected Action",
    "Expected Step",
    "Expected Input Mode",
    "Expected Error",
    "Expected Pending Hazard",
    "Expected Rejected Dimension",
    "Expected Message Contains",
    "Notes",
]


VALID_HAZARDS = {
    "Energy": [
        "Low-income households face higher electricity bills from renewable grid upgrade tariffs",
        "Rural residents face power outages from grid congestion during renewable energy integration",
        "Small businesses face utility arrears from dynamic electricity pricing and smart meter rollout",
        "Tenants face higher clean heating bills as heat pump tariffs and grid upgrade charges increase",
        "Energy community members face exclusion from solar sharing because smart grid access fees rise",
    ],
    "Housing": [
        "Low-income tenants face rent increases after mandatory green building retrofits",
        "Apartment residents face temporary displacement during deep renovation and insulation mandates",
        "Homeowners in inefficient buildings face unaffordable compliance costs from energy performance rules",
        "Renters face eviction pressure when landlords pass building renovation costs into higher rents",
        "Older residents in poorly insulated homes face disruption during mandatory residential retrofit works",
    ],
    "Transport": [
        "Low-income commuters face higher travel costs from low emission zone fees",
        "Rural residents lose mobility access when bus routes are replaced by digital booking systems",
        "Taxi drivers face income loss from EV charging downtime and clean vehicle mandates",
        "Apartment dwellers lose access to EV adoption because transport electrification relies on home charging",
        "Delivery workers face income loss from clean vehicle rules and scarce public charging infrastructure",
    ],
}

INVALID_HAZARDS = [
    "I like sunny weather",
    "Tell me about retrofit policy",
    "Solar panels reduce electricity bills for everyone",
    "Please add more data to the chart",
    "Carbon monoxide poisoning from domestic heating",
]

VAGUE_OR_INCOMPLETE_HAZARDS = [
    "The green transition is complicated for residents",
    "Digitalisation may affect some people",
    "Energy policy changes are happening quickly",
    "Housing renovation is important",
    "Transport electrification is a major topic",
]

BENEFIT_OR_MITIGATION_STATEMENTS = [
    "Solar subsidies reduce energy bills for low-income households",
    "Retrofit grants help tenants live in warmer homes",
    "EV charging grants support taxi drivers",
    "Better bus services improve access for rural residents",
    "Smart meters help households understand energy use",
]

CROSS_SECTOR_HAZARDS = {
    "Energy": "Low-income households face rising electricity tariffs from renewable grid upgrades",
    "Housing": "Tenants face rent increases after mandatory apartment retrofit policies",
    "Transport": "Taxi drivers face income loss from EV charging downtime and clean vehicle mandates",
}

SECTOR_SYNONYM_HAZARDS = {
    "Energy": [
        "Households face utility arrears when power tariffs rise after smart grid modernisation",
        "Remote communities face outage risk from renewable power balancing constraints",
    ],
    "Housing": [
        "Renters face unaffordable dwelling upgrades from stricter residential energy performance rules",
        "Flat residents face disruption when landlords renovate buildings to meet insulation standards",
    ],
    "Transport": [
        "Commuters face mobility exclusion when public transit ticketing becomes digital-only",
        "Drivers without private parking face barriers to clean vehicle adoption from charging access gaps",
    ],
}

MIXED_SIGNAL_VALID_HAZARDS = {
    "Energy": [
        "Apartment households face higher electricity bills when clean heating tariffs fund grid upgrades",
        "Tenants face utility arrears because heat pump electricity tariffs rise faster than incomes",
    ],
    "Housing": [
        "Households face rent increases when landlords retrofit homes and install heat pumps",
        "Apartment residents face displacement during building renovation for energy efficiency compliance",
    ],
    "Transport": [
        "Renters face unequal EV adoption because transport electrification depends on private home charging",
        "Low-income households face mobility exclusion when clean vehicle zones raise car access costs",
    ],
}

TITLE_CLARIFICATION_FLOWS = [
    {
        "sector": "Energy",
        "hazard": "Digital energy services leave people behind.",
        "answer_1": "",
        "answer_2": "",
        "expected_action": "ASK_TITLE_CLARIFICATION",
        "expected_step": "custom_hazard_title_clarification",
        "expected_input_mode": "text",
        "expected_error": False,
        "expected_message_contains": "Clarification Needed",
        "notes": "A transition-linked but underspecified hazard title should ask a title clarification question.",
    },
    {
        "sector": "Energy",
        "hazard": "Digital energy services leave people behind.",
        "answer_1": "I don't know",
        "answer_2": "",
        "expected_action": "REASK_TITLE_CLARIFICATION",
        "expected_step": "custom_hazard_title_clarification",
        "expected_input_mode": "text",
        "expected_error": True,
        "expected_message_contains": "does not clarify the hazard|Clarification Needed",
        "notes": "A non-answer must not be accepted as title clarification.",
    },
    {
        "sector": "Energy",
        "hazard": "Digital energy services leave people behind.",
        "answer_1": "Older adults are affected, but I am not sure how.",
        "answer_2": "what to do",
        "expected_action": "REASK_TITLE_CLARIFICATION",
        "expected_step": "custom_hazard_title_clarification",
        "expected_input_mode": "text",
        "expected_error": True,
        "expected_message_contains": "question or request|Clarification Needed",
        "notes": "Question/request-style replies must be rejected in later title clarification rounds too.",
    },
    {
        "sector": "Energy",
        "hazard": "Digital energy services leave people behind.",
        "answer_1": "Older adults and low-income households without internet access or digital skills are excluded from online-only electricity billing and support services.",
        "answer_2": "",
        "expected_action": "ACCEPT_HAZARD_NAME",
        "expected_step": "custom_hazard_validation",
        "expected_input_mode": "reason_evidence",
        "expected_error": False,
        "expected_message_contains": "Reason and Evidence Needed",
        "notes": "A meaningful clarification should be validated together with the original hazard title and proceed to reason/evidence.",
    },
]

CONTEXT_CLARIFICATION_FLOWS = [
    {
        "sector": "Energy",
        "hazard": "Low-income households face higher electricity bills from renewable grid upgrade tariffs",
        "reason": "This affects households in Germany, but I have not explained the Bavarian policy or tariff pathway yet.",
        "expected_action": "ASK_CONTEXT_CLARIFICATION",
        "expected_step": "hazards",
        "expected_input_mode": "textarea",
        "expected_error": False,
        "expected_message_contains": "Clarification|Bavaria",
        "notes": "After reason/evidence, locally incomplete support should ask a context clarification before saving the hazard.",
    },
    {
        "sector": "Housing",
        "hazard": "Tenants face rent increases after mandatory green building retrofits",
        "reason": "Retrofit obligations can raise rents, but the explanation does not identify whether tenants or owners bear the cost.",
        "expected_action": "ASK_CONTEXT_CLARIFICATION",
        "expected_step": "hazards",
        "expected_input_mode": "textarea",
        "expected_error": False,
        "expected_message_contains": "Clarification|who is affected",
        "notes": "A plausible hazard with an unresolved affected-group mechanism should stay in clarification rather than being rejected or accepted.",
    },
]

GROUNDING_DIMENSION_CLARIFICATION_FLOWS = [
    {
        "sector": "Transport",
        "hazard": "Clean mobility access problems",
        "answer_1": "Low-income commuters in Bavaria face higher travel costs when low-emission zone rules restrict older cars before affordable electric alternatives are available.",
        "expected_action": "ASK_GROUNDING_CLARIFICATION",
        "expected_step": "custom_hazard_clarification",
        "expected_input_mode": "textarea",
        "expected_error": False,
        "expected_message_contains": "more detail|selected sector",
        "notes": "A clarified title can still need dimension grounding when the affected group or policy pathway is not grounded enough.",
    },
    {
        "sector": "Energy",
        "hazard": "Smart meter rollout creates cost pressure",
        "answer_1": "Low-income households in Bavaria face installation and tariff costs when smart meter rollout shifts billing and grid charges onto consumers.",
        "expected_action": "ASK_GROUNDING_CLARIFICATION",
        "expected_step": "custom_hazard_clarification",
        "expected_input_mode": "textarea",
        "expected_error": False,
        "expected_message_contains": "more detail|green, digital",
        "notes": "Grounding clarification should be distinct from title clarification and occur after the hazard title has been accepted.",
    },
]


def row(
    *,
    category: str,
    sector: str,
    hazard: str,
    expected_action: str,
    expected_step: str,
    clarification_answer_1: str = "",
    clarification_answer_2: str = "",
    expected_input_mode: str = "",
    expected_error: bool = False,
    expected_pending_hazard: str = "",
    expected_rejected_dimension: str = "",
    expected_message_contains: str = "",
    notes: str = "",
) -> dict[str, str]:
    return {
        "Category": category,
        "Selected Country": COUNTRY,
        "Selected Region": REGION,
        "Selected Sector": sector,
        "User Hazard": hazard,
        "Clarification Answer 1": clarification_answer_1,
        "Clarification Answer 2": clarification_answer_2,
        "Expected Action": expected_action,
        "Expected Step": expected_step,
        "Expected Input Mode": expected_input_mode,
        "Expected Error": "Yes" if expected_error else "No",
        "Expected Pending Hazard": expected_pending_hazard,
        "Expected Rejected Dimension": expected_rejected_dimension,
        "Expected Message Contains": expected_message_contains,
        "Notes": notes,
    }


def make_test_cases() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []

    for sector, hazards in VALID_HAZARDS.items():
        for hazard in hazards:
            rows.append(
                row(
                    category="Valid hazard for selected sector",
                    sector=sector,
                    hazard=hazard,
                    expected_action="ACCEPT_HAZARD_NAME",
                    expected_step="custom_hazard_validation",
                    expected_input_mode="reason_evidence",
                    expected_pending_hazard=hazard,
                    expected_message_contains="Reason and Evidence Needed",
                    notes="A clear sector-specific transition risk should proceed to reason/evidence collection.",
                )
            )

    for sector, hazards in SECTOR_SYNONYM_HAZARDS.items():
        for hazard in hazards:
            rows.append(
                row(
                    category="Valid hazard with sector synonyms",
                    sector=sector,
                    hazard=hazard,
                    expected_action="ACCEPT_HAZARD_NAME",
                    expected_step="custom_hazard_validation",
                    expected_input_mode="reason_evidence",
                    expected_pending_hazard=hazard,
                    expected_message_contains="Reason and Evidence Needed",
                    notes="Synonyms such as utility, dwelling, flat, transit, or mobility should still fit the selected sector.",
                )
            )

    for sector, hazards in MIXED_SIGNAL_VALID_HAZARDS.items():
        for hazard in hazards:
            rows.append(
                row(
                    category="Valid mixed-signal hazard",
                    sector=sector,
                    hazard=hazard,
                    expected_action="ACCEPT_HAZARD_NAME",
                    expected_step="custom_hazard_validation",
                    expected_input_mode="reason_evidence",
                    expected_pending_hazard=hazard,
                    expected_message_contains="Reason and Evidence Needed",
                    notes="Some hazards mention adjacent-sector terms but should pass when the selected-sector mechanism is dominant.",
                )
            )

    for sector in SECTORS:
        for hazard in INVALID_HAZARDS:
            is_general_safety = "Carbon monoxide" in hazard
            rows.append(
                row(
                    category="Invalid or non-hazard input",
                    sector=sector,
                    hazard=hazard,
                    expected_action="REJECT_REWRITE",
                    expected_step="hazards",
                    expected_error=True,
                    expected_rejected_dimension="twin_transition_policy_fit",
                    expected_message_contains=(
                        "general household safety risk|please rewrite"
                        if is_general_safety
                        else "hazard"
                    ),
                    notes="Non-hazards, generic questions, benefits, or transition-unrelated risks should not continue.",
                )
            )

    for sector in SECTORS:
        rows.append(
            row(
                category="Generic consumer price issue",
                sector=sector,
                hazard="Rising grocery prices reduce household purchasing power in Germany's Baden-Württemberg region.",
                expected_action="REJECT_REWRITE",
                expected_step="hazards",
                expected_error=True,
                expected_rejected_dimension="twin_transition_policy_fit",
                expected_message_contains="grocery or food-price pressure",
                notes="General grocery-price or purchasing-power harms should not pass as sector transition hazards.",
            )
        )

    for sector in SECTORS:
        for hazard in VAGUE_OR_INCOMPLETE_HAZARDS:
            rows.append(
                row(
                    category="Vague or incomplete hazard",
                    sector=sector,
                    hazard=hazard,
                    expected_action="ASK_TITLE_CLARIFICATION",
                    expected_step="custom_hazard_title_clarification",
                    expected_input_mode="text",
                    expected_message_contains="Clarification Needed",
                    notes="Transition-linked but underspecified policy-topic statements should ask for hazard-title clarification before reason/evidence.",
                )
            )

    for flow in TITLE_CLARIFICATION_FLOWS:
        rows.append(
            row(
                category="Hazard title clarification flow",
                sector=str(flow["sector"]),
                hazard=str(flow["hazard"]),
                clarification_answer_1=str(flow["answer_1"]),
                clarification_answer_2=str(flow["answer_2"]),
                expected_action=str(flow["expected_action"]),
                expected_step=str(flow["expected_step"]),
                expected_input_mode=str(flow["expected_input_mode"]),
                expected_error=bool(flow["expected_error"]),
                expected_message_contains=str(flow["expected_message_contains"]),
                notes=str(flow["notes"]),
            )
        )

    for flow in CONTEXT_CLARIFICATION_FLOWS:
        rows.append(
            row(
                category="Hazard clarification after reason",
                sector=str(flow["sector"]),
                hazard=str(flow["hazard"]),
                clarification_answer_1=str(flow["reason"]),
                expected_action=str(flow["expected_action"]),
                expected_step=str(flow["expected_step"]),
                expected_input_mode=str(flow["expected_input_mode"]),
                expected_error=bool(flow["expected_error"]),
                expected_message_contains=str(flow["expected_message_contains"]),
                notes=str(flow["notes"]),
            )
        )

    for flow in GROUNDING_DIMENSION_CLARIFICATION_FLOWS:
        rows.append(
            row(
                category="Grounding dimension clarification flow",
                sector=str(flow["sector"]),
                hazard=str(flow["hazard"]),
                clarification_answer_1=str(flow["answer_1"]),
                expected_action=str(flow["expected_action"]),
                expected_step=str(flow["expected_step"]),
                expected_input_mode=str(flow["expected_input_mode"]),
                expected_error=bool(flow["expected_error"]),
                expected_message_contains=str(flow["expected_message_contains"]),
                notes=str(flow["notes"]),
            )
        )

    for sector in SECTORS:
        for hazard in BENEFIT_OR_MITIGATION_STATEMENTS:
            rows.append(
                row(
                    category="Benefit or mitigation statement",
                    sector=sector,
                    hazard=hazard,
                    expected_action="REJECT_REWRITE",
                    expected_step="hazards",
                    expected_error=True,
                    expected_rejected_dimension="twin_transition_policy_fit",
                    expected_message_contains="hazard",
                    notes="Benefits and mitigation actions are not hazards unless they describe a concrete harm or risk.",
                )
            )

    for selected_sector in SECTORS:
        for source_sector, hazard in CROSS_SECTOR_HAZARDS.items():
            if source_sector == selected_sector:
                continue
            rows.append(
                row(
                    category="Wrong-sector hazard",
                    sector=selected_sector,
                    hazard=hazard,
                    expected_action="REJECT_SECTOR_MISMATCH",
                    expected_step="hazards",
                    expected_error=True,
                    expected_rejected_dimension="selected_sector_fit",
                    expected_message_contains="sector",
                    notes=(
                        f"A {source_sector} hazard submitted while {selected_sector} is selected "
                        "should ask the user to rewrite it or choose the matching sector."
                    ),
                )
            )

    for sector in SECTORS:
        rows.append(
            row(
                category="Empty hazard input",
                sector=sector,
                hazard="",
                expected_action="SHOW_ADD_HAZARD_PROMPT",
                expected_step="hazards",
                expected_error=True,
                expected_message_contains=f"Add a New Hazard|{sector}",
                notes="Blank input should keep the user on hazard creation.",
            )
        )
        rows.append(
            row(
                category="Go back from hazard creation",
                sector=sector,
                hazard="Go back to list of hazards",
                expected_action="GO_BACK_TO_HAZARDS",
                expected_step="hazards",
                expected_message_contains=f"{sector} selected. Selection flow completed.",
                notes="The explicit back option should return to the post-sector hazards screen.",
            )
        )

    return rows


def add_test_cases_sheet(workbook: Workbook, rows: list[dict[str, str]]) -> None:
    sheet = workbook.active
    sheet.title = TEST_SHEET
    sheet.append(COLUMNS)

    for index, item in enumerate(rows, start=1):
        sheet.append([f"HC-{index:03d}", *[item.get(column, "") for column in COLUMNS[1:]]])

    style_sheet(sheet)


def add_summary_sheet(workbook: Workbook, rows: list[dict[str, str]]) -> None:
    sheet = workbook.create_sheet(SUMMARY_SHEET)
    sheet.append(["Category", "Number of Test Cases"])
    counts = Counter(str(item["Category"]) for item in rows)
    for category in sorted(counts):
        sheet.append([category, counts[category]])
    style_sheet(sheet)


def style_sheet(sheet) -> None:
    header_fill = PatternFill(fill_type="solid", fgColor="1F4E78")
    header_font = Font(color="FFFFFF", bold=True)
    header_alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    body_alignment = Alignment(vertical="top", wrap_text=True)

    for cell in sheet[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = header_alignment

    for row_cells in sheet.iter_rows(min_row=2):
        for cell in row_cells:
            cell.alignment = body_alignment

    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions

    for column_cells in sheet.columns:
        max_length = 0
        column_letter = get_column_letter(column_cells[0].column)
        for cell in column_cells:
            value = "" if cell.value is None else str(cell.value)
            max_length = max(max_length, min(len(value), 80))
        sheet.column_dimensions[column_letter].width = max(14, min(max_length + 2, 55))


def create_workbook(output_path: str | Path = OUTPUT_FILE) -> Path:
    output = Path(output_path).resolve()
    rows = make_test_cases()

    workbook = Workbook()
    add_test_cases_sheet(workbook, rows)
    add_summary_sheet(workbook, rows)
    workbook.save(output)
    return output


def main() -> None:
    output = create_workbook(Path.cwd() / OUTPUT_FILE)
    print(f"Created Excel hazard-creation test-case file: {output}")


if __name__ == "__main__":
    main()
