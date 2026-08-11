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

Open conversation text such as "None of these hazards fit, I want to add one"
or "The hazard above does not make sense, I want to add a new hazard" is treated
as the same action when the user is on the hazard listing step. Question-shaped
messages such as "What does Add a new Hazard mean?" or "Can I add my own
hazard?" are answered as workflow help and do not move the user into hazard
creation.

If the user starts mitigation planning and reaches hazard selection, **Other
Options** includes:

```text
Go back to list of hazards
```

Selecting it clears the selected-hazard context and returns to the hazard
listing overview.

**Old Flow Summary**

Previously, the custom hazard flow behaved like this:

```text
Enter hazard title
-> accept/reject title
-> ask for reason
-> ask evidence with Yes / No buttons only
-> validate reason and evidence
-> run duplicate and grounding checks
-> review affected groups
-> save hazard
```

Important limitations in the old flow:

```text
1. Natural evidence replies such as "no, I don't have" could be rejected by the
   generic input-quality gate instead of being treated as No.
2. URL evidence pasted in normal chat text was not always extracted from the
   evidence decision message.
3. The dimension review displayed "Custom profile impact reason" as a status
   card, even though custom affected-group clarification already handled that.
4. Generic affected groups such as "people" could pass too far before the user
   was asked for a specific group.
5. The duplicate confirmation branch could loop on "Possible Duplicate Hazard"
   after the user chose to continue with a custom hazard.
6. The affected-group review labelled the refined hazard as "New hazard", which
   was confusing when the app had rewritten the title.
7. Strict validation with Crowd Sourcing enabled did not consistently show the
   platform-visibility notice on review and final success screens.
```

**New Flow Summary**

The current custom hazard flow is:

```text
Enter hazard title
-> deterministic and LLM title screening
-> duplicate check
-> ask title clarification if needed
-> ask reason / justification
-> ask evidence decision
-> accept open-chat Yes / No / URL evidence
-> validate core dimensions first
-> loop clarification until Hazard, Twin-transition, Sector, and Country/Region fit are clear
-> ask generic affected-group clarification if needed
-> review affected groups under "Hazard to be co-created"
-> show strict + crowd-sourcing visibility notice when applicable
-> save hazard
-> show final co-created hazard success message with visibility notice when applicable
```

Key behavior in the new flow:

```text
1. Core dimensions are resolved before reason/evidence/group review can complete:
   Hazard, Twin-transition policy, selected Sector, and Country/Region fit.
2. Evidence decision accepts buttons and open conversation messages.
3. "No", "no I don't have", "no, I don't know", and similar messages continue
   without evidence.
4. A URL pasted in an open conversation message is extracted as URL evidence.
5. Generic affected groups are rejected and the user is asked for a more
   specific targetable group.
6. The "Custom profile impact reason" display-only card is removed; custom
   profile clarification remains in the affected-group flow.
7. Duplicate override is respected after the user chooses to continue.
8. Strict validation plus Crowd Sourcing shows a visibility notice on review and
   success screens.
9. In-scope workflow questions are answered in place and the user remains on the
   same workflow step.
```

System hazards shown in the normal hazard-selection flow are seeded from the
authoritative sector prompt files under:

```text
app/prompts/Energy_truth.txt
app/prompts/Housing_truth.txt
app/prompts/Transport_truth.txt
```

During reference-data seeding, the app extracts each `HAZARD n.` entry from
Section 5 of those files into `system_hazards`. The `hazards.xlsx` seed then
maps mitigation policies to those seeded system hazards through
`mitigation_measure_policy_system_hazards`.

The main methods involved are:

```python
ChatService._capture_custom_hazard(...)
ChatService._validate_custom_hazard(...)
ChatService._handle_custom_hazard_title_clarification(...)
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
title_validation_status
title_clarification_round
title_clarification_questions
title_clarification_answers
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

When accepted here, the app proceeds to the hazard clarification step, where
the reason/justification is collected before evidence is requested.

It can also return `needs_clarification` for transition-linked titles that are
too broad to save or reject. These cases enter the title clarification branch
before reason/justification collection.

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
valid
```

or:

```text
invalid
needs_clarification
```

If the LLM is unavailable, the app returns:

```text
I could not review this hazard for clarity and policy fit because the local LLM is unavailable. Please try again.
```

