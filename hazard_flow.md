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
  -> deterministic title gates
  -> optional LLM title review
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
title. The state contains the raw/resolved title, selected scope, validation
rounds, scores, clarifications, affected groups, duplicate candidates, status,
and duplicate-override state.

The title gates run in this order:

1. `_plain_custom_hazard_rejection_reason(...)` rejects obvious
   non-transition hazards.
2. `deterministic_custom_hazard_input_review(...)` rejects questions, requests,
   benefits, vague inputs, and other non-hazards; it can return
   `needs_clarification`.
3. `_custom_hazard_sector_mismatch_reason(...)` rejects a mechanism belonging
   mainly to another selected sector.
4. `_review_custom_hazard_input(...)` is used when deterministic logic has not
   decided the title. It returns `valid`, `invalid`, or `needs_clarification`.

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
Twin-transition policy fit
Selected sector fit
Country / region fit
Affected population groups fit
```

Strict mode requires overall score 75 and dimension floor 5. Easy mode requires
45 and 3. Critical dimensions are hazard definition, twin-transition policy,
selected-sector fit, and country/region fit.

The validator routes to:

```text
ask_clarification | ask_duplicate_confirmation | review_groups | validate | reject
```

Only the first one or two unresolved grounding questions are shown. Answers in
`custom_hazard_clarification` are appended to `clarifications`, then the
dimension check runs again. Repeated clarification questions return an error
instead of silently advancing.

## 5. Reason and evidence

When the router needs reason/evidence, the pending custom hazard uses:

```text
add_hazard_reason
  -> custom_hazard_clarification (textarea answer containing the reason)
  -> add_hazard_evidence_decision
```

The reason is parsed with `parse_reason_evidence(...)`; an empty reason is
rejected. It is staged in `session.pending_hazard_reason` and custom state, not
saved.

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
