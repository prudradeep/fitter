**Mitigation Measure Creation Flow**

This document describes the current app flow for creating, validating, saving,
reviewing, and evaluating a mitigation measure.

The mitigation-measure flow starts only after the user has already selected:

```text
Country -> Region -> Sector -> Hazard -> Socio-demographic profiles
```

Users can enter mitigation planning by choosing **Start Mitigation Planning** on
the hazard listing step or by typing an equivalent open conversation message
such as "Start mitigation planning". The app then asks the user to select the
hazard to analyse. From that hazard-selection step, **Other Options** lets the
user go back to the hazard listing before choosing a hazard.

During mitigation planning, in-scope workflow questions are answered without
advancing or resetting the workflow. After the answer, the user remains on the
same step and can continue where they left off.

For a custom hazard, the same mitigation flow starts after the custom hazard has
been accepted and behaves like the selected hazard.

**Old Flow Summary**

Previously, the mitigation-measure flow behaved like this:

```text
Start mitigation planning
-> choose manual entry or adopt suggested proposal
-> validate mitigation measure
-> ask clarification / reason
-> ask evidence with Yes / No buttons only
-> validate grounded mitigation
-> identify target population from measure and reason
-> save mitigation measure
-> concept comparison
-> evaluation
```

Important limitations in the old flow:

```text
1. Suggested proposal adoption used the proposal and reason, but target-group
   mechanisms were not reliably carried into target-population identification.
2. If the suggested proposal listed multiple target-group mechanisms, target
   population inference could identify only one group.
3. Natural evidence replies such as "no, I don't know" could be rejected by the
   generic input-quality gate instead of being treated as No.
4. URL evidence pasted in normal chat text was not always extracted from the
   evidence decision message.
5. The reason or clarification could repeat the mitigation measure text and
   still move forward.
6. Grounded validation ABSTAIN / "needs more support" returned the evidence-only
   UI with Skip and evidence file controls, even when clarification was needed.
7. Strict validation with Crowd Sourcing enabled did not consistently show the
   platform-visibility notice on mitigation review.
```

**New Flow Summary**

The current mitigation-measure flow is:

```text
Start mitigation planning
-> choose manual entry or adopt suggested proposal
-> preserve proposal, reason, and target-group mechanisms
-> validate mitigation measure
-> ask clarification / reason
-> reject reason or clarification if it repeats existing input
-> ask evidence decision
-> accept open-chat Yes / No / URL evidence
-> validate grounded mitigation
-> if support is missing, return to clarification textarea
-> identify all target populations from measure, reason, and target-group mechanisms
-> review target population
-> save mitigation measure
-> show strict + crowd-sourcing visibility notice when applicable
-> concept comparison
-> evaluation
-> system inquiry
```

Key behavior in the new flow:

```text
1. Adopted suggested proposals include target-group mechanisms in the stored
   reason context and in target-population inference.
2. Every explicit group label in Target-group mechanisms is extracted and merged
   into the identified target population list.
3. Evidence decision accepts buttons and open conversation messages.
4. "No", "no I don't have", "no, I don't know", and similar messages continue
   without evidence.
5. A URL pasted in an open conversation message is extracted as URL evidence.
6. Repeated reason/clarification text is rejected when it repeats the mitigation
   measure, original input, or existing reason.
7. Missing support / ABSTAIN outcomes ask for clarification with textarea input
   instead of showing evidence-only controls.
8. Mitigation review moves directly into evaluation.
9. Open conversation action text is accepted for mitigation entry, evidence
   decisions, and mitigation-measure creation when it clearly expresses the
   intended workflow action.
```

Seeded mitigation-policy suggestions depend on two reference-data steps:

```text
1. Sector prompt hazards are extracted into system_hazards.
2. hazards.xlsx maps mitigation policies to those system hazards in
   mitigation_measure_policy_system_hazards.
```

This means reference-data seeding must run after changes to either
`app/prompts/*_truth.txt` or `hazards.xlsx`.

The main methods involved are:

