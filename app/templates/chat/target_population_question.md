{% if error_reason %}
**{{ error_reason }}**

{% endif %}
{% if mitigation_proposal %}
## Target population group
{% else %}
## Affected Population Group
{% endif %}

{% if not mitigation_proposal %}
Hazard to be co-created:

- **{{ hazard }}**
{% endif %}

Question {{ current }} of {{ total }}

{{ question }}

Choose one of the options below, skip this question, or skip all remaining questions.
