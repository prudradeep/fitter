{% if error_reason %}
**{{ error_reason }}**

{% endif %}
## Review Summary

Hazard to be co-created:

- **{{ hazard }}**

{% if generated_title and generated_title != hazard %}
**Generated title:** {{ generated_title }}

{% endif %}
### Generated summary

{{ summary }}

Choose **Continue** to confirm this summary and save the hazard. To change it, enter instructions or additional context in the text area; the revised summary will return here for confirmation.