```python
ChatService._create_mitigation_measure_step(...)
ChatService._handle_reason_confirmation(...)
ChatService._capture_mitigation_measure(...)
ChatService._handle_mitigation_clarity_answer(...)
ChatService._handle_mitigation_evidence_decision(...)
ChatService._capture_mitigation_evidence(...)
ChatService._handle_mitigation_target_population_review(...)
ChatService._finalize_validated_mitigation(...)
ChatService._handle_mitigation_review(...)
ChatService._build_mitigation_review_messages(...)
ChatService._handle_evaluation_answer(...)
```

**1. Start Mitigation Planning**

The user reaches mitigation planning from the post-hazard / socio-demographic
flow by choosing:

```text
Create Mitigation Measure
```

or an equivalent open-text command such as:

```text
create mitigation
create mitigation measure
start mitigation planning
```

The app calls:

```python
ChatService._create_mitigation_measure_step(session_id, session)
```

The app enters:

```text
session.phase = reason_confirmation
```

Before asking the user to write a measure, the app generates practical policy
considerations:

```python
ChatService._practical_policy_recommendations(session)
```

This uses:

```text
llm/practical_policy_recommendations_user.txt
```

The response uses:

```text
step = reason_confirmation
options = Yes / Adopt mitigation proposal suggested above / No
```

**2. Reason Confirmation Choices**

The app handles the response with:

```python
ChatService._handle_reason_confirmation(session_id, session, message)
```

The accepted actions are:

```text
Yes
Adopt mitigation proposal suggested above
Continue with current mitigation measure
No
```

Open text is also mapped when it clearly means one of those actions.

If the user chooses:

```text
Yes
```

then the app enters manual mitigation entry:

```text
session.phase = mitigation_measure
step = mitigation_measure
input_mode = mitigation_measure
```

The user sees the mitigation-measure prompt from:

```text
templates/chat/mitigation_measure_reason.md
```

If the user chooses:

```text
Adopt mitigation proposal suggested above
```

or:

```text
Continue with current mitigation measure
```

the app calls:

```python
ChatService._adopt_suggested_mitigation_response(...)
```

The adopted measure comes from:

```python
ChatService._suggested_mitigation_measure_for_context(session)
```

which uses:

```text
session.suggested_new_policy_proposal
```

or falls back to:

```python
ChatService._current_policy_mitigation_measure(session)
```

Then the app stores:

```text
session.pending_mitigation_measure = <suggested measure>
session.suggested_new_policy_target_group_mechanisms = <target-group mechanisms>
session.phase = mitigation_clarity
```

and asks clarification questions, including the reason/justification.

When target-group mechanisms are present in the suggested proposal, they are
kept with the adopted measure and later used to infer all intended target
population groups.

If the user chooses:

```text
No
```

the app moves to:

```text
session.phase = other_actions
step = complete
```

**3. Manual Mitigation Measure Capture**

When the user writes a mitigation measure, the app calls:

```python
ChatService._capture_mitigation_measure(session_id, session, message)
```

The app first parses any combined measure/reason payload:

```python
parse_mitigation_reason(message)
```

Then it clears old clarity and validation state:

```python
_clear_mitigation_clarity_state(session)
_clear_mitigation_validation_state(session)
```

It also clears duplicate-suggestion state:

```text
session.suggested_mitigation_measure_id = None
session.suggested_mitigation_measure_name = None
```

**4. Local Mitigation Measure Checks**

Before using the LLM, the app runs local checks.

If the mitigation measure is missing, the app returns:

```text
step = mitigation_measure
input_mode = mitigation_measure
error = True
```

with:

```text
`Mitigation measure:` is required.
```

If the text is gibberish or unrecognizable, the app rejects it.

The app then calls:

```python
ChatService._local_mitigation_measure_error(mitigation_measure)
```

which delegates to:

```python
local_mitigation_measure_error(...)
```

This catches very short or non-meaningful measures.

Example rejected:

```text
help
```

because it is too short to evaluate as a policy action.

**5. LLM Mitigation Measure Validation**

If local checks pass, the app validates only the mitigation-measure text:

```python
ChatService._validate_mitigation_measure_only(session, mitigation_measure)
```

This first tries local review:

```python
_local_mitigation_measure_only_review(...)
```

For obvious weak measures, it returns:

```text
INVALID
```

or:

```text
NEEDS_CLARIFICATION
```

Example weak measures:

```text
improve awareness
government should help
reduce emissions
help people
mitigate the hazard
```

If local review cannot decide, the app calls the LLM using:

