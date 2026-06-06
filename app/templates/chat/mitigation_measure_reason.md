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

Please provide the reason this measure should reduce the negative impact of this hazard for these socio-demographic profiles. Evidence is optional.
{% else %}
{% if mitigation_examples is defined and mitigation_examples %}
Sector-specific mitigation measure examples:

{{ mitigation_examples }}

{% endif %}
Please share the mitigation measure you would recommend for reducing the negative impact of this hazard for these socio-demographic profiles.
{% endif %}
