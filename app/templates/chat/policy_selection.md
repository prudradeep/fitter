### Select a policy to set a context

Policies on the platform for **{{ sector }}** sector in **{{ region }}**, **{{ country }}**

<ol>
{% for policy in policies %}
<li><strong>{{ policy.title }}</strong> <span class="hazard-evidence-label {% if policy.document_available %}hazard-evidence-label--provided{% else %}hazard-evidence-label--not-provided{% endif %}">Policy document: {% if policy.document_available %}Available{% else %}Not available{% endif %}</span>{% if policy.document_url %} <a href="{{ policy.document_url }}">View document</a>{% endif %}{% if policy.description %}<br><small>{{ policy.description }}</small>{% endif %}</li>
{% endfor %}
</ol>