```text
llm/mitigation_measure_validation.txt
llm/mitigation_measure_validation_user.txt
```

The LLM response is normalized into a review with:

```text
status
summary
policy_quality
hazard_fit
sector_fit
country_region_fit
twin_transition_fit
clarification_question
suggested_improvement
```

If the LLM is unavailable, the app returns:

```text
templates/chat/mitigation_validation_unavailable.md
```

If the status is not:

```text
VALID
```

the app stays on:

```text
session.phase = mitigation_measure
step = mitigation_measure
input_mode = mitigation_measure
error = True
```

and shows the validation-failed message.

**6. Duplicate Mitigation Check**

After the measure is valid, the app checks whether it duplicates an existing
mitigation measure for the same selected hazard.

First it runs:

```python
ChatService._local_mitigation_duplicate_check(session, mitigation_measure)
```

This compares against saved mitigation measures for the same hazard with:

```python
mitigations_are_similar(...)
```

If no local duplicate is found, the app runs:

```python
ChatService._semantic_mitigation_duplicate_check(session, mitigation_measure)
```

This uses:

```text
llm/mitigation_duplicate_check.txt
llm/mitigation_duplicate_check_user.txt
```

If a duplicate is found, the app enters:

```text
session.phase = mitigation_duplicate_suggestion
step = mitigation_duplicate_suggestion
```

The response uses:

```text
templates/chat/mitigation_duplicate_suggestion.md
```

and shows:

```text
Yes, show existing mitigation report
No, continue with my proposal
```

**7. Duplicate Suggestion Branch**

Duplicate suggestions are handled by:

```python
ChatService._handle_mitigation_duplicate_suggestion(...)
```

If the user chooses:

```text
Yes, show existing mitigation report
```

the app enters:

```text
session.phase = mitigation_duplicate_report
step = mitigation_duplicate_report
```

The response uses:

```text
templates/chat/mitigation_existing_report.md
```

It shows the existing mitigation measure, saved reason, and saved evaluation
report where available.

The next options are:

```text
Yes, continue with my proposed mitigation
No, write another mitigation measure
```

If the user continues with the proposed measure, the app proceeds to mitigation
clarification.

If the user chooses to write another measure, the app returns to:

```text
session.phase = mitigation_measure
step = mitigation_measure
input_mode = mitigation_measure
```

If the user chooses:

```text
No, continue with my proposal
```

from the duplicate suggestion screen, the app also proceeds to mitigation
clarification.

**8. Mitigation Clarification / Reason Step**

If there is no duplicate, or the user continues despite the duplicate warning,
the app stores:

```text
session.pending_mitigation_measure = <measure>
session.phase = mitigation_clarity
```

The response uses:

```text
step = mitigation_clarity
input_mode = textarea
```

The user must answer clarification questions. These questions include the
reason/justification for why the mitigation measure reduces the selected
hazard's negative impact for the affected profiles.

Evidence is not requested in this step. The app explicitly tells the user that
their answers clarify the measure and justification, and that evidence will be
collected next.

If the user typed a combined `Mitigation measure:` and `Reason:` payload, the
reason is preserved and evaluated through this same clarification track.

**9. Mitigation Clarity Track**

The app runs:

```python
ChatService._run_mitigation_clarity_track(
    session_id,
    session,
    mitigation_measure,
    reason,
    evidence_text,
)
```

This calls:

```python
ChatService._assess_mitigation_clarity(...)
```

The LLM prompts are:

```text
llm/mitigation_clarity_assessment.txt
llm/mitigation_clarity_assessment_user.txt
```

The clarity assessment checks whether the app can freeze unambiguous inputs
for:

```text
specificity
justification_clarity
evidence_identifiability
```

During this stage, no user evidence has been requested yet, so missing evidence
does not block clarity.

If the assessment is clear, the app stores:

```text
session.mitigation_frozen_inputs
```

and moves to the evidence decision step.

If the assessment is unclear but fixable, the app stays in:

```text
session.phase = mitigation_clarity
step = mitigation_clarity
input_mode = textarea
```

It stores:

```text
session.pending_mitigation_measure
session.pending_mitigation_reason
session.pending_mitigation_evidence
session.pending_mitigation_clarity_dimension
session.mitigation_clarity_turns
```

