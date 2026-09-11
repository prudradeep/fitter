# Exact custom-hazard flow

This is the implementation flow for adding a custom hazard after the user has
selected a country, region, and sector. The flow is implemented across
`ChatHazardCreationMixin` and `ChatCustomHazardPopulationStepsMixin`.

## Entry point

```text
Country -> Region -> Sector -> Add a new Hazard -> custom_hazard_input
```

The hazard-listing step accepts supported natural-language equivalents of “add
a new hazard”. A question about what the option means is answered in place and
does not start creation. `Go back to list of hazards` clears pending custom
state and returns to `hazards`.

## Main state machine

```text
custom_hazard_input
  -> text-quality gate
  -> mandatory LLM ambiguity review and hazard extraction
  -> custom_hazard_title_clarification     (underspecified title only)
  -> duplicate check
  -> custom_hazard_dimension_check
       -> custom_hazard_clarification       (missing core grounding)
       -> custom_hazard_evidence_decision   (reason/evidence still needed)
       -> custom_hazard_group_review        (grounded and groups available)
       -> finalize custom hazard             (next_action = validate)
       -> custom_hazard_clarification       (rejected or not yet ready)
```

A valid title enters the dimension check immediately; it does not always go
first to a separate reason screen. The dimension-check router decides whether
the next missing information is grounding, evidence, affected groups, or
nothing.

## 1. Capture and screen the title

`_capture_custom_hazard(session_id, session, message)` initializes
`session.custom_hazard` with `default_custom_hazard_state()` for a non-empty
title. The state keeps the complete user input in `raw_text` and the concise
LLM-extracted hazard in `resolved_hazard_text`, along with selected scope, validation
rounds, scores, clarifications, affected groups, duplicate candidates, status,
and duplicate-override state.

The title gates run in this order:

1. Basic text-quality checks reject empty or meaningless input.
2. `_review_custom_hazard_input(..., use_llm_for_title=True)` always checks for
   ambiguous or overly general wording and returns `valid`, `invalid`, or
   `needs_clarification`.
3. For valid context, the same review extracts one concise hazard containing the
   concrete harm/risk, affected subject when stated, and causal condition when
   stated. It excludes background narrative, policy commentary, evidence
   discussion, and recommendations.
4. Duplicate detection and every subsequent dimension use
   `resolved_hazard_text`; `raw_text` remains available as internal context.

An unavailable title-review model returns an error at the entry step. A rejected
title returns a rewrite-required response and is not saved.

## 2. Title clarification

For `needs_clarification`:

```text
session.phase = custom_hazard_title_clarification
step = custom_hazard_title_clarification
input_mode = textarea
```

`_handle_custom_hazard_title_clarification(...)` appends the answer to the
history and re-runs title review with the original title, scope, questions, and
answers. Blank, very short, ambiguous, or question/request-style replies are
re-asked. Up to three rounds are allowed. A valid result stores the resolved
title and continues to duplicate checking; an invalid result is rejected.

## 3. Duplicate checking

`_continue_valid_custom_hazard(...)` checks exact/known matches, local
same-sector similarity, and then semantic similarity. Possible duplicates enter:

```text
session.phase = custom_hazard_duplicate_confirmation
step = custom_hazard_duplicate_confirmation
options = Continue with custom hazard / Use existing hazard / Edit custom hazard
```

`Continue with custom hazard` records `duplicate_override_confirmed` and starts
grounding. `Use existing hazard` selects the suggested hazard and returns to the
normal hazard/profile flow. `Edit custom hazard` clears the state and returns to
title entry. The override survives later validation, but is reset if the title
changes.

## Free-text ambiguity rule

