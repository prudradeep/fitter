## Mitigation Measure

Selected hazard:

- **{{ hazard }}**

{% if country is defined or region is defined or sector is defined %}
Selected context:

- **Country:** {{ country }}
- **Region:** {{ region }}
- **Sector:** {{ sector }}

{% endif %}
{% if selected_policy is defined and selected_policy %}
Selected policy:

- **{{ selected_policy }}**

{% endif %}
Socio-demographic profiles to consider:

{{ dgs }}

What is the mitigation measure, and how will it reduce the selected hazard for the affected profiles?

Use this format:

`Mitigation measure: ...`

`Reason: Explain the causal pathway through which it will reduce the hazard.`

You may also include supporting evidence. The measure and explanation will be checked for clarity and relevance, then against the knowledge base before you confirm the resulting reflection and beneficiary groups.