and asks follow-up clarification questions about one unresolved dimension at a
time. If the answer still does not resolve the issue, the app asks again until
all required points/dimensions are clear or the clarity turn cap is reached.

If the clarity turn limit is reached, the app returns the user to:

```text
session.phase = mitigation_measure
step = mitigation_measure
input_mode = mitigation_measure
```

with a validation-failed message asking the user to resubmit a clearer
mitigation measure.

**10. Handle Mitigation Clarification**

Clarification answers are handled by:

```python
ChatService._handle_mitigation_clarity_answer(session_id, session, message)
```

Blank answers are rejected.

Gibberish or unrecognizable answers are rejected.

Clarification answers are also rejected if they repeat information already
provided instead of adding clarification. This includes repeating:

```text
the mitigation measure
the original/pending mitigation input
the existing reason
```

The user is asked to add new details that explain the mitigation mechanism or
missing context.

For ordinary clarity questions, the app validates answer quality through:

```python
_validate_clarification_answer_quality(session, message)
```

Then it merges the clarification into the frozen input candidates:

```python
_merge_mitigation_clarification(...)
```

Labelled clarification fields can update:

```text
measure
justification
evidence
```

Unlabelled clarification is merged based on the unclear dimension:

```text
specificity -> mitigation measure
evidence_identifiability -> evidence
otherwise -> reason
```

After merging, the app re-runs the clarity track. If clarity is resolved, it
moves to the evidence decision step. If not, it can ask another clarification
question until the turn cap is reached.

**11. Evidence Decision And Input**

Once mitigation measure and justification are clear, the app asks:

```text
Do you have evidence to validate this mitigation measure?
```

The response uses:

```text
session.phase = mitigation_evidence_decision
step = mitigation_evidence_decision
options = Yes / No
```

If the user chooses `No`, the app validates the frozen mitigation inputs
without user evidence and uses the curated mitigation knowledge base for
support.

If the user chooses `Yes`, the app enters:

```text
session.phase = mitigation_evidence_input
step = mitigation_evidence
input_mode = evidence_only
options = Back to evidence question / Skip
```

The user can paste a URL or attach a supported file:

```text
PDF
DOCX
MD
TXT
```

The evidence decision step also accepts open conversation messages. For example:

```text
yes, I have evidence
I have evidence
no I don't have
no, I don't know
skip evidence
Evidence is at https://example.org/retrofit-study.pdf
```

Open-text `No` messages continue without evidence. Open-text URL messages are
normalized into URL evidence and proceed to validation.

**12. Evidence Checks**

Evidence is optional, but supplied evidence must be usable. The app checks:

```python
_has_user_supplied_evidence(...)
_has_readable_evidence_content(...)
```

Unsupported or unreadable evidence keeps the user on:

```text
session.phase = mitigation_evidence_input
step = mitigation_evidence
input_mode = evidence_only
```

The user can revise the evidence or choose `Skip`.

**13. Grounded Mitigation Validation**

Once mitigation inputs are clear, the app validates the frozen inputs with:

```python
ChatService._validate_frozen_mitigation_inputs(...)
```

This calls grounded mitigation validation through:

```python
ChatService._validate_mitigation_against_stats(
    session,
    mitigation_measure,
    reason,
    evidence,
)
```

If the user supplied evidence, the app first validates that user evidence
against the core knowledge base:

```python
_validate_user_evidence_against_core_kb(...)
```

If the evidence contradicts the core knowledge base, the outcome is rejected.

If the evidence cannot be validated, the outcome can abstain.

If no user evidence is supplied, the app retrieves support from the curated
mitigation knowledge base:

```python
_mitigation_main_knowledge_context(...)
```

If user evidence is supplied, the app retrieves support using:

```python
_mitigation_evidence_context(...)
```

Then the app calls the LLM prompts:

```text
llm/mitigation_groundedness_validation.txt
llm/mitigation_groundedness_validation_user.txt
```

The grounding validator checks these dimensions:

```text
hazard fit
mechanism
justification soundness
evidence quality
contraindications
feasibility
```

The app samples and scores the verdict, then stores validation details such as:

```text
session.mitigation_validation
session.mitigation_grounded_synthesis
```

If grounding validation is unavailable, rejected, or abstained, the app returns a
validation message and does not save the mitigation measure.

