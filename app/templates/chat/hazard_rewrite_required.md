**{{ hazard }}**

{{ reason }}

{% if rewrite_suggestion %}
Suggested rewrite direction:

{{ rewrite_suggestion }}

{% endif %}
{% if has_suggestions %}
Existing hazards that may already cover this scope:

{{ suggestions }}

{% endif %}
