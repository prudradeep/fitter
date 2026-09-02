{% if error_reason %}
**{{ error_reason }}**

{% endif %}
## Review Affected Population Groups

Hazard to be co-created:

- **{{ hazard }}**

{% if generated_title %}
**Generated title:** {{ generated_title }}

{% endif %}
{% if visibility_notice %}
> {{ visibility_notice }}

{% endif %}
Affected population groups identified:

{{ profiles }}

Choose **Confirm affected groups** if this looks right, or type what to add or remove.

Examples:

- `Remove General Population`
- `Add low-income renters, elderly adults`
- `Remove tenants and add households with utility arrears`
