{% if mitigation_measure is defined and mitigation_measure %}
## Mitigation Reason and Evidence
{% else %}
## Mitigation Measure
{% endif %}

Selected hazard:

- **{{ hazard }}**

Socio-demographic profiles to consider:

{{ dgs }}

{% if mitigation_measure is defined and mitigation_measure %}
Proposed mitigation measure:

- **{{ mitigation_measure }}**

For your region, How will this mitigation measure reduce the negative impact of this hazard for the affected profiles. If you provide the evidence for your explanation it will help me to validate your input better.
{% else %}
Please share the mitigation measure you would recommend for reducing the negative impact of this hazard for these socio-demographic profiles.
{% endif %}
