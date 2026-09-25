### Select a policy

Policy context: **{{ country }}** - **{{ region }}** - **{{ sector }}**

{% for policy in policies %}
{{ loop.index }}. {{ policy }}
{% endfor %}
