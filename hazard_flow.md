**Custom Hazard Creation Flow**

This document describes the current app flow for creating a custom hazard. The
flow starts only after the user has selected:

```text
Country -> Region -> Sector
```

After sector selection, the app shows post-sector actions. The custom hazard
flow begins when the user chooses:

```text
Add a new Hazard
```

The main methods involved are:

```python
ChatService._capture_custom_hazard(...)
ChatService._validate_custom_hazard(...)
ChatService._handle_custom_hazard_clarification(...)
ChatService._run_custom_hazard_dimension_check(...)
```

**1. Enter Hazard Input**

The app enters:

```text
session.phase = custom_hazard_input
```

The user is asked to type the hazard title.

The app calls:

```python
ChatService._capture_custom_hazard(session_id, session, message)
```

**2. Basic Input Handling**

Inside `_capture_custom_hazard`, the app handles simple inputs first.

If the user selects:

```text
Go back to list of hazards
```

Then the app returns to:

```text
session.phase = hazards
session.pending_hazard = None
session.custom_hazard = None
```

If the hazard text is blank, the app stays on hazard entry and returns the add
hazard prompt with `error=True`.

If the text is non-empty, the app initializes:

```python
session.custom_hazard = default_custom_hazard_state()
```

The custom hazard state tracks:

```text
raw_text
normalized_text
selected_country
selected_region
selected_sector
validation_round
dimension_scores
affected_groups
duplicate_candidates
status
```

**3. Plain Rejection Rules**

Before the LLM classifier, the app checks plain deterministic rejection rules:

```python
_plain_custom_hazard_rejection_reason(...)
```

This catches inputs that are not acceptable as transition hazards even before
sector matching.

Example:

```text
Carbon monoxide poisoning from domestic heating
```

This is rejected as a general household safety risk unless it is clearly linked
to a green or digital transition policy mechanism.

The app returns:

```text
step = hazards
error = True
```

and shows the rewrite-required message.

**4. Deterministic Hazard Review**

The app then runs:

```python
deterministic_custom_hazard_input_review(
    selected_sector=session.sector,
    hazard=hazard,
)
```

This is real app logic, not test mocking.

It rejects obvious non-hazards, requests, vague statements, or benefits.

Examples rejected here:

```text
I like sunny weather
Tell me about retrofit policy
Please add more data to the chart
Solar subsidies reduce energy bills for low-income households
EV charging grants support taxi drivers
```

It accepts clear sector-specific transition hazards without needing the LLM.

Example accepted in Energy:

```text
Rural residents face power outages from grid congestion during renewable energy integration
```

When accepted here, the app proceeds directly to the reason/evidence step.

**5. Sector Mismatch Check**

Next, the app checks whether the hazard mainly belongs to another sector:

```python
_custom_hazard_sector_mismatch_reason(...)
```

This check uses:

```text
hazard text
selected sector
sector signal scores
```

Example selected sector:

```text
Housing
```

Hazard:

```text
Taxi drivers face income loss from EV charging downtime and clean vehicle mandates
```

Result:

```text
Rejected for selected_sector_fit
```

The app tells the user to rewrite the hazard for the selected sector or choose
the matching sector.

Important example:

```text
Rural residents face power outages from grid congestion during renewable energy integration
```

This passes in:

```text
Energy
```

It is rejected in:

```text
Housing
Transport
```

because the mechanism is mainly Energy-sector grid integration.

**6. LLM Hazard Classifier**

If deterministic rules do not decide the case, the app calls:

```python
_review_custom_hazard_input(session, hazard)
```

This renders:

```text
llm/custom_hazard_input_classifier.txt
```

or a model-specific prompt under:

```text
app/prompts/llm/<model-directory>/custom_hazard_input_classifier.txt
```

The model must return:

```text
ACCEPT
```

or:

```text
REJECT <reason>
```

If the LLM is unavailable, the app returns:

```text
I could not review this hazard for clarity and policy fit because the local LLM is unavailable. Please try again.
```

If the LLM rejects the hazard, the app returns a rewrite-required message.

If it accepts, the app continues.

**7. Duplicate Detection**

Before asking for the reason, the app checks possible duplicate hazards.

It compares the proposed hazard against:

```text
same-sector system hazards
same-scope custom hazards
same-scope user hazards
```

If a duplicate is found, the app enters a duplicate confirmation step:

```text
session.phase = custom_hazard_duplicate_confirmation
```

The user sees options:

```text
Continue with custom hazard
Use existing hazard
Edit custom hazard
```

If there is no blocking duplicate, the app continues.

**8. Reason And Evidence Step**

After the hazard title is accepted, the app stores:

```text
session.pending_hazard = <hazard title>
session.phase = add_hazard_evidence
```

The response uses:

```text
step = custom_hazard_validation
input_mode = reason_evidence
```

The user sees:

```text
Reason and Evidence Needed
```

At this point the hazard is not saved yet. The user must provide a reason.
Evidence is optional.

**9. Validate Reason / Evidence**

When the user submits the reason/evidence payload, the app calls:

```python
ChatService._validate_custom_hazard(session_id, session, message)
```

The app parses:

```text
Reason
Evidence URL or evidence file content
```

If the reason is missing, the app rejects immediately:

```text
Reason is required. Evidence URL and evidence file are optional.
```

This is not a clarification branch. Missing reason is a validation failure.

**10. Reason Quality Check**

The app validates reason quality through:

```python
_validate_input_quality(...)
```