Every free-text contribution in the custom-hazard flow is checked before it is
accepted. This includes hazard and clarification text, reasons, evidence
explanations, user-provided mechanisms and linkage revisions, affected-group
edits and reasons, and summary revision instructions. Ambiguous or overly general
wording repeats the current step with a targeted request for the missing detail.
Confirmation buttons and other structured selections bypass this prose check.
Evidence URLs, uploaded files, and stored document identifiers are validated from
their extracted source content; accompanying user-written explanations are still
checked as prose.

## 4. Dimension grounding

`_start_custom_hazard_grounding_check(...)` sets:

```text
session.phase = custom_hazard_dimension_check
```

`_run_custom_hazard_dimension_check(...)` calls
`validate_custom_hazard_dimensions(...)` with the hazard, staged reason/evidence,
selected sector, country, region, known hazards, prior state, and validation
mode (`strict` or `easy`). Results store scores, status, next action, groups,
confirmed groups, and duplicate candidates in `custom_hazard`.

The dimensions are:

```text
Hazard definition fit
Mechanism Fit
Policy Objective Fit
Selected sector fit
Country / region fit
Affected population groups fit
```

Strict mode requires overall score 75 and dimension floor 7. Easy mode requires
45 and 3. Critical dimensions are hazard definition, mechanism fit,
policy-objective fit, selected-sector fit, and country/region fit. Policy
Objective Fit checks the hazard against the selected sector's defined objective:
renewable-energy transition for Energy, climate adaptation for Housing, and a
shift to sustainable mobility for Transport.

The validator routes to:

```text
ask_clarification | ask_duplicate_confirmation | review_groups | validate | reject
```

Only the first one or two unresolved grounding questions are shown. Answers in
`custom_hazard_clarification` are appended to `clarifications`, then the
dimension check runs again. Repeated clarification questions return an error
instead of silently advancing.

## 5. Evidence and mechanism confirmation

After objective fit is supported, the tool first searches both the core knowledge
base (`main`) and validated secondary evidence (`validated_evidence`). If relevant
evidence exists, the AI presents a grounded reflection and evidence-to-hazard
relationship for user confirmation. Agreement continues to Mechanism Fit. A user
who disagrees supplies an alternative reflection, which is validated against the
same evidence. Supported reflections are acknowledged and accepted; unsupported
reflections require user evidence.

If no relevant knowledge-base evidence exists, the tool asks whether the user has
evidence. Supplied evidence is checked for material relevance to both the hazard
and any user reflection. Irrelevant or unclear evidence explains the mismatch and
offers **Clarify the relevance** and **Provide evidence again**. Before fetching an
evidence URL, the knowledge base checks for an accessible document with the same
normalized URL. Existing chunks are reused without downloading, parsing, chunking,
or embedding the source again.

```text
custom_hazard_evidence_reflection_confirmation (when KB evidence exists)
  -> custom_hazard_evidence_reflection_input    (when the user disagrees)
  -> custom_hazard_mechanism_confirmation

add_hazard_evidence_decision                    (when KB evidence does not exist)
  -> add_hazard_evidence_input (when evidence is available)
  -> custom_hazard_mechanism_confirmation
```

The LLM suggests one or more causal mechanisms and asks for confirmation. A
confirmed suggestion is checked against the core and validated-secondary knowledge
bases specifically for supporting policy details. When a policy is found, the tool
shows its relevant details, source, and summary using the introduction **As I
understand it, this is the policy supporting the suggested mechanism**, then asks
the user to confirm the policy before presenting the causal linkage.

If policy details are not found, or the user rejects the KB policy, the tool asks
for a policy URL or file supporting the confirmed mechanism. Supplied policy text
is validated against both the mechanism and hazard. A relevant policy is
acknowledged and summarized before the causal linkage is shown. An unclear or
irrelevant policy explains the mismatch and offers **Clarify the relevance**,
**Provide policy again**, and **Revise mechanism**. A relevance clarification is
checked against the retained policy text and cannot introduce facts absent from
the document.

If the suggested mechanism is rejected, or no sufficiently specific mechanism can
be suggested, the tool asks for the user's mechanism and validates it for clarity
and specificity.

