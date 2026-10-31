<h3 class="policy-selection-info">Select a policy to set a context</h3>

Policies on the platform for **{{ sector }}** sector in **{{ country }}** that may be linked to **{{ region }}**.

<ol>
{% for policy in policies %}
<li>{% if policy.document_url %}<a href="{{ policy.document_url }}" class="policy-document-link">{{ policy.title }}</a>{% else %}{{ policy.title }}{% endif %} <span class="policy-document-status {% if policy.document_available %}policy-document-status--available{% else %}policy-document-status--unavailable{% endif %}">{% if policy.document_available %}Document available{% else %}Document unavailable{% endif %}</span>{% if policy.description %}<br><small>{{ policy.description }}</small>{% endif %}</li>
{% endfor %}
</ol>

<div class="policy-selection-note">
<strong>Don't see a policy relevant for your region?</strong>
<p>Select <strong>Add a new policy</strong> below to provide its document. The policy will be visible to other platform users working in the same regional context.</p>
</div>