For ABSTAIN / missing support outcomes, the app returns to:

```text
session.phase = mitigation_clarity
step = mitigation_clarity
input_mode = textarea
pending_mitigation_clarity_dimension = justification_clarity
```

The screen is headed `Clarification needed` and does not show the evidence-only
controls such as `Skip`, `Back to evidence question`, URL input, or file input.

**14. Target Population Extraction**

After grounded validation succeeds, the app stores:

```text
session.mitigation_measure = <validated measure>
session.mitigation_reason = <validated reason>
session.pending_mitigation_measure = None
```

Then it calls:

```python
ChatService._ensure_mitigation_target_population_from_inputs(...)
```

If target population has not already been identified, the app tries to infer it
from the mitigation measure, reason, and any adopted target-group mechanisms:

```python
_infer_mitigation_target_population_from_inputs(...)
```

which matches text through:

```python
_match_mitigation_target_population_answer(...)
```

If no target population can be identified, the app enters:

```text
session.phase = mitigation_clarity
step = mitigation_clarity
input_mode = textarea
pending_mitigation_clarity_dimension = target_population
```

and asks the user which target groups the measure should support.

When target-group mechanisms are available, the app deterministically extracts
each explicit group label before the colon and merges those groups with the
normal target-population matcher. For example:

```text
Utility arrears households (twice or more): Provide direct financial support...
Religious minorities: Ensure equal access...
```

identifies both:

```text
Utility arrears households (twice or more)
Religious minorities
```

**15. Target Population Review**

When target populations are identified, the app calls:

```python
ChatService._mitigation_target_population_review_step(...)
```

The app enters:

```text
session.phase = mitigation_target_population_review
step = mitigation_target_population_review
```

It stores:

```text
session.pending_mitigation_measure
session.pending_mitigation_reason
session.pending_mitigation_evidence
session.mitigation_target_population
```

The user sees the identified target groups and these options:

```text
Continue
Add more target population
```

If the user chooses:

```text
Add more target population
```

the app goes back to:

```text
session.phase = mitigation_clarity
pending_mitigation_clarity_dimension = target_population_additional
```

The answer is matched to available target-population groups and merged into:

```text
session.mitigation_target_population
```

If the user chooses:

```text
Continue
```

and validation is already complete, the app finalizes the mitigation measure.

If validation is not complete, the app re-runs clarity and frozen-input
validation before finalizing.

**16. Finalize And Save Mitigation Measure**

The app finalizes through:

```python
ChatService._finalize_validated_mitigation(session_id, session)
```

It resolves the selected hazard reference:

```python
_selected_hazard_reference(session_id, session)
```

Then it saves the mitigation measure through:

```python
_store_mitigation_measure(...)
```

The stored record includes:

```text
user_session_id
user_hazard_id
custom_hazard_id
system_hazard_id
additional_hazard_id
mitigation_measure
reason
target_population
validation_mode
is_crowd_sourced
```

The saved record id is stored in:

```text
session.mitigation_record_id
```

The app records activity:

```text
mitigation_measure_validated
```

and moves to mitigation review.

**17. Concept Comparision**

After saving, the app calls:

```python
ChatService._mitigation_review_step(session_id, session)
```

The app enters:

```text
session.phase = mitigation_review
step = mitigation_review
input_mode = mitigation_review
```

This step opens a conversational discussion before evaluation questions. It is
not the evaluation questionnaire yet.

The response uses:

```text
templates/chat/mitigation_review.md
```

and is headed:

```text
Concept Comparision
```

The app builds the discussion through:

```python
ChatService._mitigation_review_response(...)
ChatService._build_mitigation_review_messages(...)
```

The initial review prompt asks the model to compare the conceptual design of the
validated mitigation measure against the configured conceptual source range:

```text
kb/FITTER_D2.3_FINAL.pdf
pages 26 to 91
```

The app reads those pages from the local PDF, ranks the page excerpts against
the selected country, region, sector, hazard, target population, mitigation
measure, and reason, then passes the most relevant excerpts into the review
assistant as:

```text
Conceptual source excerpts for the pre-evaluation discussion
```

The discussion should explain:

```text
what the mitigation measure covers well
what is not covered or is under-specified
pros / strengths
cons / risks / trade-offs
practical ways to strengthen or target the measure
```