If the LLM rejects the hazard, the app returns a rewrite-required message.

If the LLM asks for clarification, the app uses the title clarification branch.
If it accepts, the app continues.

**7. Hazard Title Clarification**

For a recognizable but underspecified hazard title, the app enters:

```text
session.phase = custom_hazard_title_clarification
```

The response uses:

```text
step = custom_hazard_title_clarification
input_mode = text
```

The app stores:

```text
session.pending_hazard = <original hazard title>
session.pending_hazard_title_clarification_question = <question>
session.pending_hazard_title_clarification_answers = []
session.custom_hazard.title_validation_status = needs_clarification
session.custom_hazard.title_clarification_round = 1
```

Examples that should ask for title clarification:

```text
Digital energy services leave people behind.
Housing renovation is important.
Transport electrification is a major topic.
```

When the user answers, the app calls:

```python
ChatService._handle_custom_hazard_title_clarification(...)
```

The app validates the original title and clarification together. A meaningful
answer can resolve the title and move to the hazard reason/justification
clarification:

```text
Older adults and low-income households without internet access or digital skills
are excluded from online-only electricity billing and support services.
```

Non-answers, very short answers, ambiguous answers, and question/request-style
replies are not accepted as clarifications. The app keeps:

```text
session.phase = custom_hazard_title_clarification
```

and asks again with `error=True`.

**8. Duplicate Detection**

Before asking for the reason/justification, the app checks possible duplicate
hazards.

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

If the user chooses to continue with the custom hazard, the duplicate override
is stored against the original hazard title. Later validation rounds preserve
that override even when reason or evidence text has been appended, so the user
does not get stuck in the same duplicate confirmation loop.

**9. Reason / Justification Clarification**

After the hazard title is accepted, the app stores:

```text
session.pending_hazard = <hazard title>
session.phase = add_hazard_reason
```

The response uses:

```text
step = hazard_clarification
input_mode = textarea
```

For custom-hazard state, the response uses:

```text
step = custom_hazard_clarification
input_mode = textarea
```

The user is asked to clarify why this should be treated as a hazard in the
selected country, region, and sector. This is where the reason/justification is
collected. Evidence is not requested yet.

At this point the hazard is not saved yet. The user must provide a
reason/justification answer.

**10. Capture Reason / Justification**

When the user answers the clarification prompt, the app calls:

```python
ChatService._capture_hazard_reason(session_id, session, message)
```

The app parses:

```text
Reason
```

If the reason is missing, the app rejects immediately:

```text
Please answer the clarification question and include the reason or justification before continuing.
```

Missing reason keeps the user in the hazard clarification branch.

If the reason is present, the app stores:

```text
session.pending_hazard_reason = <reason>
session.pending_hazard_evidence = ""
```

and moves to the evidence decision step.

**11. Evidence Decision And Input**

The app asks:

```text
Do you have evidence to validate this hazard?
```

The response uses:

```text
session.phase = add_hazard_evidence_decision
step = hazard_evidence_decision
options = Yes / No
```

For custom-hazard state, the step is:

```text
step = custom_hazard_evidence_decision
```

If the user chooses `No`, the app proceeds to validation with no evidence.

If the user chooses `Yes`, the app enters:

```text
session.phase = add_hazard_evidence_input
step = hazard_evidence
input_mode = evidence_only
options = Go back to list of hazards / Skip
```

For custom-hazard state, the step is:

```text
step = custom_hazard_evidence
```

The user can paste an evidence URL or attach a supported file.

The evidence decision step also accepts open conversation messages. For example:

```text
yes, I have evidence
I want to add evidence
no I don't have
no, I don't know
continue without evidence
Use this evidence https://example.org/report.pdf
```

Open-text `No` messages continue without evidence. Open-text URL messages are
normalized into URL evidence and proceed to validation.

**12. Validate Reason / Evidence**

Once evidence is supplied or skipped, the app calls:

```python
ChatService._validate_staged_custom_hazard(session_id, session, evidence)
```

This combines the staged values into the same validation payload used by:

```python
ChatService._validate_custom_hazard(session_id, session, message)
```

The app validates:

```text
Reason
Evidence URL or evidence file content, if supplied
```

**13. Reason Quality Check**

