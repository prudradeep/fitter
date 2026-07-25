**Custom Hazard Flow**
The custom hazard flow starts after the user has already selected:

```text
Country -> Region -> Sector
```

Once sector selection is complete, the app shows the post-sector options, including:

```text
Add a new Hazard
```

When the user chooses that, the session moves into custom hazard creation.

**1. Add Hazard Entry**
The app enters:

```text
session.phase = custom_hazard_input
```

The user is asked to type a concise hazard for the selected sector.

Example:

```text
Rural residents face power outages from grid congestion during renewable energy integration
```

The app calls the real method:

```python
ChatService._capture_custom_hazard(session_id, session, message)
```

This is the main hazard-name capture function.

**2. Basic Input Handling**
Inside `_capture_custom_hazard`, the app first handles simple cases.

If the user typed:

```text
Go back to list of hazards
```

Then it returns to the hazard list:

```text
session.phase = hazards
session.pending_hazard = None
session.custom_hazard = None
```

If the input is blank, the app stays in hazard creation and shows the add-hazard prompt again.

If the text is not blank, the app initializes custom hazard state:

```python
session.custom_hazard = default_custom_hazard_state()
```

The state stores:

```text
raw_text
normalized_text
selected_country
selected_region
selected_sector
status
dimension scores
affected groups
duplicate candidates
validation rounds
```

**3. Plain Rejection Rules**
Before calling the LLM, the app applies deterministic plain-text rejection rules.

This catches obvious cases like:

```text
Carbon monoxide poisoning from domestic heating
```

That is rejected because it is a general household safety risk unless the user links it to a green/digital transition policy mechanism.

The app returns a rewrite-required message and marks the relevant grounding dimension as rejected.

**4. Deterministic Input Review**
Next, the app runs deterministic guardrails:

```python
deterministic_custom_hazard_input_review(...)
```

This catches clear cases without relying on the LLM.

It rejects obvious non-hazards, for example:

```text
I like sunny weather
Tell me about retrofit policy
Please add more data to the chart
```

It also rejects benefits or mitigation statements, for example:

```text
Solar subsidies reduce energy bills for low-income households
EV charging grants support taxi drivers
```

Those are not hazards because they describe positive support or mitigation, not a negative impact.

It accepts clear sector-specific transition hazards, for example:

```text
Low-income households face higher electricity bills from renewable grid upgrade tariffs
```

If accepted here, the app skips the LLM classifier and proceeds to reason/evidence collection.

Important: this is not test mocking. This is real production app logic.

**5. Sector Mismatch Check**
The app checks whether the hazard mainly belongs to another sector:

```python
_custom_hazard_sector_mismatch_reason(...)
```

Example selected sector: **Housing**

User hazard:

```text
Taxi drivers face income loss from EV charging downtime and clean vehicle mandates
```

The app rejects it because that is mainly a **Transport** hazard.

The response tells the user to either rewrite it for the selected sector or choose the matching sector.

For your example:

```text
Rural residents face power outages from grid congestion during renewable energy integration
```

It passes in **Energy**, but it should reject in **Housing** or **Transport** as a sector mismatch.

**6. LLM Hazard Classifier**
If the deterministic guardrails cannot decide, the app calls the real LLM classifier:

```python
_review_custom_hazard_input(session, hazard)
```

This renders:

```text
llm/custom_hazard_input_classifier.txt
```

or the model-specific version under:

```text
app/prompts/llm/<model-directory>/custom_hazard_input_classifier.txt
```

The LLM must return either:

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

If the LLM rejects the hazard, the app shows a rewrite-required message.

If the LLM accepts it, the app proceeds.

**7. Duplicate Check**
The app checks whether the proposed custom hazard is similar to existing hazards in the same context.

It compares against:

```text
system hazards
additional hazards
saved custom hazards
same-scope user hazards
```

If a likely duplicate is found, the user is shown a duplicate confirmation step:

```text
Continue with custom hazard
Use existing hazard
Edit custom hazard
```

If no duplicate blocks the flow, the app continues.

**8. Reason And Evidence Step**
After the hazard name is accepted, the app stores:

```text
session.pending_hazard = <hazard text>
session.phase = add_hazard_evidence
```

The user sees:

```text
Reason and Evidence Needed
```

The user must provide a reason. Evidence is optional.

The next real method is:

```python
ChatService._validate_custom_hazard(session_id, session, message)
```

The expected input is a reason/evidence payload from the UI.

**9. Reason Required**
The app parses the user’s reason and optional evidence.

If no reason is provided, it rejects the submission:

```text
Reason is required. Evidence URL and evidence file are optional.
```

So the hazard name alone is not enough to save the hazard. It only moves the user to the validation stage.

**10. Reason Quality Validation**
The app validates the reason using:

```python
_validate_input_quality(...)
```

The reason must explain why the proposed hazard is a negative impact or risk caused by twin-transition policies for the selected country, region, and sector.

If the reason is low quality, unrelated, or meaningless, the app rejects it.

**11. Plain And Sector Checks Again**
The app runs the plain rejection and sector mismatch checks again, this time using:

```text
hazard + reason + evidence
```

This matters because a hazard title may be short, but the reason might reveal that it actually belongs to another sector or is not transition-related.

**12. Stats / Evidence Validation**
Then the app validates the hazard against the sector statistics and evidence:

```python
_validate_hazard_against_stats(...)
```

If the user supplied evidence, the app checks whether the evidence supports or contradicts the claim.

If no evidence is supplied, it can use sector-prompt / RAG context where available.

If validation fails, the hazard is not saved.

**13. Context Review**
Next, the app calls:

```python
_review_custom_hazard_context(...)
```

This checks whether the hazard, reason, and evidence are coherent for the selected country, region, and sector.

The context review can return:

```text
accept
reject
clarification
```

If clarification is needed, the app asks a follow-up question instead of saving immediately.

**14. Dimension Grounding Check**
For custom hazard state, the app then runs:

```python
_run_custom_hazard_dimension_check(...)
```

This calls:

```python
validate_custom_hazard_dimensions(...)
```

The hazard is scored across grounding dimensions:

```text
Hazard definition fit
Twin transition policy fit
Selected sector fit
Country / region fit
Affected population groups
Duplicate check
Custom profile impact reason
Validation readiness
```

The result determines the next action:

```text
ask_clarification
ask_duplicate_confirmation
review_groups
validate
reject
```

**15. Affected Groups Review**
If affected population groups are found, the app moves to group review.

The user may confirm affected groups or edit them.

The app can ask for reasons for user-added groups, especially if the impact mechanism is unclear.

**16. Finalizing The Custom Hazard**
Once the hazard is sufficiently grounded, the app finalizes it:

```python
_finalize_valid_custom_hazard(...)
```

It stores or updates the custom hazard record through:

```python
_ensure_custom_hazard(...)
```

It updates session state:

```text
session.custom_hazards
session.accepted_custom_hazard
session.accepted_custom_hazard_reason
session.accepted_custom_hazard_evidence
session.accepted_custom_hazard_id
```

If evidence was supplied and accepted, temporary evidence can be promoted to validated evidence.

**17. Population Profile Extraction**
The app extracts or builds affected population profiles:

```python
_extract_custom_hazard_affected_population_profiles(...)
```

If no profiles are found, the app may ask target-population questions.

Otherwise, it stores the profiles under:

```text
session.hazard_profiles[custom_hazard]
session.socio_demographic_profiles
```

**18. Custom Hazard Added / Review Step**
Finally, the user is shown the custom hazard review/added step.

From there, the hazard behaves like the selected hazard for the next stages:

```text
socio-demographic review
mitigation measure creation
reason confirmation
mitigation validation
evaluation
```

**Key Rule**
A custom hazard must pass these gates:

```text
1. It must be a hazard, not a benefit/request/fact.
2. It must be tied to green/digital/twin-transition policy.
3. It must fit the selected sector.
4. It must be plausible for the selected country/region.
5. It must have a reason.
6. It must not contradict evidence/statistical context.
7. It must have or collect affected population groups.
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

Because the mechanism is mainly Energy-sector grid integration.