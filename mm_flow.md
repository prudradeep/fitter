**Mitigation Measure Creation Flow**

This document describes the current app flow for creating, validating, saving,
reviewing, and evaluating a mitigation measure.

The mitigation-measure flow starts only after the user has already selected:

```text
Country -> Region -> Sector -> Hazard -> Socio-demographic profiles
```

For a custom hazard, the same mitigation flow starts after the custom hazard has
been accepted and behaves like the selected hazard.

The main methods involved are:

```python
ChatService._create_mitigation_measure_step(...)
ChatService._handle_reason_confirmation(...)
ChatService._capture_mitigation_measure(...)
ChatService._validate_mitigation_reason(...)
ChatService._handle_mitigation_clarity_answer(...)
ChatService._handle_mitigation_target_population_review(...)
ChatService._finalize_validated_mitigation(...)
ChatService._handle_mitigation_review(...)
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
session.phase = mitigation_reason
```

and asks for reason/evidence.

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

If the user continues with the proposed measure, the app proceeds to the reason
step.

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

from the duplicate suggestion screen, the app also proceeds to the reason step.

**8. Reason And Evidence Step**

If there is no duplicate, or the user continues despite the duplicate warning,
the app stores:

```text
session.pending_mitigation_measure = <measure>
session.phase = mitigation_reason
```

The response uses:

```text
step = mitigation_reason
input_mode = reason_evidence
```

The user must provide a reason. Evidence is optional.

The prompt is rendered from:

```text
templates/chat/mitigation_measure_reason.md
```

**9. Validate Reason / Evidence**

When the user submits the reason/evidence payload, the app calls:

```python
ChatService._validate_mitigation_reason(session_id, session, message)
```

The app parses:

```python
parse_reason_evidence(message)
```

If no labelled reason is found, it attempts to use plain unlabelled text:

```python
_plain_reason_from_unlabelled_message(message)
```

If there is no pending or saved mitigation measure, the app returns to:

```text
session.phase = mitigation_measure
step = mitigation_measure
input_mode = mitigation_measure
```

If the reason is missing, the app stays on:

```text
step = mitigation_reason
input_mode = reason_evidence
error = True
```

and shows:

```text
`Reason:` is required. Evidence URL and evidence file are optional.
```

**10. Local Reason And Evidence Checks**

The app checks reason quality with:

```python
ChatService._local_mitigation_reason_error(reason)
```

which delegates to:

```python
local_mitigation_reason_error(...)
```

This rejects:

```text
gibberish
very short reasons
I don't know
no idea
not applicable
vague reasons without a mitigation mechanism
```

The reason must explain how the measure reduces the selected hazard for the
affected groups.

If evidence is supplied, the app checks whether it is readable:

```python
_has_user_supplied_evidence(...)
_has_readable_evidence_content(...)
```

Evidence is optional, but supplied evidence must be usable. Unsupported or
unreadable evidence keeps the user on `mitigation_reason`.

**11. Mitigation Clarity Track**

After local reason/evidence checks, the app runs:

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

The clarity assessment checks whether the app can freeze unambiguous inputs for:

```text
measure description
justification / reason
evidence
hazard link
target population
specificity
implementation mechanism
evidence identifiability
```

If the assessment is clear, the app stores:

```text
session.mitigation_frozen_inputs
```

and moves forward.

If the assessment is unclear but fixable, the app enters:

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

and asks follow-up clarification questions.

If the clarity turn limit is reached, the app discards temporary evidence and
returns the user to:

```text
session.phase = mitigation_reason
step = mitigation_reason
input_mode = reason_evidence
```

with a validation-failed message asking the user to resubmit clearer inputs.

**12. Handle Mitigation Clarification**

Clarification answers are handled by:

```python
ChatService._handle_mitigation_clarity_answer(session_id, session, message)
```

Blank answers are rejected.

Gibberish or unrecognizable answers are rejected.

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
continues to validation. If not, it can ask another clarification question until
the turn cap is reached.

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
from the mitigation measure and reason:

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

**17. Mitigation Review Step**

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

The response uses:

```text
templates/chat/mitigation_review.md
```

and includes grounding validation details:

```python
_grounding_validation_details(session)
```

The user can ask follow-up questions about the validated mitigation measure.

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

The conversation is stored in:

```text
session.stats_conversation
```

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
templates/chat/mitigation_recorded.md
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

**When Clarification Happens**

Clarification can happen in two places:

```text
1. mitigation_clarity
```

This happens when the mitigation measure, reason, evidence, target population,
or mechanism is not clear enough to freeze for validation.

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
reason is missing
reason is too short or says "I don't know"
supplied evidence is unreadable
evidence contradicts the core knowledge base
grounded validation rejects the measure
```

Those cases are validation failures, not clarification branches.

**Required Gates**

A mitigation measure must pass these gates before it is saved:

```text
1. A hazard must already be selected.
2. The mitigation text must be a concrete intervention or policy action.
3. It must reasonably address the selected hazard.
4. It must fit the selected sector and context.
5. It must support green, digital, or twin-transition objectives.
6. It must not duplicate an existing mitigation measure unless the user chooses to continue.
7. It must include a reason.
8. Any supplied evidence must be readable and not contradictory.
9. The app must be able to freeze clear measure/reason/evidence inputs.
10. Grounded validation must support the mitigation measure or accept it under the configured validation mode.
11. Target population must be identified or confirmed.
12. The measure must be stored successfully before evaluation starts.
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
-> mitigation_reason
-> mitigation_clarity, only if the reason or target group is unclear
-> mitigation_target_population_review
-> mitigation_review
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

the app can proceed to clarity, grounded validation, target-population review,
and final save if the remaining checks pass.