It also includes grounding validation details:

```python
_grounding_validation_details(session)
```

The user can ask follow-up questions about the validated mitigation measure and
the concept comparison before moving into scoring.

Follow-up questions are handled by:

```python
ChatService._handle_mitigation_review(session_id, session, message)
```

The app locally rejects gibberish or very short questions, then validates input
quality with:

```python
_validate_input_quality(...)
```

Valid follow-up questions are answered by:

```python
_mitigation_review_response(session, message)
```

Follow-up answers stay grounded in the sector context, the curated mitigation
knowledge, and the configured conceptual source excerpts. If the excerpts are
thin or only indirectly relevant, the assistant should say so rather than
inventing coverage. User-facing answers should not name the source document or
page range.

The conversation is stored in:

```text
session.stats_conversation
```

When strict validation and Crowd Sourcing are enabled, the mitigation review
screen shows a visibility notice explaining that the mitigation measure will be
visible to platform users interested in mitigation options for the selected
region and country.

**18. Move To Evaluation**

From mitigation review, the user can choose:

```text
Move to next step
```

The app calls:

```python
ChatService._start_evaluation_questions(session_id, session)
```

It loads:

```python
session.evaluation_questions = self._evaluation_questions()
session.evaluation_index = 0
session.evaluation_answers = []
```

If there are no evaluation questions, the app enters:

```text
session.phase = evaluation_complete
step = evaluation_complete
```

and shows:

```text
templates/chat/mitigation_recorded.md
```

If questions exist, the app enters:

```text
session.phase = evaluation_question
step = evaluation_question
input_mode = evaluation_question
```

and asks the first evaluation question.

The mitigation review step moves directly to evaluation.

**19. Evaluation Question Flow**

Evaluation answers are handled by:

```python
ChatService._handle_evaluation_answer(session_id, session, message)
```

The app parses:

```python
parse_evaluation_answer(message)
```

The user must provide:

```text
score from 1 to 10
```

Reason and evidence are optional.

If no score is found, the app repeats the current question with:

```text
Please provide a score from 1 to 10.
```

If reason or evidence is provided, the app validates input quality with:

```python
_validate_input_quality(...)
```

and validates the answer against context through the evaluation validation
prompts:

```text
llm/evaluation_answer_validation.txt
llm/evaluation_answer_validation_user.txt
```

Each valid answer is saved as a `UserQuestionResponse` associated with:

```text
hazard_id
mitigation_measure_id
question_id
score
reason
evidence
```

The app increments:

```text
session.evaluation_index
```

If more questions remain, it asks the next one. If all questions are complete,
it moves to the completion step.

**20. Evaluation Complete**

When all evaluation questions are answered, the app enters:

```text
session.phase = evaluation_complete
step = evaluation_complete
```

The app promotes temporary evidence where appropriate:

```python
_promote_temporary_evidence(session)
```

Then it shows:

```text
templates/chat/evaluation_complete.md
```

The completed mitigation measure now has:

```text
validated measure text
validated reason
target population
grounding validation details
saved database record
evaluation answers
```

The app then starts system inquiry:

```text
session.phase = system_inquiry_intro
step = system_inquiry_intro
```

System inquiry generates up to three observations from the current mitigation
context, evaluation scores, affected groups, target population, and other
saved measures in the session. The first saved measure is capped at two
questions; later measures are capped at three. Candidate probes that are not
shown because of the cap are retained as held observations and named in the
intro boundary note.

Each candidate is enriched with probe-library metadata before ranking:

```text
candidate_id
tier
library_version
trigger_basis
required_anchors
anchor_counts
candidate_status
salience_score
```

Candidates missing required anchors are marked `discarded_no_anchor` and are not
shown. Valid candidates beyond the cap are marked `held_cap`.

Before probe selection, the app builds a deterministic MeasureAttributes-style
profile for the mitigation measure:

```text
action_type
leverage_depth
delivery_channel
cost_incidence
time_to_benefit
eligibility_basis
named_group_ids
named_sectors
requires_capacity
capacity_type
```

The current implementation now runs a constrained LLM extraction call first
(`extraction_method = llm_constrained_v1`) and falls back to code heuristics
(`deterministic_v1_llm_unavailable`) when the local model is unavailable. Probe
triggers use the resulting attributes.

