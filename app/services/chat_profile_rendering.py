import re
from html import escape

from app.services.chat_options import normalize_for_match


class ChatProfileRenderingMixin:
    def _format_hazard_profiles_markdown(
        self,
        hazard: str,
        profiles: list[dict[str, str]],
        *,
        user_profiles: list[dict[str, str]] | None = None,
    ) -> str:
        lines = [f"### Socio-demographic profiles most affected by {hazard}"]
        if not profiles and not user_profiles:
            lines.append("- No clearly supported socio-demographic profiles were returned for this hazard.")
            return "\n".join(lines)
        rows = self._hazard_profile_table_rows(profiles)
        user_rows = self._hazard_profile_table_rows(
            [self._system_style_user_profile(profile) for profile in (user_profiles or [])]
        )
        if rows:
            lines.append("")
            lines.append(
                self._hazard_profile_table_html(
                    rows,
                    show_admin_details=self._show_profile_admin_details(),
                )
            )
        if user_rows:
            lines.append("")
            lines.append("#### User-added socio-demographic profiles")
            lines.append(
                self._hazard_profile_table_html(
                    user_rows,
                    show_admin_details=self._show_profile_admin_details(),
                )
            )
        return "\n".join(lines)

    def _show_profile_admin_details(self) -> bool:
        return bool(getattr(self, "is_admin", False))

    @classmethod
    def _hazard_profile_table_rows(
        cls, profiles: list[dict[str, str]]
    ) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for profile in profiles:
            name = str(profile.get("name") or profile.get("profile") or "").strip()
            if not name:
                continue
            variable_name = str(profile.get("variable_name") or profile.get("variable") or "").strip()
            variable_type = str(profile.get("variable_type") or "").strip()
            target_population_labels = cls._list_from_profile_or_metadata(
                profile,
                "target_population_labels",
            )
            population_lookup_labels = cls._list_from_profile_or_metadata(
                profile,
                "population_lookup_labels",
            )
            rows.append(
                {
                    "name": name,
                    "explanation": cls._clean_profile_explanation(
                        str(profile.get("explanation") or "").strip()
                    ),
                    "statistical_basis": str(profile.get("statistical_basis") or "").strip(),
                    "target_population_labels": target_population_labels,
                    "population_lookup_labels": population_lookup_labels,
                    "regional": profile.get("regional_population_pct")
                    or profile.get("population_pct"),
                    "national": profile.get("national_population_pct"),
                    "is_macro": cls._profile_variable_type(variable_name, variable_type) == "macro",
                }
            )
        return rows

    @staticmethod
    def _list_from_profile_or_metadata(profile: dict[str, object], key: str) -> list[object]:
        value = profile.get(key)
        if isinstance(value, list) and value:
            return list(value)
        metadata = profile.get("metadata")
        if isinstance(metadata, dict):
            value = metadata.get(key)
            if isinstance(value, list):
                return list(value)
        return []

    @classmethod
    def _combine_covered_profile_rows(
        cls,
        rows: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        if len(rows) < 2:
            return rows

        label_sets = [cls._mapped_label_key_set(row) for row in rows]
        covered_by: dict[int, int] = {}
        for child_index, child_labels in enumerate(label_sets):
            if not child_labels:
                continue
            parent_candidates = [
                (len(parent_labels), parent_index)
                for parent_index, parent_labels in enumerate(label_sets)
                if parent_index != child_index
                and child_labels < parent_labels
            ]
            if parent_candidates:
                _, parent_index = max(parent_candidates)
                covered_by[child_index] = parent_index

        if not covered_by:
            return rows

        combined = [dict(row) for row in rows]
        for child_index, parent_index in covered_by.items():
            child_name = str(rows[child_index].get("name") or "").strip()
            if not child_name:
                continue
            covered_names = combined[parent_index].setdefault("covered_profile_names", [])
            cls._append_unique_value(covered_names, child_name)

        return [
            row
            for index, row in enumerate(combined)
            if index not in covered_by
        ]

    @staticmethod
    def _mapped_label_key_set(row: dict[str, object]) -> set[str]:
        labels = row.get("target_population_labels")
        if not isinstance(labels, list) or not labels:
            labels = row.get("population_lookup_labels")
        if not isinstance(labels, list):
            return set()
        return {
            normalize_for_match(str(label))
            for label in labels
            if str(label).strip()
        }

    @classmethod
    def _group_selected_hazard_profile_rows(
        cls, rows: list[dict[str, object]]
    ) -> list[dict[str, object]]:
        grouped: dict[str, dict[str, object]] = {}
        ordered: list[dict[str, object]] = []
        for row in rows:
            label_groups = [
                group
                for label in row.get("target_population_labels", [])
                if (group := cls._split_target_population_label(str(label)))
            ]
            if not label_groups:
                ordered.append(row)
                continue

            question = label_groups[0][0]
            key = normalize_for_match(question)
            group = grouped.get(key)
            if group is None:
                group = {
                    "name": cls._display_target_population_question(question),
                    "explanation": "",
                    "statistical_basis": "",
                    "target_population_labels": [],
                    "population_lookup_labels": [],
                    "options": [],
                    "regional_values": [],
                    "national_values": [],
                    "regional": None,
                    "national": None,
                    "is_macro": False,
                    "is_grouped_target_population": True,
                }
                grouped[key] = group
                ordered.append(group)
            for group_question, option in label_groups:
                if normalize_for_match(group_question) == key:
                    cls._append_unique_value(group["options"], option)
            for label in row.get("target_population_labels", []):
                cls._append_unique_value(group["target_population_labels"], str(label))
            for label in row.get("population_lookup_labels", []):
                cls._append_unique_value(group["population_lookup_labels"], str(label))
            if row.get("statistical_basis") and not group.get("statistical_basis"):
                group["statistical_basis"] = str(row.get("statistical_basis") or "")
            cls._append_numeric_value(group["regional_values"], row.get("regional"))
            cls._append_numeric_value(group["national_values"], row.get("national"))

        for row in ordered:
            if row.get("is_grouped_target_population"):
                row["regional"] = cls._average_numeric_values(row.get("regional_values"))
                row["national"] = cls._average_numeric_values(row.get("national_values"))
                options = [
                    str(option).strip()
                    for option in row.get("options", [])
                    if str(option).strip()
                ]
                description_parts: list[str] = []
                if options:
                    description_parts.append("Selected options: " + "; ".join(options))
                if row.get("statistical_basis"):
                    description_parts.append(
                        "Reference: " + str(row.get("statistical_basis") or "")
                    )
                lookup_labels = [
                    str(label).strip()
                    for label in row.get("population_lookup_labels", [])
                    if str(label).strip()
                ]
                if lookup_labels:
                    description_parts.append(
                        "Eurostat population lookup: " + "; ".join(lookup_labels)
                    )
                row["explanation"] = "\n".join(description_parts)
        return ordered

    @staticmethod
    def _split_target_population_label(label: str) -> tuple[str, str] | None:
        if ":" not in label:
            return None
        question, option = [part.strip() for part in label.split(":", 1)]
        if not question or not option:
            return None
        return question, option

    @staticmethod
    def _display_target_population_question(question: str) -> str:
        cleaned = re.sub(r"\s+", " ", question.strip().rstrip("."))
        aliases = {
            "age range": "Age group",
        }
        return aliases.get(normalize_for_match(cleaned), cleaned)

    @staticmethod
    def _append_unique_value(values: object, value: str) -> None:
        if not isinstance(values, list):
            return
        cleaned = re.sub(r"\s+", " ", value).strip()
        if cleaned and cleaned.casefold() not in {str(item).casefold() for item in values}:
            values.append(cleaned)

    @staticmethod
    def _append_numeric_value(values: object, value: object) -> None:
        if not isinstance(values, list):
            return
        try:
            values.append(float(value))
        except (TypeError, ValueError):
            pass

    @staticmethod
    def _average_numeric_values(values: object) -> float | None:
        if not isinstance(values, list) or not values:
            return None
        return round(sum(values) / len(values), 1)

    @classmethod
    def _hazard_profile_table_html(
        cls,
        rows: list[dict[str, object]],
        *,
        show_admin_details: bool = False,
    ) -> str:
        rows = cls._combine_covered_profile_rows(rows)
        body_rows: list[str] = []
        for row in rows:
            macro_label = (
                '<span class="profile-type-label">MACRO</span>'
                if row.get("is_macro")
                else ""
            )
            regional = row.get("regional")
            national = row.get("national")
            description_parts: list[str] = []
            covered_profile_names = row.get("covered_profile_names")
            if isinstance(covered_profile_names, list) and covered_profile_names:
                combined_names = [
                    str(name).strip()
                    for name in covered_profile_names
                    if str(name).strip()
                ]
                if combined_names:
                    description_parts.append(
                        "Combined profiles: " + "; ".join(combined_names)
                    )
            explanation = str(row.get("explanation") or "").strip()
            if explanation and not show_admin_details:
                explanation = cls._strip_profile_admin_detail_lines(explanation)
            if explanation:
                description_parts.append(explanation)
            statistical_basis = str(row.get("statistical_basis") or "").strip()
            target_population_labels = row.get("target_population_labels")
            population_lookup_labels = row.get("population_lookup_labels")
            if show_admin_details and statistical_basis:
                description_parts.append(f"Reference: {statistical_basis}")
            if (
                show_admin_details
                and isinstance(target_population_labels, list)
                and target_population_labels
            ):
                mapped_labels = [
                    str(label).strip()
                    for label in target_population_labels
                    if str(label).strip()
                ]
                if mapped_labels:
                    description_parts.append(
                        "Mapped target population: " + "; ".join(mapped_labels)
                    )
            elif (
                show_admin_details
                and isinstance(population_lookup_labels, list)
                and population_lookup_labels
            ):
                mapped_labels = [
                    str(label).strip()
                    for label in population_lookup_labels
                    if str(label).strip()
                ]
                if mapped_labels:
                    description_parts.append(
                        "Mapped target population: " + "; ".join(mapped_labels)
                    )
            if (
                show_admin_details
                and isinstance(population_lookup_labels, list)
                and population_lookup_labels
            ):
                lookup_labels = [
                    str(label).strip()
                    for label in population_lookup_labels
                    if str(label).strip()
                ]
                if lookup_labels:
                    description_parts.append(
                        "Eurostat population lookup: " + "; ".join(lookup_labels)
                    )
            description = "\n".join(description_parts)
            description_html = "<br>".join(
                escape(part.strip())
                for part in description.splitlines()
                if part.strip()
            )
            body_rows.append(
                "<tr>"
                '<th scope="row">'
                f'<strong>{escape(str(row.get("name") or ""))}</strong>{macro_label}'
                f'{f"<small>{description_html}</small>" if description_html else ""}'
                "</th>"
                f'<td><span class="population-value">{cls._format_profile_population(regional)}</span>'
                f"{cls._profile_population_comparison(regional, national)}</td>"
                f'<td><span class="population-value">{cls._format_profile_population(national)}</span></td>'
                "</tr>"
            )
        return (
            '<div class="hazard-population-table hazard-population-table--selected">'
            "<table>"
            "<thead><tr>"
            '<th scope="col">Affected population profile</th>'
            '<th scope="col">Regional</th>'
            '<th scope="col">National</th>'
            "</tr></thead>"
            f"<tbody>{''.join(body_rows)}</tbody>"
            "</table>"
            "</div>"
        )

    @staticmethod
    def _clean_profile_explanation(explanation: str) -> str:
        cleaned = re.sub(
            r"(?i)\s*(?:This profile represents about [0-9.]+% of the regional population, "
            r"compared with [0-9.]+% nationally\.|Across \d+ matched Eurostat profiles, the average "
            r"population share is [0-9.]+% regionally and [0-9.]+% nationally\.)",
            "",
            explanation,
        )
        return re.sub(r"\s+", " ", cleaned).strip()

    @staticmethod
    def _strip_profile_admin_detail_lines(text: str) -> str:
        return "\n".join(
            line
            for line in text.splitlines()
            if not re.match(
                r"\s*(?:Reference|Plain[- ]English|Mapped target population|"
                r"Eurostat population lookup):",
                line,
                flags=re.IGNORECASE,
            )
        ).strip()

    @staticmethod
    def _format_profile_population(value: object) -> str:
        try:
            return f"{float(value):.1f}%"
        except (TypeError, ValueError):
            return "-"

    @staticmethod
    def _profile_population_comparison(regional: object, national: object) -> str:
        try:
            difference = float(regional) - float(national)
        except (TypeError, ValueError):
            return ""
        if abs(difference) < 0.05:
            return '<span class="population-trend is-equal" title="Equal to national" aria-label="equal to national">•</span>'
        if difference > 0:
            return '<span class="population-trend is-up" title="Higher than national" aria-label="higher than national">↑</span>'
        return '<span class="population-trend is-down" title="Lower than national" aria-label="lower than national">↓</span>'

    @staticmethod
    def _append_profile_lines(lines: list[str], profiles: list[dict[str, str]]) -> None:
        for profile in profiles:
            name = profile.get("name", "").strip()
            if not name:
                continue
            explanation = profile.get("explanation", "").strip()
            if explanation:
                lines.append(f"- **{name}**: {explanation}")
            else:
                lines.append(f"- **{name}**")