The reason must explain why the hazard is a negative impact or risk caused by
twin-transition policies for the selected country, region, and sector.

If the reason is low quality, unrelated, meaningless, or clearly invalid, the
app rejects it with `error=True`.

**11. Re-run Plain And Sector Rules With Reason**

The app re-runs plain rejection and sector mismatch checks using:

```text
hazard + reason + evidence
```

This matters because a short hazard title might look valid, but the reason can
reveal a different sector or a non-transition mechanism.

If sector mismatch is detected here, the response marks:

```text
selected_sector_fit = REJECTED
```

**12. Stats / Evidence Validation**

The app validates the hazard against statistical/evidence context:

```python
_validate_hazard_against_stats(...)
```

If user evidence is supplied, the app checks whether it supports or contradicts
the hazard claim.

If validation fails, the hazard is rejected and not saved.

**13. Context Review And Clarification**

Next, the app calls:

```python
_review_custom_hazard_context(...)
```

This checks whether the hazard, reason, evidence, country, region, and sector
fit together coherently.

The context review can return:

```text
accept
reject
clarification
```

If it returns `clarification`, the app does not save the hazard yet. It enters:

```text
session.phase = add_hazard_clarification
```

and stores:

```text
session.pending_hazard_reason = <reason>
session.pending_hazard_evidence = <evidence>
session.pending_hazard_clarification_question = <question>
```

The user is asked a follow-up question.

Example unclear reason:

```text
Hazard: Higher electricity bills
Reason: It affects people.
```

The app may ask who is affected or how the selected transition policy causes the
bill increase.

**14. Handle Clarification Answer**

When the user answers the clarification question, the app calls the custom
hazard clarification handler:

```python
_handle_custom_hazard_clarification(...)
```

The app combines:

```text
hazard
original reason
evidence
clarification answer
```

Then it continues validation. If the clarification resolves the issue, the flow
moves forward. If not, it can reject or ask for more grounding depending on the
next validation result.

**15. Dimension Grounding Check**

For accepted custom hazard state, the app runs:

```python
_run_custom_hazard_dimension_check(...)
```

This calls:

```python
validate_custom_hazard_dimensions(...)
```

The hazard is scored across these dimensions:

```text
Hazard definition fit
Twin transition policy fit
Selected sector fit
Country / region fit
Affected population groups
Duplicate check
Custom profile impact reason
Clarification progress
Validation readiness
```

The dimension check sets a next action:

```text
ask_clarification
ask_duplicate_confirmation
review_groups
validate
reject
```

If dimension grounding still needs clarification, the app enters:

```text
session.phase = custom_hazard_clarification
```

and asks one or two grounding questions.

This is separate from the earlier `add_hazard_clarification` context-review
question.

**16. Affected Groups Review**

If affected population groups are identified, the app moves to group review.

The user can confirm affected groups or edit them.

If the user adds an affected group without a reason, the app can ask why that
group is affected.

**17. Finalize Custom Hazard**

Once the hazard is valid and grounded, the app finalizes it:

```python
_finalize_valid_custom_hazard(...)
```

or:

```python
_finalize_custom_hazard_from_grounding(...)
```

The app stores or updates the custom hazard through:

```python
_ensure_custom_hazard(...)
```

The session is updated:

```text
session.custom_hazards
session.accepted_custom_hazard
session.accepted_custom_hazard_reason
session.accepted_custom_hazard_evidence
session.accepted_custom_hazard_id
```

If evidence was supplied and accepted, temporary evidence can be promoted to
validated evidence.

**18. Population Profile Extraction**

The app extracts affected population profiles:

```python
_extract_custom_hazard_affected_population_profiles(...)
```

If no profiles are found, the app may ask target-population questions.

If profiles are found, it stores them in:

```text
session.hazard_profiles[custom_hazard]
session.socio_demographic_profiles
```

**19. Review And Continue**

The user is shown the custom hazard population/review step.

From there, the custom hazard behaves like a selected hazard and the user can
continue to:

```text
socio-demographic review
mitigation measure creation
mitigation reason/evidence
mitigation validation
evaluation
```

**When Clarification Happens**

Clarification can happen in two places:

```text
1. add_hazard_clarification
```

This happens after reason/evidence submission when context review says the
reason is unclear but potentially fixable.

```text
2. custom_hazard_clarification
```

This happens during dimension grounding when one or more grounding dimensions
need more detail.

Clarification does not happen when:

```text
reason is missing
input is clearly not a hazard
input clearly belongs to another sector
evidence clearly contradicts the hazard
```

Those cases are rejected or sent back for rewrite.

**Required Gates**

A custom hazard must pass these gates before it is saved:

```text
1. It must be a hazard, not a request, benefit, mitigation, or unrelated fact.
2. It must link to green, digital, or twin-transition policy.
3. It must fit the selected sector.
4. It must be plausible for the selected country/region.
5. It must include a reason.
6. It must not contradict evidence/statistical context.
7. It must identify or collect affected population groups.
8. It must pass grounding dimensions or resolve clarification questions.
```

**Example**

Selected sector:

```text
Energy
```

Hazard:

```text
Rural residents face power outages from grid congestion during renewable energy integration
```

Expected result:

```text
Accepted as hazard name
Moves to Reason and Evidence Needed
```

Selected sector:

```text
Housing
```

Same hazard:

```text
Rural residents face power outages from grid congestion during renewable energy integration
```

Expected result:

```text
Rejected for sector fit
```

because the mechanism is mainly Energy-sector grid integration.