Current deterministic probes include:

```text
C2-P1 recognition / unnamed affected group
C1-P1 distributional incidence
C3-P1 procedural access
A2-P1 cross-sector coupling
A4-P1 delay and time horizon
A5-P1 leverage-point self-evaluation mismatch
A6-P1 policy resistance and rebound
A7-P1 capacity and stock constraints
C4-P1 long-term burden
D1-P1 measure interaction
D2-P1 same-group cumulative burden
D3-P1 leverage concentration
B1-P1 problem framing fallback
```

`D1-P1` uses deterministic interaction signatures for the initial pairwise
summary. The candidate then enters the same LLM screen, verify, and corpus
adjudication stages as the other probes, after anchor validation.

The user can start or skip the inquiry.

If started, each observation enters:

```text
session.phase = system_inquiry_observation
step = system_inquiry_observation
input_mode = textarea
```

Each response is stored as a system inquiry annotation with a resolution state:

```text
addressed
partially_addressed
not_applicable_reasoned
acknowledged_unresolved
open
```

Thin or dismissive responses receive one follow-up:

```text
session.phase = system_inquiry_followup
step = system_inquiry_followup
input_mode = textarea
```

The user can answer, skip the follow-up, or end system inquiry. When all
observations are handled, the app enters:

Follow-up wording is selected from each probe's `followup_types` metadata rather
than generated freely. Current deterministic follow-up types are:

```text
specify_mechanism
name_group
state_timeframe
```

```text
session.phase = system_inquiry_complete
step = system_inquiry_complete
```

and shows:

```text
templates/chat/system_inquiry_complete.md
```

At completion, the app builds a system inquiry profile and writes the full
payload to the saved mitigation measure:

```text
user_mitigation_measures.system_inquiry_json
```

The payload includes the coverage summary, resolution-state profile, annotations,
follow-up questions, and follow-up responses.

The system inquiry profile includes `per_family` coverage records for
`A_structure`, `B_framing`, `C_justice`, and `D_portfolio`. Each family records
surfaced questions, response-state counts, coverage, and valid probes held by
the cap. The profile also includes `leverage_distribution`, a count of current
and prior saved measures classified as `parameter`, `rules`, `goals`, or
`paradigm`, and `trajectory`, a per-measure sequence of family coverage values
from prior saved system inquiry payloads plus the current measure.

The profile includes `session_id_anon` and `library_version` so later telemetry
or evaluation can group profile records without storing a raw session key.

The saved `system_inquiry_json` also includes a `telemetry` object shaped for
future central aggregation. It contains only anonymised/session context,
library/model identifiers, measure ordinal, probe outcome states, response
length buckets, family coverage, leverage distribution, and skip status. It
does not include composed observation text, user free text, or new knowledge
claims; those remain local in `annotations`, `held_observations`, and the
measure-attached payload.

The payload also records `candidate_audit`, a local-only runtime list for every
candidate probe produced by the deterministic library. Each item records the
candidate id, probe id, measure id, anchors, anchor counts, screen/verify
fields, corpus label, citations, salience score, and final status
(`selected`, `held_cap`, `discarded_no_anchor`, `discarded_dedupe`,
`discarded_refuted`, or `discarded_unstable`). In the async chat path,
`discarded_unstable` can now come from the constrained screen/verify stages,
and `discarded_refuted` can come from constrained corpus adjudication. If the
LLM is unavailable, the deterministic candidate metadata remains authoritative.
This preserves why a lens was shown, held, or discarded without placing user
text or system-inquiry claims into telemetry.

Before candidate finalization, the async chat path runs the constrained LLM
pipeline:

```text
P1 MeasureAttributes extraction
P2 probe screen
P3 probe verify
P4 corpus adjudication
P5 response adjudication during dialogue
```

If the local LLM is unavailable or returns invalid JSON, each stage keeps the
deterministic result and continues. Candidate finalization then applies the
document's bounding rules locally:
ordinal-1 measures surface at most two observations; later measures surface at
most three and include a portfolio lens when one is available; no more than two
selected observations come from one family; after ten prior surfaced
observations in the session, only candidates with `salience_score >= 0.9` are
eligible to surface. Other valid candidates remain in the local audit as
`held_cap`.

Each annotation is written with:

```text
annotation_id
version = 1
created_at
status = current
context_fingerprint
superseded_by = null
```

Annotations also copy the selected candidate metadata needed for local audit and
future invalidation: candidate id/status, trigger basis, screen result,
verification votes, salience score, citations, source references, required
anchors, anchor counts, and the full anchor graph. User response text remains
only in the annotation payload attached to the measure.

Implemented probes carry per-probe source references back to the lens catalogue
entries in `System inquiry.md §5.3`. Unknown future probes fall back to the
generic runtime schema reference in `§4.4` until their library records are
authored.

`C1-P1` and `C2-P1` can receive `corpus_label = evidenced` when their
claim is directly anchored in the session's structured affected-population
profile. The saved candidate/annotation then includes a local citation with
`source = session_affected_population_profile` and the matched predictor label.
The LLM corpus adjudicator may mark a candidate `refuted` when supplied session
facts directly contradict it, but it can only keep `evidenced` when the
candidate already carries structured citations. Probes without structured
profile support remain `unproven`.

The context fingerprint is computed from the selected hazard, mitigation
measure, mitigation reason, target population, and evaluation scores. If the
system inquiry is persisted again for the same mitigation record after that
context changes, previous annotations are retained under
`superseded_annotations` with `status = superseded` and `superseded_by` pointing
to the new fingerprint.

When the system inquiry intro opens and the saved mitigation record already has
current annotations with a different context fingerprint, the app shows a
re-run note. The user is offered the normal Start / Skip choice; stale
reflections are never force-regenerated.

When an existing saved mitigation measure is shown through the duplicate-report
path, the rendered report includes a **Systemic Reflection** section built from
the current annotations in `system_inquiry_json`. Superseded annotations are not
rendered inline, but their retained count is shown.

**When Clarification Happens**

Clarification can happen in two places:

```text
1. mitigation_clarity
```

This happens after the mitigation measure is accepted. It collects and validates
the reason/justification, and it can ask repeated clarification questions until
the measure, justification, and required dimensions are clear enough to freeze
for validation.

```text
2. mitigation_target_population_review -> Add more target population
```

This happens when the user wants to add another target group after the app has
already identified initial groups.

Clarification does not happen when:

```text
mitigation measure is missing
measure text is gibberish
measure is clearly not a mitigation measure
supplied evidence is unreadable
evidence contradicts the core knowledge base
grounded validation rejects the measure
```

Those cases are validation failures or evidence-step errors, not mitigation
clarification branches. Missing or unclear justification is handled inside
`mitigation_clarity`.

**Required Gates**

A mitigation measure must pass these gates before it is saved:

```text
1. A hazard must already be selected.
2. The mitigation text must be a concrete intervention or policy action.
3. It must reasonably address the selected hazard.
4. It must fit the selected sector and context.
5. It must support green, digital, or twin-transition objectives.
6. It must not duplicate an existing mitigation measure unless the user chooses to continue.
7. It must include a clarified reason/justification.
8. The app must be able to freeze clear measure and justification inputs.
9. The evidence decision step must be completed.
10. Any supplied evidence must be readable and not contradictory.
11. Grounded validation must support the mitigation measure or accept it under the configured validation mode.
12. Target population must be identified or confirmed.
13. The measure must be stored successfully before evaluation starts.
14. The user reaches the concept comparison discussion before starting evaluation questions.
```

**Example**

Selected hazard:

```text
Higher electricity bills from renewable grid upgrade tariffs
```

Mitigation measure:

```text
Targeted electricity bill support for low-income households during grid upgrade tariff increases
```

Expected flow:

```text
mitigation_measure
-> mitigation_clarity
-> mitigation_evidence_decision
-> mitigation_evidence, only if the user chooses to add evidence
-> mitigation_target_population_review
-> mitigation_review / concept comparison discussion
-> evaluation_question
-> evaluation_complete
```

If the user submits:

```text
Reason: It helps.
```

the app should reject or clarify because the reason does not explain how the
measure reduces the selected hazard for the affected groups.

If the user submits:

```text
Reason: It reduces affordability pressure for low-income households while grid upgrade costs are passed through to bills.
```

the app can freeze the clarification, ask whether the user has evidence, run
grounded validation, review target population, and save if the remaining checks
pass.