The supported chain is displayed as `source finding or policy provision ->
mechanism -> hazard impact`. The user must confirm that linkage before the
existing affected population group stage begins. A rejected linkage returns to
mechanism input and is checked against the available KB or policy text.

```text
custom_hazard_mechanism_confirmation
  -> custom_hazard_policy_details_confirmation (KB policy found)
  -> custom_hazard_policy_reference             (KB policy missing/rejected)
  -> custom_hazard_mechanism_input               (mechanism rejected)

custom_hazard_policy_reference
  -> custom_hazard_policy_relevance_clarification (unclear relevance)
  -> custom_hazard_causal_linkage_confirmation    (policy accepted)
```

Evidence decision:

```text
session.phase = add_hazard_evidence_decision
step = custom_hazard_evidence_decision
options = Yes / No
```

Open-chat decisions are supported, including `I have evidence`, `no I don't
have`, `no, I don't know`, `continue without evidence`, and a message containing
a URL such as `Use this evidence https://example.org/report.pdf`.

`No` validates without evidence. `Yes` enters:

```text
session.phase = add_hazard_evidence_input
step = custom_hazard_evidence
input_mode = evidence_only
options = Go back to list of hazards / Skip
```

The input accepts a URL or PDF, DOCX, MD, or TXT file. URLs are ingested into
temporary knowledge-base scope and their temporary document ID is added to the
staged evidence. `Skip` validates without evidence. Accepted evidence is
promoted to validated evidence only after save.

`_validate_staged_custom_hazard(...)` writes the staged reason/evidence to
custom state and runs the dimension check again.

## 6. Affected groups and review

Generic groups such as `people`, `households`, `residents`, `consumers`, or
`general population` trigger a clarification asking for a specific targetable
group. The flow cannot confirm a generic label.

Once groups are usable:

```text
session.phase = custom_hazard_group_review
step = custom_hazard_group_review
```

The review is labelled **Hazard to be co-created** and supports confirm/continue,
add, remove, and edit-reason actions. Every added group enters
`custom_hazard_profile_reason` and must receive a validated reason. Confirming
with no groups is an error. Confirmation marks groups as
`confirmed_affected_groups`, marks the state ready, and routes to finalization.

Strict validation with Crowd Sourcing enabled adds the platform-visibility
notice to this review.

## 7. Save and complete

`_finalize_custom_hazard_from_grounding(...)` is the normal custom-state save
path. It adds the resolved hazard to `session.custom_hazards`, stores the
accepted hazard/reason/evidence and IDs, calls `_ensure_custom_hazard(...)`,
records `custom_hazard_added`, promotes accepted temporary evidence, and stores
affected-population profiles.

If no usable profiles were extracted, the configured
`target_population_question` flow can run first. After profiles exist, the
group review is shown; confirmation calls `_custom_hazard_added_step(...)`.

The final response has:

```text
step = hazards
options = post-sector options
```

It shows the accepted hazard, reason, evidence, affected groups, and—when
strict validation plus Crowd Sourcing is enabled—the same visibility notice as
the review screen.

## System-hazard reference data

Normal system hazards are seeded from Section 5 of:

```text
app/prompts/Energy_truth.txt
app/prompts/Housing_truth.txt
app/prompts/Transport_truth.txt
```

Their `HAZARD n.` entries become `system_hazards`; mitigation-policy mappings
are seeded through `mitigation_measure_policy_system_hazards`.

## Primary implementation methods

```python
_capture_custom_hazard
_handle_custom_hazard_title_clarification
_continue_valid_custom_hazard
_run_custom_hazard_dimension_check
_handle_custom_hazard_clarification
_handle_hazard_evidence_decision
_capture_hazard_evidence
_handle_custom_hazard_population_review
_finalize_custom_hazard_from_grounding
_custom_hazard_added_step
```
