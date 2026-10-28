<h3 class="policy-selection-info">Select a policy to set a context</h3>

Policies on the platform for **{{ sector }}** sector in **{{ region }}**, **{{ country }}**

<ol>
{% for policy in policies %}
<li>{{ policy.title }} <span class="hazard-evidence-label {% if policy.document_available %}hazard-evidence-label--provided{% else %}hazard-evidence-label--not-provided{% endif %}">Policy document: {% if policy.document_available %}Available{% else %}Not available{% endif %}</span>{% if policy.document_url %} <a class="policy-document-link" href="{{ policy.document_url }}">View document</a>{% endif %}{% if policy.description %}<br><small>{{ policy.description }}</small>{% endif %}</li>
{% endfor %}
</ol>

If you are not able to find the policy linked to your context from the above list click on "Add a new policy" to add the policy document for your region (The policy added will be visible to the other users of the system analysing the same geographical context).
