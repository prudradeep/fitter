# ruff: noqa: F403,F405
from app.services.chat_mitigation_creation_common import *


class ChatMitigationCreationSystemObservationsMixin:
    def _system_inquiry_observations(
        self,
        session: ChatSession,
        attributes: dict[str, object] | None = None,
    ) -> list[dict[str, object]]:
        measure = str(session.mitigation_measure or "").strip() or "the mitigation measure"
        hazard = str(session.selected_hazard or "the selected hazard").strip()
        groups = self._system_inquiry_group_labels(session)
        group_label = groups[0] if groups else "the intended target group"
        prior_measures = self._system_inquiry_prior_measure_rows(session)
        attributes = attributes or self._system_inquiry_measure_attributes(
            session.mitigation_measure,
            session.mitigation_reason,
            groups,
        )
        session.system_inquiry_attributes = attributes
        observations: list[dict[str, object]] = []
        measure_text = normalize_for_match(
            " ".join([measure, str(session.mitigation_reason or ""), hazard, " ".join(groups)])
        )
        validation_gaps = self._system_inquiry_validation_gaps(session)

        observations.append(
            {
                "probe_id": "A1-P1",
                "lens_id": "A1",
                "family": "A_structure",
                "title": "Boundary of the measure",
                "corpus_label": "unproven",
                "observation": (
                    f"{measure} draws a boundary around {hazard} and "
                    f"{group_label}, but that boundary may leave out related causes, "
                    "constraints, or side effects."
                ),
                "why_it_matters": (
                    "Every mitigation measure includes some things and excludes others; "
                    "the excluded edge can be where implementation fails."
                ),
                "question": (
                    "What important group, place, cause, or side effect sits just outside "
                    "the boundary this measure draws? "
                ),
                "followup_types": ["name_group"],
                "anchors": {"measure": measure, "hazard": hazard, "groups": groups},
                "salience": 66,
            }
        )

        if self._system_inquiry_has_feedback_loop_signal(measure_text):
            observations.append(
                {
                    "probe_id": "A3-P1",
                    "lens_id": "A3",
                    "family": "A_structure",
                    "title": "Feedback loops",
                    "corpus_label": "unproven",
                    "observation": (
                        f"{measure} could change uptake, demand, participation, or prices "
                        f"in ways that then change conditions around {hazard}."
                    ),
                    "why_it_matters": (
                        "When a measure changes the system it depends on, the first-order "
                        "effect can hide the second-order response."
                    ),
                    "question": (
                        "If this measure succeeds, what behavior, demand, or price change "
                        "could it trigger next?"
                    ),
                    "followup_types": ["specify_mechanism"],
                    "anchors": {"measure": measure, "hazard": hazard, "groups": groups},
                    "salience": 88,
                }
            )

        if validation_gaps:
            gap = validation_gaps[0]
            gap_name = str(gap.get("name") or "a validation dimension").strip()
            gap_status = str(gap.get("status") or "insufficient").strip()
            gap_explanation = str(gap.get("explanation") or "").strip()
            observations.append(
                {
                    "probe_id": "B2-P1",
                    "lens_id": "B2",
                    "family": "B_framing",
                    "title": "Untested assumptions",
                    "corpus_label": "evidenced",
                    "observation": (
                        f"The mitigation validation still has an open {gap_name} "
                        f"dimension ({gap_status})."
                    ),
                    "why_it_matters": (
                        "A dimension that is not yet supported often points to an "
                        "assumption the measure is still carrying."
                    ),
                    "question": (
                        f"What assumption is still untested in the {gap_name} dimension?"
                    ),
                    "followup_types": ["specify_mechanism"],
                    "anchors": {
                        "measure": measure,
                        "hazard": hazard,
                        "groups": groups,
                        "predictors": (
                            [gap_name, gap_explanation] if gap_explanation else [gap_name]
                        ),
                    },
                    "salience": 94,
                }
            )

        if self._system_inquiry_defines_criteria(measure_text, attributes):
            observations.append(
                {
                    "probe_id": "B3-P1",
                    "lens_id": "B3",
                    "family": "B_framing",
                    "title": "Worldview plurality and expertise",
                    "corpus_label": "unproven",
                    "observation": (
                        f"{measure} appears to set eligibility, priority, or success "
                        f"criteria for {group_label}."
                    ),
                    "why_it_matters": (
                        "Criteria are never neutral: they privilege some expertise, lived "
                        "experience, and definitions of success over others."
                    ),
                    "question": (
                        "Whose knowledge or perspective shaped these criteria, and who might "
                        "read them differently?"
                    ),
                    "followup_types": ["name_group", "specify_mechanism"],
                    "anchors": {"measure": measure, "hazard": hazard, "groups": groups},
                    "salience": 86,
                }
            )

        if self._system_inquiry_delegates_to_intermediary(measure_text, attributes):
            observations.append(
                {
                    "probe_id": "B4-P1",
                    "lens_id": "B4",
                    "family": "B_framing",
                    "title": "Boundary judgements of power and legitimacy",
                    "corpus_label": "unproven",
                    "observation": (
                        f"{measure} seems to rely on an intermediary or delegated actor to "
                        f"reach {group_label}."
                    ),
                    "why_it_matters": (
                        "Delegated delivery shifts practical power to an actor who may make "
                        "their own judgments about who deserves access."
                    ),
                    "question": (
                        "Which intermediary decides access here, and what keeps that actor "
                        "accountable?"
                    ),
                    "followup_types": ["specify_mechanism"],
                    "anchors": {"measure": measure, "hazard": hazard, "groups": groups},
                    "salience": 84,
                }
            )

        if attributes["cost_incidence"] in {"upfront_user_cost", "ongoing_user_cost"}:
            c1_evidence = self._system_inquiry_financial_strain_evidence(
                session,
                groups,
            )
            observations.append(
                {
                    "probe_id": "C1-P1",
                    "lens_id": "C1",
                    "family": "C_justice",
                    "title": "Distributional incidence",
                    "corpus_label": c1_evidence["corpus_label"],
                    "observation": (
                        f"{measure} appears to involve a payment, subsidy, grant, tariff, "
                        f"or other cost pathway affecting {group_label}."
                    ),
                    "why_it_matters": (
                        "A measure can be fair in intention but still miss people who cannot "
                        "carry upfront or ongoing costs."
                    ),
                    "question": (
                        f"For {group_label}, what happens if they cannot pay "
                        "or wait for reimbursement?"
                    ),
                    "followup_types": ["specify_mechanism"],
                    "anchors": {
                        "measure": measure,
                        "hazard": hazard,
                        "groups": groups,
                        "predictors": c1_evidence["predictors"],
                    },
                    "citations": c1_evidence["citations"],
                    "salience": 95,
                }
            )

        omitted_groups = self._system_inquiry_omitted_affected_groups(session)
        if omitted_groups:
            omitted_group = omitted_groups[0]
            c2_evidence = self._system_inquiry_affected_profile_evidence(
                session,
                omitted_groups,
            )
            observations.append(
                {
                    "probe_id": "C2-P1",
                    "lens_id": "C2",
                    "family": "C_justice",
                    "title": "Recognition",
                    "corpus_label": c2_evidence["corpus_label"],
                    "observation": (
                        f"{hazard} is associated with {omitted_group} in this "
                        f"session's affected-population profile. {measure} does not "
                        "name that characteristic in the target population."
                    ),
                    "why_it_matters": (
                        "A group left unnamed may still be reached, but its specific "
                        "barriers can disappear from implementation decisions."
                    ),
                    "question": (
                        f"Is omitting {omitted_group} deliberate, or would this "
                        "measure reach them without naming them?"
                    ),
                    "followup_types": ["name_group"],
                    "anchors": {
                        "measure": measure,
                        "hazard": hazard,
                        "omitted_groups": omitted_groups,
                        "predictors": c2_evidence["predictors"],
                    },
                    "citations": c2_evidence["citations"],
                    "salience": 90,
                }
            )

        if attributes["delivery_channel"] in {"application", "means_tested", "intermediary"}:
            observations.append(
                {
                    "probe_id": "C3-P1",
                    "lens_id": "C3",
                    "family": "C_justice",
                    "title": "Procedural access",
                    "corpus_label": "unproven",
                    "observation": (
                        f"{measure} seems to depend on an application, eligibility check, "
                        "intermediary, or documented process."
                    ),
                    "why_it_matters": (
                        "Administrative steps can under-reach the groups with the least time, "
                        "documentation, language access, or digital access."
                    ),
                    "question": (
                        f"Who helps {group_label} complete the process if they "
                        "cannot navigate it alone?"
                    ),
                    "followup_types": ["specify_mechanism", "name_group"],
                    "anchors": {"measure": measure, "hazard": hazard, "groups": groups},
                    "salience": 90,
                }
            )

        named_sectors = [
            str(item)
            for item in attributes.get("named_sectors", [])
            if str(item).strip()
        ]
        selected_sector = normalize_for_match(str(session.sector or ""))
        secondary_sectors = [
            sector
            for sector in named_sectors
            if normalize_for_match(sector) and normalize_for_match(sector) != selected_sector
        ]
        if secondary_sectors:
            secondary_sector = secondary_sectors[0]
            observations.append(
                {
                    "probe_id": "A2-P1",
                    "lens_id": "A2",
                    "family": "A_structure",
                    "title": "Cross-sector coupling",
                    "corpus_label": "unproven",
                    "observation": (
                        f"{measure} is framed in the {session.sector or 'selected'} "
                        f"sector but also names or depends on {secondary_sector}."
                    ),
                    "why_it_matters": (
                        "A mitigation measure can fail at the handoff between sectors even "
                        "when it is coherent inside one sector."
                    ),
                    "question": (
                        f"Who is responsible for the {secondary_sector} part of this "
                        "measure, and what happens if that sector does not deliver?"
                    ),
                    "followup_types": ["specify_mechanism"],
                    "anchors": {
                        "measure": measure,
                        "hazard": hazard,
                        "groups": groups,
                        "sectors": [secondary_sector],
                    },
                    "salience": 82,
                }
            )

        if attributes["action_type"] in {"subsidy", "grant", "tariff", "tax"}:
            observations.append(
                {
                    "probe_id": "A6-P1",
                    "lens_id": "A6",
                    "family": "A_structure",
                    "title": "Policy resistance and rebound",
                    "corpus_label": "unproven",
                    "observation": (
                        f"{measure} changes the financial signal around {hazard} for "
                        f"{group_label}."
                    ),
                    "why_it_matters": (
                        "Financial measures can be absorbed by prices, eligibility behaviour, "
                        "or supplier responses unless the feedback path is explicit."
                    ),
                    "question": (
                        "Who could change their behaviour in response to this financial "
                        "measure, and how would you monitor that?"
                    ),
                    "followup_types": ["specify_mechanism"],
                    "anchors": {"measure": measure, "hazard": hazard, "groups": groups},
                    "salience": 78,
                }
            )

        if bool(attributes.get("requires_capacity")):
            capacity_type = str(attributes.get("capacity_type") or "capacity").replace("_", " ")
            observations.append(
                {
                    "probe_id": "A7-P1",
                    "lens_id": "A7",
                    "family": "A_structure",
                    "title": "Capacity and stock constraints",
                    "corpus_label": "unproven",
                    "observation": (
                        f"{measure} appears to depend on {capacity_type} capacity "
                        f"being available for {group_label}."
                    ),
                    "why_it_matters": (
                        "If scarce delivery capacity is allocated first to easier or more "
                        "profitable cases, the named target group may wait longest."
                    ),
                    "question": (
                        f"What ensures {group_label} gets access to that capacity rather "
                        "than being left at the back of the queue?"
                    ),
                    "followup_types": ["specify_mechanism", "state_timeframe"],
                    "anchors": {"measure": measure, "hazard": hazard, "groups": groups},
                    "salience": 82,
                }
            )

        if attributes["time_to_benefit"] in {"months", "years"}:
            observations.append(
                {
                    "probe_id": "A4-P1",
                    "lens_id": "A4",
                    "family": "A_structure",
                    "title": "Delay and time horizon",
                    "corpus_label": "unproven",
                    "observation": (
                        f"{measure} appears to deliver benefits after an implementation period, "
                        f"while {hazard} may affect {group_label} before then."
                    ),
                    "why_it_matters": (
                        "Delayed benefits can leave the most exposed groups carrying risk during "
                        "the transition interval."
                    ),
                    "question": (
                        f"What do you expect {group_label} to do before this "
                        "measure is fully working?"
                    ),
                    "followup_types": ["state_timeframe", "specify_mechanism"],
                    "anchors": {"measure": measure, "hazard": hazard, "groups": groups},
                    "salience": 85,
                }
            )

        if attributes["time_to_benefit"] == "years":
            observations.append(
                {
                    "probe_id": "C4-P1",
                    "lens_id": "C4",
                    "family": "C_justice",
                    "title": "Long-term burden",
                    "corpus_label": "unproven",
                    "observation": (
                        f"{measure} has a long time horizon. The costs, disruption, or "
                        f"asset choices it creates may outlast the immediate response to {hazard}."
                    ),
                    "why_it_matters": (
                        "Long-lived measures can shift burdens to people who are not in the "
                        "room now, including future residents or younger households."
                    ),
                    "question": (
                        "Who carries the long-term cost, maintenance, or lock-in risk once "
                        "this measure is in place?"
                    ),
                    "followup_types": ["name_group", "state_timeframe"],
                    "anchors": {"measure": measure, "hazard": hazard, "groups": groups},
                    "salience": 76,
                }
            )

        systemic_score = self._system_inquiry_systemic_score(session)
        if systemic_score >= 7 and attributes["leverage_depth"] == "parameter":
            observations.append(
                {
                    "probe_id": "A5-P1",
                    "lens_id": "A5",
                    "family": "A_structure",
                    "title": "Leverage-point depth",
                    "corpus_label": "unproven",
                    "observation": (
                        f"You rated systemic or structural impact {systemic_score}/10. "
                        f"As written, {measure} mainly appears to adjust support, cost, "
                        "rates, or access within existing rules."
                    ),
                    "why_it_matters": (
                        "A parameter-level measure can help immediately while leaving the "
                        "rules that produced the hazard unchanged."
                    ),
                    "question": (
                        "What would have to change beyond the payment level or "
                        "service level for this to produce structural change?"
                    ),
                    "followup_types": ["specify_mechanism"],
                    "anchors": {
                        "measure": measure,
                        "hazard": hazard,
                        "self_eval_score": systemic_score,
                    },
                    "salience": 75,
                }
            )

        interacting_prior = self._system_inquiry_interacting_prior_measure(
            session,
            prior_measures,
            attributes,
        )
        if interacting_prior is not None:
            prior_measure = str(interacting_prior.get("measure") or "the earlier measure")
            interaction_summary = str(interacting_prior.get("summary") or "").strip()
            observations.append(
                {
                    "probe_id": "D1-P1",
                    "lens_id": "D1",
                    "family": "D_portfolio",
                    "title": "Measure interaction",
                    "corpus_label": "unproven",
                    "observation": (
                        f"{measure} and {prior_measure} interact: "
                        f"{interaction_summary} Neither description acknowledges the other."
                    ),
                    "why_it_matters": (
                        "Interacting measures can work well together, but only if their "
                        "sequencing and assumptions are explicit."
                    ),
                    "question": (
                        "Do you intend these two to run together, and if so which takes "
                        "priority when they compete?"
                    ),
                    "followup_types": ["state_timeframe", "specify_mechanism"],
                    "anchors": {
                        "measure": measure,
                        "prior_measure": prior_measure,
                        "interaction_summary": interaction_summary,
                        "groups": groups,
                    },
                    "salience": 80,
                }
            )

        shared_prior = self._system_inquiry_shared_group_prior_measure(
            session,
            prior_measures,
        )
        if shared_prior is not None:
            shared_group = str(shared_prior.get("shared_group") or group_label)
            prior_measure = str(shared_prior.get("measure") or "the earlier measure")
            observations.append(
                {
                    "probe_id": "D2-P1",
                    "lens_id": "D2",
                    "family": "D_portfolio",
                    "title": "Cumulative burden",
                    "corpus_label": "unproven",
                    "observation": (
                        f"{shared_group} is targeted by both {prior_measure} and "
                        f"{measure}. Together these may ask the same group to absorb "
                        "cost, time, administrative effort, or disruption."
                    ),
                    "why_it_matters": (
                        "Cumulative burden is often invisible when each mitigation "
                        "measure is reviewed on its own."
                    ),
                    "question": (
                        f"Is {shared_group} able to take on both at once, and if not, "
                        "which comes first?"
                    ),
                    "followup_types": ["state_timeframe", "name_group"],
                    "anchors": {
                        "measure": measure,
                        "prior_measure": prior_measure,
                        "shared_group": shared_group,
                    },
                    "salience": 90,
                }
            )

        if (
            len(prior_measures) >= 2
            and attributes["leverage_depth"] == "parameter"
            and self._system_inquiry_prior_parameter_share(prior_measures) >= 0.8
        ):
            observations.append(
                {
                    "probe_id": "D3-P1",
                    "lens_id": "D3",
                    "family": "D_portfolio",
                    "title": "Leverage concentration",
                    "corpus_label": "unproven",
                    "observation": (
                        f"All {len(prior_measures) + 1} mitigation measures seen so far "
                        "appear to adjust support, costs, eligibility, or service "
                        "levels within existing rules."
                    ),
                    "why_it_matters": (
                        "A portfolio concentrated at parameter level can redistribute "
                        "within the current structure without changing the structure itself."
                    ),
                    "question": (
                        f"Is there a rule or institutional arrangement that would reduce "
                        f"{hazard} more durably than adjusting these levels?"
                    ),
                    "followup_types": ["specify_mechanism"],
                    "anchors": {
                        "measure": measure,
                        "prior_measure_count": len(prior_measures),
                    },
                    "salience": 85,
                }
            )

        observations.append(
            {
                "probe_id": "B1-P1",
                "lens_id": "B1",
                "family": "B_framing",
                "title": "Problem framing",
                "corpus_label": "unproven",
                "observation": (
                    f"{measure} frames the response around {hazard} and "
                    f"{group_label}."
                ),
                "why_it_matters": (
                    "Every mitigation measure draws a boundary around what counts as the "
                    "problem and who is expected to act."
                ),
                "question": (
                    "What important group, place, cause, or side effect sits "
                    "just outside that boundary?"
                ),
                "followup_types": ["name_group"],
                "anchors": {"measure": measure, "hazard": hazard, "groups": groups},
                "salience": 70,
            }
        )
        observations.extend(
            self._system_inquiry_additional_library_observations(
                session,
                attributes,
                measure=measure,
                hazard=hazard,
                groups=groups,
                group_label=group_label,
                measure_text=measure_text,
                validation_gaps=validation_gaps,
                prior_measures=prior_measures,
            )
        )

        candidates = [
            self._system_inquiry_enriched_candidate(observation, session)
            for observation in observations
        ]
        self._apply_system_inquiry_prior_dedupe_reuse(
            session,
            candidates,
            prior_measures,
        )
        cap = 3 if prior_measures else 2
        deduped = self._system_inquiry_finalize_candidates(
            candidates,
            cap=cap,
            prior_surface_count=self._system_inquiry_prior_surface_count(prior_measures),
            require_portfolio=bool(prior_measures),
        )
        selected_keys = {str(item.get("candidate_id") or "") for item in deduped}
        session.system_inquiry_candidate_audit = list(candidates)
        self._set_system_inquiry_held_observations(session, candidates, selected_keys)
        return deduped

    async def _system_inquiry_observations_with_llm(
        self,
        session: ChatSession,
    ) -> list[dict[str, object]]:
        groups = self._system_inquiry_group_labels(session)
        attributes = await self._system_inquiry_measure_attributes_with_llm(session, groups)
        observations = self._system_inquiry_observations(session, attributes=attributes)
        candidates = list(session.system_inquiry_candidate_audit or [])
        if not candidates:
            return observations

        await self._system_inquiry_screen_candidates_with_llm(session, candidates)
        await self._system_inquiry_verify_candidates_with_llm(session, candidates)
        await self._system_inquiry_adjudicate_corpus_with_llm(session, candidates)

        prior_measures = self._system_inquiry_prior_measure_rows(session)
        cap = 3 if prior_measures else 2
        selected = self._system_inquiry_finalize_candidates(
            candidates,
            cap=cap,
            prior_surface_count=self._system_inquiry_prior_surface_count(prior_measures),
            require_portfolio=bool(prior_measures),
        )
        selected_keys = {str(item.get("candidate_id") or "") for item in selected}
        session.system_inquiry_candidate_audit = candidates
        self._set_system_inquiry_held_observations(session, candidates, selected_keys)
        return selected

    def _system_inquiry_additional_library_observations(
        self,
        session: ChatSession,
        attributes: dict[str, object],
        *,
        measure: str,
        hazard: str,
        groups: list[str],
        group_label: str,
        measure_text: str,
        validation_gaps: list[dict[str, object]],
        prior_measures: list[UserMitigationMeasure],
    ) -> list[dict[str, object]]:
        records = system_inquiry_probe_library().get("records")
        if not isinstance(records, dict):
            return []
        current_ids = {
            "A1-P1", "A2-P1", "A3-P1", "A4-P1", "A5-P1", "A6-P1", "A7-P1",
            "B1-P1", "B2-P1", "B3-P1", "B4-P1", "C1-P1", "C2-P1", "C3-P1",
            "C4-P1", "D1-P1", "D2-P1", "D3-P1",
        }
        observations: list[dict[str, object]] = []
        for probe_id in sorted(str(key) for key in records if str(key) not in current_ids):
            trigger = self._system_inquiry_extra_probe_triggered(
                probe_id,
                session,
                attributes,
                measure_text=measure_text,
                validation_gaps=validation_gaps,
                prior_measures=prior_measures,
            )
            if not trigger:
                continue
            observations.append(
                self._system_inquiry_library_observation(
                    probe_id,
                    measure=measure,
                    hazard=hazard,
                    groups=groups,
                    group_label=group_label,
                    prior_measures=prior_measures,
                    attributes=attributes,
                )
            )
        return observations

    def _system_inquiry_extra_probe_triggered(
        self,
        probe_id: str,
        session: ChatSession,
        attributes: dict[str, object],
        *,
        measure_text: str,
        validation_gaps: list[dict[str, object]],
        prior_measures: list[UserMitigationMeasure],
    ) -> bool:
        text = measure_text
        action_type = str(attributes.get("action_type") or "")
        delivery = str(attributes.get("delivery_channel") or "")
        time_to_benefit = str(attributes.get("time_to_benefit") or "")
        depth = str(attributes.get("leverage_depth") or "")
        if probe_id == "A1-P2":
            return any(term in text for term in ("only", "eligible", "target", "priority"))
        if probe_id == "A2-P2":
            return any(term in text for term in ("agency", "municipal", "landlord", "association", "provider", "utility"))
        if probe_id == "A3-P2":
            return self._system_inquiry_has_feedback_loop_signal(text)
        if probe_id == "A4-P2":
            return time_to_benefit in {"months", "years"} and not any(
                term in text for term in ("interim", "temporary", "bridge", "emergency")
            )
        if probe_id == "A5-P2":
            return depth == "parameter"
        if probe_id == "A6-P2":
            return action_type in {"subsidy", "grant", "tariff", "tax"} and any(
                term in text for term in ("supplier", "provider", "contractor", "market", "price")
            )
        if probe_id == "A7-P2":
            return bool(attributes.get("requires_capacity")) and any(
                term in text for term in ("queue", "waiting", "wait", "priority", "limited", "scarce")
            )
        if probe_id == "B1-P2":
            return True
        if probe_id == "B2-P2":
            return bool(validation_gaps)
        if probe_id == "B3-P2":
            return self._system_inquiry_defines_criteria(text, attributes)
        if probe_id == "B4-P2":
            return self._system_inquiry_delegates_to_intermediary(text, attributes)
        if probe_id == "C1-P2":
            return str(attributes.get("cost_incidence") or "") in {
                "upfront_user_cost",
                "ongoing_user_cost",
            } and any(term in text for term in ("reimburse", "deposit", "loan", "co pay", "copay", "contribution"))
        if probe_id == "C2-P2":
            return any(
                "lower" in normalize_for_match(str(item))
                for item in self._system_inquiry_affected_profile_details(session)
            )
        if probe_id == "C3-P2":
            return delivery in {"application", "means_tested", "intermediary"} and any(
                term in text for term in ("document", "proof", "certif", "digital", "online", "form")
            )
        if probe_id == "C4-P2":
            return time_to_benefit == "years" and any(
                term in text for term in ("maintain", "maintenance", "asset", "equipment", "retrofit", "install")
            )
        if probe_id == "D1-P2":
            return len(prior_measures) >= 1 and any(term in text for term in ("before", "after", "first", "then", "sequence"))
        if probe_id == "D2-P2":
            return self._system_inquiry_shared_group_prior_measure(session, prior_measures) is not None and delivery in {
                "application", "means_tested", "intermediary",
            }
        if probe_id == "D3-P2":
            return (
                len(prior_measures) >= 2
                and depth == "parameter"
                and self._system_inquiry_prior_parameter_share(prior_measures) >= 0.8
            )
        return False

    def _system_inquiry_library_observation(
        self,
        probe_id: str,
        *,
        measure: str,
        hazard: str,
        groups: list[str],
        group_label: str,
        prior_measures: list[UserMitigationMeasure],
        attributes: dict[str, object],
    ) -> dict[str, object]:
        record = system_inquiry_probe_record(probe_id) or {}
        title = str(record.get("title") or probe_id)
        prior_measure = (
            str(getattr(prior_measures[-1], "measure", "") or "").strip()
            if prior_measures
            else ""
        )
        anchors: dict[str, object] = {"measure": measure, "hazard": hazard, "groups": groups}
        if probe_id.startswith("D1") and prior_measure:
            anchors["prior_measure"] = prior_measure
        if probe_id.startswith("D2"):
            shared = self._system_inquiry_shared_group_prior_measure(
                ChatSession(
                    selected_hazard=hazard,
                    mitigation_measure=measure,
                    mitigation_target_population=groups,
                ),
                prior_measures,
            )
            anchors["shared_group"] = str((shared or {}).get("shared_group") or group_label)
            if prior_measure:
                anchors["prior_measure"] = prior_measure
        if probe_id.startswith("D3"):
            anchors = {"measure": measure, "prior_measure_count": len(prior_measures)}
        if probe_id == "C2-P2":
            anchors["predictors"] = [f"Protective or lower-concern predictor for {hazard}"]
        observation = (
            f"{measure} raises the '{title}' lens for {hazard}"
            f"{' and ' + group_label if group_label else ''}."
        )
        if prior_measure and probe_id.startswith("D"):
            observation += f" The relevant prior measure is {prior_measure}."
        why = self._system_inquiry_library_why_it_matters(probe_id, title)
        question = self._system_inquiry_library_question(
            probe_id,
            title=title,
            measure=measure,
            hazard=hazard,
            group_label=group_label,
            attributes=attributes,
        )
        return {
            "probe_id": probe_id,
            "lens_id": str(record.get("lens_id") or probe_id.split("-", 1)[0]),
            "family": str(record.get("family") or "A_structure"),
            "title": title,
            "corpus_label": "unproven",
            "observation": observation,
            "why_it_matters": why,
            "question": question,
            "followup_types": list(record.get("followup_types") or ["specify_mechanism"]),
            "anchors": anchors,
            "salience": int(record.get("salience") or 70),
        }

    @staticmethod
    def _system_inquiry_library_why_it_matters(probe_id: str, title: str) -> str:
        if probe_id.startswith("A"):
            return "Structural patterns can affect whether a measure works beyond its first intended effect."
        if probe_id.startswith("B"):
            return "Framing choices decide whose knowledge, assumptions, and responsibilities become visible."
        if probe_id.startswith("C"):
            return "Justice effects often appear in access, recognition, timing, and who carries practical burdens."
        if probe_id.startswith("D"):
            return "Portfolio effects are easy to miss when each measure is considered alone."
        return f"The {title} lens checks a possible blind spot before the measure is recorded."

    @staticmethod
    def _system_inquiry_library_question(
        probe_id: str,
        *,
        title: str,
        measure: str,
        hazard: str,
        group_label: str,
        attributes: dict[str, object],
    ) -> str:
        if probe_id.endswith("P2") and probe_id.startswith("A4"):
            return f"What interim protection exists for {group_label} before {measure} starts reducing {hazard}?"
        if probe_id.startswith("A7"):
            return f"How is scarce delivery capacity allocated so {group_label} is not pushed behind easier cases?"
        if probe_id.startswith("C3"):
            return f"What practical help is available if {group_label} cannot complete the required process alone?"
        if probe_id.startswith("D"):
            return "Does the earlier measure change the sequencing, burden, or priority decision for this measure?"
        return f"What would need to be explicit in {measure} to address the {title.casefold()} issue?"

    @staticmethod
    def _set_system_inquiry_held_observations(
        session: ChatSession,
        candidates: list[dict[str, object]],
        selected_keys: set[str],
    ) -> None:
        session.system_inquiry_held_observations = [
            {
                "probe_id": str(item.get("probe_id") or ""),
                "title": str(item.get("title") or ""),
                "family": str(item.get("family") or ""),
                "tier": str(item.get("tier") or ""),
                "library_version": str(item.get("library_version") or ""),
                "candidate_status": str(item.get("candidate_status") or ""),
                "salience": int(item.get("salience") or 0),
                "required_anchors": (
                    item.get("required_anchors")
                    if isinstance(item.get("required_anchors"), dict)
                    else {}
                ),
                "anchor_counts": (
                    item.get("anchor_counts")
                    if isinstance(item.get("anchor_counts"), dict)
                    else {}
                ),
            }
            for item in candidates
            if str(item.get("probe_id") or "")
            and str(item.get("candidate_id") or "") not in selected_keys
        ]

    def _system_inquiry_finalize_candidates(
        self,
        candidates: list[dict[str, object]],
        *,
        cap: int,
        prior_surface_count: int = 0,
        require_portfolio: bool = False,
    ) -> list[dict[str, object]]:
        selectable: list[dict[str, object]] = []
        seen_candidate_keys: set[str] = set()
        for candidate in candidates:
            probe_id = str(candidate.get("probe_id") or "").strip()
            if not probe_id:
                continue
            dedupe_key = self._system_inquiry_candidate_dedupe_key(candidate)
            if dedupe_key in seen_candidate_keys:
                candidate["candidate_status"] = "discarded_dedupe"
                continue
            seen_candidate_keys.add(dedupe_key)
            if not self._system_inquiry_candidate_has_required_anchors(candidate):
                candidate["candidate_status"] = "discarded_no_anchor"
                continue
            if str(candidate.get("corpus_label") or "").casefold() == "refuted":
                candidate["candidate_status"] = "discarded_refuted"
                continue
            if self._system_inquiry_candidate_is_unstable(candidate):
                candidate["candidate_status"] = "discarded_unstable"
                continue
            if (
                prior_surface_count >= 10
                and float(candidate.get("salience_score") or 0.0) < 0.9
            ):
                candidate["candidate_status"] = "held_cap"
                continue
            candidate["candidate_status"] = "held_cap"
            selectable.append(candidate)

        ranked = sorted(selectable, key=self._system_inquiry_candidate_rank_key)
        selected: list[dict[str, object]] = []
        family_counts: dict[str, int] = {}
        for candidate in ranked:
            family = str(candidate.get("family") or "")
            if family and family_counts.get(family, 0) >= 2:
                continue
            candidate["candidate_status"] = "selected"
            selected.append(candidate)
            if family:
                family_counts[family] = family_counts.get(family, 0) + 1
            if len(selected) >= cap:
                break
        if require_portfolio:
            self._system_inquiry_ensure_portfolio_selection(selected, ranked, cap)
        return selected

    @staticmethod
    def _system_inquiry_ensure_portfolio_selection(
        selected: list[dict[str, object]],
        ranked: list[dict[str, object]],
        cap: int,
    ) -> None:
        if any(str(item.get("family") or "") == "D_portfolio" for item in selected):
            return
        portfolio = next(
            (
                item
                for item in ranked
                if str(item.get("family") or "") == "D_portfolio"
                and item not in selected
            ),
            None,
        )
        if portfolio is None:
            return
        if len(selected) < cap:
            portfolio["candidate_status"] = "selected"
            selected.append(portfolio)
            return
        replace_index = next(
            (
                index
                for index in range(len(selected) - 1, -1, -1)
                if str(selected[index].get("family") or "") != "D_portfolio"
            ),
            None,
        )
        if replace_index is None:
            return
        selected[replace_index]["candidate_status"] = "held_cap"
        portfolio["candidate_status"] = "selected"
        selected[replace_index] = portfolio

    def _apply_system_inquiry_prior_dedupe_reuse(
        self,
        session: ChatSession,
        candidates: list[dict[str, object]],
        prior_measures: list[UserMitigationMeasure],
    ) -> None:
        prior_by_key = self._system_inquiry_prior_annotation_reuse_map(prior_measures)
        if not prior_by_key:
            return
        for candidate in candidates:
            key = self._system_inquiry_anchor_reuse_key(candidate)
            prior = prior_by_key.get(key)
            if not prior:
                continue
            prior_response = str(
                prior.get("followup_response") or prior.get("user_response") or ""
            ).strip()
            if not prior_response:
                continue
            candidate["dedupe_basis"] = "probe_id_anchor_set_prior_response"
            candidate["dedupe_prior_annotation_id"] = str(prior.get("annotation_id") or "")
            candidate["dedupe_prior_resolution_state"] = str(
                prior.get("resolution_state") or "open"
            )
            candidate["dedupe_prior_response_excerpt"] = self._truncate_system_inquiry_text(
                prior_response,
                220,
            )
            candidate["observation"] = self._system_inquiry_reuse_observation(
                candidate,
                prior_response,
            )
            candidate["question"] = self._system_inquiry_reuse_question(candidate)

    def _system_inquiry_prior_annotation_reuse_map(
        self,
        prior_measures: list[UserMitigationMeasure],
    ) -> dict[str, dict[str, object]]:
        reusable: dict[str, dict[str, object]] = {}
        for row in prior_measures:
            payload = self._system_inquiry_existing_payload(
                getattr(row, "system_inquiry_json", None),
            )
            annotations = payload.get("annotations")
            if not isinstance(annotations, list):
                continue
            for annotation in annotations:
                if not isinstance(annotation, dict):
                    continue
                if str(annotation.get("status") or "current") != "current":
                    continue
                key = self._system_inquiry_anchor_reuse_key(annotation)
                if key and key not in reusable:
                    reusable[key] = annotation
        return reusable

    @classmethod
    def _system_inquiry_candidate_dedupe_key(cls, candidate: dict[str, object]) -> str:
        probe_id = str(candidate.get("probe_id") or "").strip()
        anchors = candidate.get("anchors") if isinstance(candidate.get("anchors"), dict) else {}
        return json.dumps(
            {
                "probe_id": probe_id,
                "anchors": cls._system_inquiry_canonical_anchor_payload(anchors),
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )

    @classmethod
    def _system_inquiry_anchor_reuse_key(cls, item: dict[str, object]) -> str:
        probe_id = str(item.get("probe_id") or "").strip()
        anchors = item.get("anchors") if isinstance(item.get("anchors"), dict) else {}
        payload = cls._system_inquiry_canonical_anchor_payload(
            anchors,
            exclude_keys={
                "measure",
                "prior_measure",
                "interaction_summary",
                "prior_measure_count",
                "self_eval_score",
            },
        )
        return json.dumps(
            {"probe_id": probe_id, "anchors": payload},
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )

    @classmethod
    def _system_inquiry_canonical_anchor_payload(
        cls,
        anchors: dict[str, object],
        *,
        exclude_keys: set[str] | None = None,
    ) -> dict[str, object]:
        excluded = exclude_keys or set()
        payload: dict[str, object] = {}
        for key, value in anchors.items():
            key_text = str(key or "").strip()
            if not key_text or key_text in excluded:
                continue
            normalized = cls._system_inquiry_canonical_anchor_value(value)
            if normalized not in ("", [], {}):
                payload[key_text] = normalized
        return payload

    @classmethod
    def _system_inquiry_canonical_anchor_value(cls, value: object) -> object:
        if isinstance(value, list):
            return sorted(
                {
                    str(cls._system_inquiry_canonical_anchor_value(item))
                    for item in value
                    if str(cls._system_inquiry_canonical_anchor_value(item)).strip()
                }
            )
        if isinstance(value, dict):
            return {
                str(key): cls._system_inquiry_canonical_anchor_value(item)
                for key, item in sorted(value.items())
            }
        return normalize_for_match(str(value or ""))

    def _system_inquiry_reuse_observation(
        self,
        candidate: dict[str, object],
        prior_response: str,
    ) -> str:
        title = str(candidate.get("title") or "this system inquiry").strip()
        excerpt = self._truncate_system_inquiry_text(prior_response, 220)
        current = str(candidate.get("observation") or "").strip()
        return (
            f"This {title.casefold()} lens has already come up for the same "
            f"anchor context. Earlier you said: \"{excerpt}\" "
            f"For the current measure, the narrower issue is: {current}"
        )

    def _system_inquiry_reuse_question(self, candidate: dict[str, object]) -> str:
        anchors = candidate.get("anchors") if isinstance(candidate.get("anchors"), dict) else {}
        group = (
            str(anchors.get("shared_group") or "").strip()
            or self._first_system_inquiry_anchor_label(anchors, "groups")
            or self._first_system_inquiry_anchor_label(anchors, "omitted_groups")
            or "the same group"
        )
        measure = str(anchors.get("measure") or "this measure").strip()
        original = str(candidate.get("question") or "").strip()
        return (
            f"Does your earlier answer still apply for {group} in {measure}, "
            f"or is there a narrower exception here? {original}"
        )

    @staticmethod
    def _first_system_inquiry_anchor_label(anchors: dict[str, object], key: str) -> str:
        values = anchors.get(key)
        if isinstance(values, list):
            for value in values:
                cleaned = str(value or "").strip()
                if cleaned:
                    return cleaned
        return ""

    @staticmethod
    def _truncate_system_inquiry_text(value: str, limit: int) -> str:
        cleaned = " ".join(str(value or "").split())
        if len(cleaned) <= limit:
            return cleaned
        return cleaned[: max(0, limit - 3)].rstrip() + "..."

    @staticmethod
    def _system_inquiry_candidate_is_unstable(candidate: dict[str, object]) -> bool:
        if candidate.get("screen_result") is False:
            return True
        verify_votes = candidate.get("verify_votes")
        if verify_votes is None:
            return False
        try:
            return int(verify_votes) < 2
        except (TypeError, ValueError):
            return True

    @staticmethod
    def _system_inquiry_candidate_rank_key(
        candidate: dict[str, object],
    ) -> tuple[int, int, int, int]:
        anchor_counts = (
            candidate.get("anchor_counts")
            if isinstance(candidate.get("anchor_counts"), dict)
            else {}
        )
        group_count = int(anchor_counts.get("groups") or 0)
        corpus_label = str(candidate.get("corpus_label") or "unproven").casefold()
        evidence_rank = 0 if corpus_label == "evidenced" else 1
        family = str(candidate.get("family") or "")
        family_rank = 0 if family == "D_portfolio" else 1
        salience = int(candidate.get("salience") or 0)
        return (-group_count, evidence_rank, family_rank, -salience)

    def _system_inquiry_prior_surface_count(
        self,
        prior_measures: list[UserMitigationMeasure],
    ) -> int:
        count = 0
        for row in prior_measures:
            payload = self._system_inquiry_existing_payload(
                getattr(row, "system_inquiry_json", None),
            )
            annotations = payload.get("annotations")
            if isinstance(annotations, list):
                count += len([item for item in annotations if isinstance(item, dict)])
        return count

    def _system_inquiry_coverage_summary(self, session: ChatSession) -> dict[str, object]:
        uncovered: list[str] = []
        selected_hazard = str(session.selected_hazard or "").strip()
        if selected_hazard and not str(session.mitigation_measure or "").strip():
            uncovered.append(selected_hazard)

        affected = {
            normalize_for_match(item)
            for item in self._system_inquiry_affected_group_labels(session)
        }
        targeted = {
            normalize_for_match(item)
            for item in self._system_inquiry_group_labels(session)
        }
        untargeted = sorted(
            {
                item
                for item in self._system_inquiry_affected_group_labels(session)
                if normalize_for_match(item) and normalize_for_match(item) not in targeted
            }
        )
        return {
            "uncovered_hazards": uncovered,
            "untargeted_groups": untargeted,
            "affected_group_count": len(affected),
            "targeted_group_count": len(targeted),
        }

    @staticmethod
    def _format_system_inquiry_coverage_summary(summary: dict[str, object] | None) -> str:
        if not isinstance(summary, dict):
            return ""
        uncovered = [
            str(item).strip()
            for item in (summary.get("uncovered_hazards") or [])
            if str(item).strip()
        ]
        untargeted = [
            str(item).strip()
            for item in (summary.get("untargeted_groups") or [])
            if str(item).strip()
        ]
        lines: list[str] = []
        if uncovered:
            lines.append(
                "Coverage note: "
                "the selected hazard has "
                "no mitigation measure attached in this session: "
                + uncovered[0]
                + "."
            )
        if untargeted:
            lines.append(
                f"{len(untargeted)} affected group"
                f"{' is' if len(untargeted) == 1 else 's are'} "
                "not named in the mitigation target population: "
                + "; ".join(untargeted[:5])
                + "."
            )
        return "\n\n".join(lines)

    @staticmethod
    def _format_system_inquiry_boundary_note(
        held_observations: list[dict[str, object]] | None,
    ) -> str:
        held = [
            str(item.get("title") or item.get("probe_id") or "").strip()
            for item in (held_observations or [])
            if str(item.get("candidate_status") or "") == "held_cap"
            and str(item.get("title") or item.get("probe_id") or "").strip()
        ]
        if not held:
            return ""
        return (
            "Boundary note: other possible lenses were held back to keep this "
            "step focused: "
            + "; ".join(held[:5])
            + "."
        )

    def _system_inquiry_enriched_candidate(
        self,
        observation: dict[str, object],
        session: ChatSession,
    ) -> dict[str, object]:
        candidate = dict(observation)
        metadata = self._system_inquiry_probe_metadata(
            str(candidate.get("probe_id") or "")
        )
        candidate.update({key: value for key, value in metadata.items() if key not in candidate})
        candidate["candidate_id"] = self._system_inquiry_candidate_id(candidate)
        candidate["measure_id"] = str(session.mitigation_record_id or "current_measure")
        candidate["screen_result"] = True
        candidate["verify_votes"] = None
        candidate["citations"] = (
            list(candidate.get("citations"))
            if isinstance(candidate.get("citations"), list)
            else []
        )
        candidate["salience_score"] = round(float(candidate.get("salience") or 0) / 100, 3)
        candidate["anchor_counts"] = self._system_inquiry_anchor_counts(candidate)
        candidate["candidate_status"] = (
            "selected"
            if self._system_inquiry_candidate_has_required_anchors(candidate)
            else "discarded_no_anchor"
        )
        return candidate

    @staticmethod
    def _system_inquiry_candidate_id(candidate: dict[str, object]) -> str:
        probe_id = str(candidate.get("probe_id") or "probe")
        anchors = candidate.get("anchors") if isinstance(candidate.get("anchors"), dict) else {}
        payload = json.dumps(
            {"probe_id": probe_id, "anchors": anchors},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode("utf-8")
        return f"{probe_id}-{hashlib.sha256(payload).hexdigest()[:10]}"

    @classmethod
    def _system_inquiry_anchor_counts(cls, candidate: dict[str, object]) -> dict[str, int]:
        anchors = candidate.get("anchors") if isinstance(candidate.get("anchors"), dict) else {}
        measures = 0
        if cls._system_inquiry_real_anchor_value(
            anchors.get("measure"),
            {"the mitigation measure"},
        ):
            measures += 1
        if cls._system_inquiry_real_anchor_value(
            anchors.get("prior_measure"),
            {"the earlier measure"},
        ):
            measures += 1
        if int(anchors.get("prior_measure_count") or 0) > 0:
            measures += int(anchors.get("prior_measure_count") or 0)
        hazards = (
            1
            if cls._system_inquiry_real_anchor_value(
                anchors.get("hazard"),
                {"the selected hazard"},
            )
            else 0
        )
        groups = 0
        if isinstance(anchors.get("groups"), list):
            groups += len([item for item in anchors["groups"] if str(item).strip()])
        if isinstance(anchors.get("omitted_groups"), list):
            groups += len(
                [item for item in anchors["omitted_groups"] if str(item).strip()]
            )
        if str(anchors.get("shared_group") or "").strip():
            groups += 1
        sectors = 0
        if isinstance(anchors.get("sectors"), list):
            sectors = len([item for item in anchors["sectors"] if str(item).strip()])
        predictors = 0
        if isinstance(anchors.get("predictors"), list):
            predictors = len([item for item in anchors["predictors"] if str(item).strip()])
        return {
            "measures": measures,
            "hazards": hazards,
            "groups": groups,
            "sectors": sectors,
            "predictors": predictors,
        }

    @staticmethod
    def _system_inquiry_real_anchor_value(
        value: object,
        placeholders: set[str],
    ) -> bool:
        cleaned = str(value or "").strip()
        return bool(cleaned and normalize_for_match(cleaned) not in placeholders)

    @staticmethod
    def _system_inquiry_candidate_has_required_anchors(
        candidate: dict[str, object],
    ) -> bool:
        required = (
            candidate.get("required_anchors")
            if isinstance(candidate.get("required_anchors"), dict)
            else {}
        )
        counts = (
            candidate.get("anchor_counts")
            if isinstance(candidate.get("anchor_counts"), dict)
            else {}
        )
        for key, required_count in required.items():
            try:
                needed = int(required_count)
            except (TypeError, ValueError):
                continue
            try:
                present = int(counts.get(key) or 0)
            except (TypeError, ValueError):
                present = 0
            if present < needed:
                return False
        return True

    @staticmethod
    def _system_inquiry_probe_metadata(probe_id: str) -> dict[str, object]:
        asset_record = system_inquiry_probe_record(probe_id)
        if asset_record is not None:
            enriched = dict(asset_record)
            enriched.setdefault("tier", "core")
            enriched.setdefault("trigger_basis", "deterministic trigger")
            enriched.setdefault("required_anchors", {"measures": 1})
            enriched.setdefault("source_refs", [])
            enriched["library_version"] = system_inquiry_library_version()
            return enriched
        records: dict[str, dict[str, object]] = {
            "A2-P1": {
                "tier": "conditional",
                "trigger_basis": "measure names or implies a second sector",
                "required_anchors": {"measures": 1, "hazards": 1, "sectors": 1},
                "source_refs": [
                    {
                        "tier": "T3",
                        "document": "System enquiry.md",
                        "locator": "§5.3 Lens catalogue / A2 Cross-sector coupling",
                    }
                ],
            },
            "A1-P1": {
                "tier": "core",
                "trigger_basis": "always",
                "required_anchors": {"measures": 1, "hazards": 1, "groups": 1},
                "source_refs": [
                    {
                        "tier": "T3",
                        "document": "System enquiry.md",
                        "locator": "§5.3 Lens catalogue / A1 Boundary of the measure",
                    }
                ],
            },
            "A3-P1": {
                "tier": "conditional",
                "trigger_basis": "measure suggests uptake, demand, participation, prices, or response effects",
                "required_anchors": {"measures": 1, "hazards": 1, "groups": 1},
                "source_refs": [
                    {
                        "tier": "T3",
                        "document": "System enquiry.md",
                        "locator": "§5.3 Lens catalogue / A3 Feedback loops",
                    }
                ],
            },
            "A4-P1": {
                "tier": "core",
                "trigger_basis": "time_to_benefit in months or years",
                "required_anchors": {"measures": 1, "hazards": 1, "groups": 1},
                "source_refs": [
                    {
                        "tier": "T3",
                        "document": "System enquiry.md",
                        "locator": "§5.3 A4-P1 — DELAY-INCIDENCE",
                    }
                ],
            },
            "B2-P1": {
                "tier": "core",
                "trigger_basis": "any validation dimension verdict is insufficient",
                "required_anchors": {"measures": 1, "hazards": 1},
                "source_refs": [
                    {
                        "tier": "T3",
                        "document": "System enquiry.md",
                        "locator": "§5.3 Lens catalogue / B2 Untested assumptions",
                    }
                ],
            },
            "B3-P1": {
                "tier": "conditional",
                "trigger_basis": "measure defines eligibility or success criteria",
                "required_anchors": {"measures": 1, "hazards": 1, "groups": 1},
                "source_refs": [
                    {
                        "tier": "T3",
                        "document": "System enquiry.md",
                        "locator": "§5.3 Lens catalogue / B3 Worldview plurality and expertise",
                    }
                ],
            },
            "B4-P1": {
                "tier": "conditional",
                "trigger_basis": "measure delegates delivery to an intermediary",
                "required_anchors": {"measures": 1, "hazards": 1, "groups": 1},
                "source_refs": [
                    {
                        "tier": "T3",
                        "document": "System enquiry.md",
                        "locator": "§5.3 Lens catalogue / B4 Boundary judgements of power and legitimacy",
                    }
                ],
            },
            "A5-P1": {
                "tier": "core",
                "trigger_basis": "systemic self-evaluation >= 7 and leverage_depth parameter",
                "required_anchors": {"measures": 1},
                "source_refs": [
                    {
                        "tier": "T3",
                        "document": "System enquiry.md",
                        "locator": "§5.3 A5-P1 — DEPTH-SELFEVAL-MISMATCH",
                    }
                ],
            },
            "A6-P1": {
                "tier": "conditional",
                "trigger_basis": "action_type is subsidy, grant, tariff, or tax",
                "required_anchors": {"measures": 1, "hazards": 1, "groups": 1},
                "source_refs": [
                    {
                        "tier": "T3",
                        "document": "System enquiry.md",
                        "locator": "§5.3 Lens catalogue / A6 Policy resistance and rebound",
                    }
                ],
            },
            "A7-P1": {
                "tier": "conditional",
                "trigger_basis": "measure requires delivery capacity or stock",
                "required_anchors": {"measures": 1, "hazards": 1, "groups": 1},
                "source_refs": [
                    {
                        "tier": "T3",
                        "document": "System enquiry.md",
                        "locator": "§5.3 Lens catalogue / A7 Capacity and stock constraints",
                    }
                ],
            },
            "C1-P1": {
                "tier": "core",
                "trigger_basis": "cost_incidence is upfront or ongoing user cost",
                "required_anchors": {"measures": 1, "hazards": 1, "groups": 1},
                "source_refs": [
                    {
                        "tier": "T3",
                        "document": "System enquiry.md",
                        "locator": "§5.3 C1-P1 — UPFRONT-COST-INCIDENCE",
                    }
                ],
            },
            "C2-P1": {
                "tier": "core",
                "trigger_basis": "affected group omitted from target population",
                "required_anchors": {"measures": 1, "hazards": 1, "groups": 1},
                "source_refs": [
                    {
                        "tier": "T3",
                        "document": "System enquiry.md",
                        "locator": "§5.3 C2-P1 — PREDICTOR-UNNAMED",
                    }
                ],
            },
            "C3-P1": {
                "tier": "core",
                "trigger_basis": "delivery_channel is application, means-tested, or intermediary",
                "required_anchors": {"measures": 1, "groups": 1},
                "source_refs": [
                    {
                        "tier": "T3",
                        "document": "System enquiry.md",
                        "locator": "§5.3 C3-P1 — APPLICATION-BARRIER",
                    }
                ],
            },
            "C4-P1": {
                "tier": "conditional",
                "trigger_basis": "time_to_benefit is years or measure creates a long-lived asset",
                "required_anchors": {"measures": 1, "hazards": 1, "groups": 1},
                "source_refs": [
                    {
                        "tier": "T3",
                        "document": "System enquiry.md",
                        "locator": "§5.3 Lens catalogue / C4 Intergenerational and ecological burden",
                    }
                ],
            },
            "D1-P1": {
                "tier": "conditional",
                "trigger_basis": "current and prior measures have a deterministic policy tension",
                "required_anchors": {"measures": 2},
                "source_refs": [
                    {
                        "tier": "T3",
                        "document": "System enquiry.md",
                        "locator": "§5.3 D1-P1 — ASSUMPTION-UNDERMINE",
                    }
                ],
            },
            "D2-P1": {
                "tier": "conditional",
                "trigger_basis": "current and prior measure share a target group",
                "required_anchors": {"measures": 2, "groups": 1},
                "source_refs": [
                    {
                        "tier": "T3",
                        "document": "System enquiry.md",
                        "locator": "§5.3 D2-P1 — SAME-GROUP-COMPOUND",
                    }
                ],
            },
            "D3-P1": {
                "tier": "conditional",
                "trigger_basis": "at least three parameter-level measures in the session",
                "required_anchors": {"measures": 3},
                "source_refs": [
                    {
                        "tier": "T3",
                        "document": "System enquiry.md",
                        "locator": "§5.3 D3-P1 — DEPTH-CONCENTRATION",
                    }
                ],
            },
            "B1-P1": {
                "tier": "core",
                "trigger_basis": "fallback boundary critique",
                "required_anchors": {"measures": 1, "hazards": 1},
                "source_refs": [
                    {
                        "tier": "T3",
                        "document": "System enquiry.md",
                        "locator": "§5.3 Lens catalogue / B framing fallback",
                    }
                ],
            },
        }
        record = records.get(probe_id, {})
        enriched = dict(record)
        enriched.setdefault("tier", "core")
        enriched.setdefault("trigger_basis", "deterministic trigger")
        enriched.setdefault("required_anchors", {"measures": 1})
        enriched["library_version"] = system_inquiry_library_version()
        enriched.setdefault(
            "source_refs",
            [
                {
                    "tier": "T3",
                    "document": "System enquiry.md",
                    "locator": "§4.4",
                }
            ],
        )
        return enriched

    def _system_inquiry_group_labels(self, session: ChatSession) -> list[str]:
        values = [
            str(item or "").strip()
            for item in (session.mitigation_target_population or [])
            if str(item or "").strip()
        ]
        return self._dedupe_system_inquiry_labels(values)

    def _system_inquiry_omitted_affected_groups(self, session: ChatSession) -> list[str]:
        targeted = {
            normalize_for_match(item)
            for item in self._system_inquiry_group_labels(session)
        }
        return [
            item
            for item in self._system_inquiry_affected_group_labels(session)
            if normalize_for_match(item) and normalize_for_match(item) not in targeted
        ]

    def _system_inquiry_financial_strain_evidence(
        self,
        session: ChatSession,
        groups: list[str],
    ) -> dict[str, object]:
        group_keys = {
            normalize_for_match(item)
            for item in groups
            if normalize_for_match(item)
        }
        matches = [
            detail
            for detail in self._system_inquiry_affected_profile_details(session)
            if normalize_for_match(str(detail.get("name") or "")) in group_keys
            and self._system_inquiry_is_financial_strain_profile(detail)
        ]
        return self._system_inquiry_profile_evidence_payload(matches)

    def _system_inquiry_affected_profile_evidence(
        self,
        session: ChatSession,
        labels: list[str],
    ) -> dict[str, object]:
        label_keys = {
            normalize_for_match(item)
            for item in labels
            if normalize_for_match(item)
        }
        matches = [
            detail
            for detail in self._system_inquiry_affected_profile_details(session)
            if normalize_for_match(str(detail.get("name") or "")) in label_keys
        ]
        return self._system_inquiry_profile_evidence_payload(matches)

    @staticmethod
    def _system_inquiry_profile_evidence_payload(
        details: list[dict[str, object]],
    ) -> dict[str, object]:
        citations: list[dict[str, str]] = []
        predictors: list[str] = []
        for detail in details:
            name = str(detail.get("name") or "").strip()
            variable = str(detail.get("variable_name") or "").strip()
            if not name:
                continue
            predictor = variable or name
            predictors.append(predictor)
            citations.append(
                {
                    "citation_id": f"session_affected_profile:{normalize_for_match(predictor)}",
                    "source": "session_affected_population_profile",
                    "label": name,
                    "variable_name": variable,
                }
            )
        return {
            "corpus_label": "evidenced" if citations else "unproven",
            "citations": citations,
            "predictors": predictors,
        }

    @staticmethod
    def _system_inquiry_is_financial_strain_profile(
        detail: dict[str, object],
    ) -> bool:
        text = normalize_for_match(
            " ".join(
                [
                    str(detail.get("name") or ""),
                    str(detail.get("variable_name") or ""),
                ]
            )
        )
        return any(
            marker in text
            for marker in (
                "arrears",
                "bill",
                "bills",
                "debt",
                "energy poverty",
                "financial",
                "fuel poverty",
                "income",
                "low income",
                "low-income",
                "poverty",
                "struggling",
                "unemployed",
                "utility",
            )
        )

    @staticmethod
    def _system_inquiry_affected_profile_details(
        session: ChatSession,
    ) -> list[dict[str, object]]:
        return [
            dict(item)
            for item in session._affected_profile_details()
            if isinstance(item, dict)
        ]

    def _system_inquiry_affected_group_labels(self, session: ChatSession) -> list[str]:
        values: list[str] = []
        for item in self._system_inquiry_affected_profile_details(session):
            label = str(item.get("name") or "").strip()
            if label:
                values.append(label)
        return self._dedupe_system_inquiry_labels(values)

    @staticmethod
    def _dedupe_system_inquiry_labels(values: list[str]) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for value in values:
            key = normalize_for_match(value)
            if value and key not in seen:
                seen.add(key)
                deduped.append(value)
        return deduped

    def _system_inquiry_prior_measure_rows(
        self,
        session: ChatSession,
    ) -> list[UserMitigationMeasure]:
        db = getattr(self, "db", None)
        mitigation_record_id = str(session.mitigation_record_id or "").strip()
        if db is None or not mitigation_record_id:
            return []
        try:
            current = db.scalar(
                select(UserMitigationMeasure).where(
                    UserMitigationMeasure.id == mitigation_record_id
                )
            )
            user_session_id = getattr(current, "user_session_id", None)
            if current is None or not user_session_id:
                return []
            return list(
                db.scalars(
                    select(UserMitigationMeasure)
                    .where(
                        UserMitigationMeasure.user_session_id == user_session_id,
                        UserMitigationMeasure.id != mitigation_record_id,
                    )
                    .order_by(UserMitigationMeasure.created_at, UserMitigationMeasure.id)
                )
            )
        except Exception:
            logger.exception("Failed to load prior mitigation measures for system inquiry")
            return []

    def _system_inquiry_shared_group_prior_measure(
        self,
        session: ChatSession,
        prior_measures: list[UserMitigationMeasure],
    ) -> dict[str, str] | None:
        current_groups = {
            normalize_for_match(item): item
            for item in self._system_inquiry_group_labels(session)
            if normalize_for_match(item)
        }
        if not current_groups:
            return None
        for row in prior_measures:
            for group in self._system_inquiry_target_population_from_json(
                row.target_population,
            ):
                key = normalize_for_match(group)
                if key in current_groups:
                    return {
                        "measure": str(row.measure or "").strip(),
                        "shared_group": current_groups[key],
                    }
        return None

    def _system_inquiry_interacting_prior_measure(
        self,
        session: ChatSession,
        prior_measures: list[UserMitigationMeasure],
        current_attributes: dict[str, object],
    ) -> dict[str, str] | None:
        current_measure = str(session.mitigation_measure or "").strip()
        if not current_measure:
            return None
        current_action = str(current_attributes.get("action_type") or "other")
        current_delivery = str(current_attributes.get("delivery_channel") or "unknown")
        current_cost = str(current_attributes.get("cost_incidence") or "unknown")
        for row in prior_measures:
            prior_measure = str(getattr(row, "measure", "") or "").strip()
            if not prior_measure:
                continue
            prior_attributes = self._system_inquiry_measure_attributes(
                getattr(row, "measure", ""),
                getattr(row, "reason", ""),
                self._system_inquiry_target_population_from_json(
                    getattr(row, "target_population", None),
                ),
            )
            prior_action = str(prior_attributes.get("action_type") or "other")
            prior_delivery = str(prior_attributes.get("delivery_channel") or "unknown")
            prior_cost = str(prior_attributes.get("cost_incidence") or "unknown")

            if current_action in {"mandate", "regulation", "tax"} and prior_action in {
                "grant",
                "subsidy",
                "service",
            }:
                return {
                    "measure": prior_measure,
                    "summary": (
                        f"{current_measure} increases obligations while {prior_measure} "
                        "is designed to help the same users meet or absorb them."
                    ),
                }
            if prior_action in {"mandate", "regulation", "tax"} and current_action in {
                "grant",
                "subsidy",
                "service",
            }:
                return {
                    "measure": prior_measure,
                    "summary": (
                        f"{prior_measure} increases obligations while {current_measure} "
                        "is designed to help the same users meet or absorb them."
                    ),
                }
            if (
                current_delivery in {"application", "means_tested"}
                and prior_delivery in {"application", "means_tested"}
            ):
                return {
                    "measure": prior_measure,
                    "summary": (
                        f"{current_measure} and {prior_measure} both rely on user-initiated "
                        "administrative processes."
                    ),
                }
            if current_cost != "unknown" and current_cost == prior_cost:
                return {
                    "measure": prior_measure,
                    "summary": (
                        f"{current_measure} and {prior_measure} place a similar cost pathway "
                        "on users."
                    ),
                }
        return None

    @staticmethod
    def _system_inquiry_target_population_from_json(value: str | None) -> list[str]:
        try:
            decoded = json.loads(value or "[]")
        except (TypeError, ValueError):
            return []
        if not isinstance(decoded, list):
            return []
        return [str(item or "").strip() for item in decoded if str(item or "").strip()]

    def _system_inquiry_prior_parameter_share(
        self,
        prior_measures: list[UserMitigationMeasure],
    ) -> float:
        if not prior_measures:
            return 0.0
        parameter_count = 0
        for row in prior_measures:
            attributes = self._system_inquiry_measure_attributes(
                row.measure,
                row.reason,
                self._system_inquiry_target_population_from_json(row.target_population),
            )
            if attributes["leverage_depth"] == "parameter":
                parameter_count += 1
        return parameter_count / len(prior_measures)

    def _system_inquiry_leverage_distribution(
        self,
        session: ChatSession,
    ) -> dict[str, int]:
        distribution = {"parameter": 0, "rules": 0, "goals": 0, "paradigm": 0}
        current_attributes = session.system_inquiry_attributes or (
            self._system_inquiry_measure_attributes(
                session.mitigation_measure,
                session.mitigation_reason,
                session.mitigation_target_population,
            )
        )
        current_depth = str(current_attributes.get("leverage_depth") or "parameter")
        if current_depth in distribution:
            distribution[current_depth] += 1
        for row in self._system_inquiry_prior_measure_rows(session):
            attributes = self._system_inquiry_measure_attributes(
                row.measure,
                row.reason,
                self._system_inquiry_target_population_from_json(row.target_population),
            )
            depth = str(attributes.get("leverage_depth") or "parameter")
            if depth in distribution:
                distribution[depth] += 1
        return distribution

    def _system_inquiry_trajectory(
        self,
        session: ChatSession,
        current_per_family: dict[str, dict[str, float | int]],
    ) -> list[dict[str, object]]:
        trajectory: list[dict[str, object]] = []
        for index, row in enumerate(self._system_inquiry_prior_measure_rows(session), start=1):
            payload = self._system_inquiry_existing_payload(
                getattr(row, "system_inquiry_json", None),
            )
            profile = payload.get("profile") if isinstance(payload.get("profile"), dict) else {}
            coverage = self._system_inquiry_profile_coverage(profile)
            if coverage:
                trajectory.append(
                    {
                        "ordinal": index,
                        "coverage": coverage,
                        "measure_id": str(getattr(row, "id", "") or ""),
                    }
                )
        trajectory.append(
            {
                "ordinal": len(trajectory) + 1,
                "coverage": self._system_inquiry_family_coverage(current_per_family),
                "measure_id": str(session.mitigation_record_id or "current_measure"),
            }
        )
        return trajectory

    @staticmethod
    def _system_inquiry_profile_coverage(profile: dict[str, object]) -> dict[str, float]:
        per_family = profile.get("per_family")
        if not isinstance(per_family, dict):
            return {}
        coverage: dict[str, float] = {}
        for family, data in per_family.items():
            if not isinstance(data, dict):
                continue
            try:
                coverage[str(family)] = round(float(data.get("coverage") or 0.0), 3)
            except (TypeError, ValueError):
                coverage[str(family)] = 0.0
        return coverage

    @staticmethod
    def _system_inquiry_family_coverage(
        per_family: dict[str, dict[str, float | int]],
    ) -> dict[str, float]:
        coverage: dict[str, float] = {}
        for family, data in per_family.items():
            try:
                coverage[family] = round(float(data.get("coverage") or 0.0), 3)
            except (TypeError, ValueError):
                coverage[family] = 0.0
        return coverage
