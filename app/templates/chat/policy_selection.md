### Select a policy to set a context

Policies on the platform for **{{ sector }}** sector in **{{ region }}**, **{{ country }}**

{% for policy in policies %}
{{ loop.index }}. {{ policy }}
{% endfor %}
