### Select a policy to set a context

Policies on the platform for **{{ sector }}** sector in **{{ region }}**, **{{ country }}**

<ol>
{% for policy in policies %}
<li><strong>{{ policy.title }}</strong> <span class="hazard-evidence-label {% if policy.document_available %}hazard-evidence-label--provided{% else %}hazard-evidence-label--not-provided{% endif %}">Policy document: {% if policy.document_available %}Available{% else %}Not available{% endif %}</span>{% if policy.description %}<br><small>{{ policy.description }}</small>{% endif %}</li>
{% endfor %}
</ol>
