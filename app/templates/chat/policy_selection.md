<h3 class="policy-selection-info">Select a policy to set a context</h3>

Policies on the platform for **{{ sector }}** sector in **{{ region }}**, **{{ country }}**

<ol>
{% for policy in policies %}
<li>{{ policy.title }} <span class="hazard-evidence-label {% if policy.document_available %}hazard-evidence-label--provided{% else %}hazard-evidence-label--not-provided{% endif %}">Policy document: {% if policy.document_available %}Available{% else %}Not available{% endif %}</span>{% if policy.document_url %} <a class="policy-document-link" href="{{ policy.document_url }}">View document</a>{% endif %}{% if policy.description %}<br><small>{{ policy.description }}</small>{% endif %}</li>
{% endfor %}
</ol>

<div class="policy-selection-note">
<strong>Don't see your policy?</strong>
<p>Select <strong>Add a new policy</strong> below to provide its document. The policy will be visible to others working in the same region.</p>
</div>