The app validates reason quality through:

```python
_validate_input_quality(...)
```

The reason must explain why the hazard is a negative impact or risk caused by
twin-transition policies for the selected country, region, and sector.

If the reason is low quality, unrelated, meaningless, or clearly invalid, the
app rejects it with `error=True`.

**14. Re-run Plain And Sector Rules With Reason**

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

**15. Stats / Evidence Validation**

The app validates the hazard against statistical/evidence context:

```python
_validate_hazard_against_stats(...)
```

If user evidence is supplied, the app checks whether it supports or contradicts
the hazard claim.

If validation fails, the hazard is rejected and not saved.

**16. Context Review And Clarification**

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

Regression cases now cover this branch explicitly with expected action:

```text
ASK_CONTEXT_CLARIFICATION
```

The expected response remains unsaved and uses:

```text
step = hazards
input_mode = textarea
session.phase = add_hazard_clarification
```

**17. Handle Clarification Answer**

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

**18. Dimension Grounding Check**

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

Regression cases cover this branch with expected action:

```text
ASK_GROUNDING_CLARIFICATION
```

The response uses:

```text
step = custom_hazard_clarification
input_mode = textarea
```

Only the first one or two pending grounding questions are shown. They are taken
from dimensions with `needs_clarification=True`, in dimension order.

**19. Affected Groups Review**

If affected population groups are identified, the app moves to group review.

The user can confirm affected groups or edit them.

If the user adds an affected group without a reason, the app can ask why that
group is affected.

The review screen labels the refined hazard as:

```text
Hazard to be co-created:
```

instead of the older `New hazard:` label. This makes it clear that the app is
showing the reviewed/refined hazard title.

If a group label is too generic, such as:

```text
people
households
residents
consumers
general population
```

the app asks the user to provide a specific affected group before the hazard can
be confirmed.

When strict validation and Crowd Sourcing are enabled, the review screen also
shows a visibility notice explaining that the hazard will be visible to platform
users interested in the selected region and country.

**20. Finalize Custom Hazard**

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

When strict validation and Crowd Sourcing are enabled, the final success message
also shows that the hazard is now visible to other platform users interested in
transition risks for the selected region and country.

**21. Population Profile Extraction**

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

**22. Review And Continue**

The user is shown the custom hazard population/review step.

From there, the custom hazard behaves like a selected hazard and the user can
continue to:

```text
socio-demographic review
mitigation measure creation
mitigation clarification / justification
mitigation evidence
mitigation validation
evaluation
```

**When Clarification Happens**

Clarification can happen in two places:

```text
1. add_hazard_reason
```

This happens after the hazard title is accepted. It collects the
reason/justification through a clarification-style prompt before asking for
evidence.

```text
2. custom_hazard_title_clarification
```

This happens before reason/justification when the hazard title is
transition-linked but too underspecified to accept as a hazard name.

```text
3. custom_hazard_clarification
```

This happens during dimension grounding when one or more grounding dimensions
need more detail.

```text
4. add_hazard_clarification
```

This happens after staged reason/evidence validation when context review says
the reason is unclear but potentially fixable.

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

**Hazard Creation Regression Cases**

The generated workbook is built by:

```text
tests/generate_hazard_creation_test_cases.py
```

It includes cases for:

```text
valid hazards
sector synonyms
mixed-signal valid hazards
invalid or non-hazard inputs
generic consumer-price issues
vague or incomplete titles
hazard title clarification
hazard clarification after reason
grounding dimension clarification
benefits or mitigation statements
wrong-sector hazards
empty input
go back from hazard creation
```

The runner is:

```text
tests/run_hazard_creation_cases.py
```

It writes one result workbook per model:

```text
hazard_creation_test_results_<model>.xlsx
```

The generated expected actions include:

```text
ASK_TITLE_CLARIFICATION
REASK_TITLE_CLARIFICATION
ASK_CONTEXT_CLARIFICATION
ASK_GROUNDING_CLARIFICATION
ACCEPT_HAZARD_NAME
REJECT_REWRITE
REJECT_SECTOR_MISMATCH
SHOW_ADD_HAZARD_PROMPT
GO_BACK_TO_HAZARDS
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
Moves to hazard clarification for reason/justification, then evidence decision
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
