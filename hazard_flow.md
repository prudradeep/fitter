# Add a new hazard flow

This describes the custom-hazard chat flow after a country, region, sector, and
policy context have been selected. `custom_hazard_state_machine.py` dispatches
the phases; the `chat_custom_hazard_*` services implement them.

## 1. Title and duplicate check

```text
Hazards -> Add a new Hazard -> custom_hazard_input
  -> meaningful-text review of the proposed title and short description
  -> title clarification or rewrite, if needed
  -> concise hazard extraction
  -> exact, local-similarity, and semantic duplicate checks
```

The full submission remains `raw_text`; the extracted hazard is
`resolved_hazard_text`. A possible duplicate offers **Continue with custom
hazard**, **Use existing hazard**, and **Edit custom hazard**. Continuing records
an override. Custom hazards are compared within the selected country, sector,
and region. System hazards available to the sector and additional hazards
available to the country and sector are also compared because those catalogs
are shared across regions. Returning to the hazards list clears the pending draft.

## 2. Objective and context checks

The first grounding pass checks **Sectoral Objective Fit** against the
predefined transition objective of the selected sector. The next checks
**Policy Objective Fit** against the selected policy and its confirmed summary,
including its objectives and target-actor mechanisms. Each is a separate scored
dimension. A substantive mismatch asks a focused question and reruns the
relevant check with the answer. Each supported result is confirmed by the user
before moving to the next stage. The policy stage also checks stated country
and region claims; these findings appear in `context_fit`. Omitting place or
sector names from the hazard title is not a mismatch.

## 3. Mechanism and causal reflection

After objective fit, the app suggests a mechanism. It previews a policy or
knowledge source only if its text supports that particular mechanism and
policy-to-hazard linkage. The proposed mechanism displays **Documented** with
the source and supporting detail, or **Inferred** if support has not been found.
Mechanisms are shown as complete sentences. The policy summary displays its
mechanism bullets with the same provenance labels; an unfinished bullet is
completed from source text when possible, otherwise a completed inference is
labelled **Inferred**.
The user can confirm the suggestion or provide a mechanism.

A user-provided mechanism is checked for clarity. The app then asks whether the
user has evidence for it. This source is optional. If supplied, the mechanism
linkage is checked against the readable content of that source alone; knowledge
base text cannot substitute for it. Unsupported evidence offers clarification,
replacement evidence, or mechanism revision. If no user source is supplied,
the existing policy and knowledge-source path applies. A policy reference may
be supplied or skipped. Skipping all sources can still produce a plausible
**Inferred** linkage for the user to confirm; the app must not label that
linkage documented. Policy-reference text is not treated as evidence that the
hazard occurred or affects a population.

Once a linkage is supported, the app provides a contextual AI reflection about
the selected policy, mechanism, hazard, country, region, and sector. The user
confirms the linkage or revises the mechanism. Source and **Documented/Inferred**
status remain visible on this screen. Confirmation moves to affected-group
grounding and review.

## 4. Affected groups

Grounding then checks hazard definition and extracts candidate affected groups.
Missing grounding or no specific group prompts a targeted clarification. The
group review supports additions, removals, and reason edits. Each added or
edited group needs a meaningful explanation of its concrete disadvantage.

The app checks a group's impact against user-supplied evidence, when present,
and relevant main and validated evidence. If the user's evidence does not
support a proposed group impact, the group cannot be confirmed until the
explanation is revised. A group backed by a specific source finding is labelled
**Documented** with that source; otherwise it is **Inferred** and attributed to
the hazard description or user explanation. At least one usable group must be
confirmed.

## 5. Final review and persistence

```text
Confirm affected groups
  -> generate title and summary
  -> Confirm and save / Revise title / Revise summary / Back to affected groups
  -> save only after Confirm and save
```

The draft stays in session state through both reviews. On confirmation the app
ensures a shared custom-hazard record for the selected country, sector, and
region; stores its reason, evidence, summary, and affected-group
profiles; promotes eligible temporary policy references and evidence; adds the
hazard to the session list; and records `custom_hazard_added` activity. The
completion response displays the saved hazard and next actions.

The weighted grounding dimensions are sectoral-objective fit (10%),
policy-objective fit (15%), mechanism fit (30%), hazard definition (30%), and
affected groups (15%). Country and region findings are contextual checks that
can block objective fit. Strict mode requires an overall score of 75
and a 7/10 dimension floor; easy mode requires 45 and 3/10.
