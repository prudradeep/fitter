You have successfully co-created a hazard.

Hazard to be co-created:

- **{{ original_hazard or hazard }}**

{% if original_hazard and original_hazard != hazard %}
**Generated title:** {{ hazard }}

{% endif %}

{% if visibility_notice %}
> {{ visibility_notice }}

{% endif %}
**Reason:** {{ reason }}

**Evidence:** {{ evidence }}

## Affected Population Groups

{{ affected_population_groups }}

Choose what you want to do next.
