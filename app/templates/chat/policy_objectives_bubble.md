<div class="policy-objectives-heading">
  <span>FITTER analyses specific policy objectives within each sector. These objectives represent key transition priorities aligned with EU twin-transition strategies and relevant national sector strategies. We are now focusing on <strong>{{ region }}</strong>. Please select the sector to explore its implications for different population groups</span>
  <span class="policy-objectives-info" tabindex="0" aria-label="Why these policy objectives?">
    <span class="policy-objectives-info-icon" aria-hidden="true">ⓘ</span>
    <div class="policy-objectives-info-bubble"><strong>Why these policy objectives?</strong><br> The policy objectives were selected through the FITTER scenario-building process. They were identified based on their alignment with EU twin-transition strategies and their presence in the relevant national sector strategies. This provides a common basis for assessing how specific transition pathways may affect different population groups</div>
  </span>
</div>

<table class="policy-objectives-table">
  <thead>
    <tr><th scope="col">Sector</th><th scope="col">Policy objective</th></tr>
  </thead>
  <tbody>
    {% for sector in sectors %}
    {% if sector == "Energy" %}<tr><td>Energy</td><td>Transition towards renewable energy</td></tr>{% endif %}
    {% if sector == "Housing" %}<tr><td>Housing &amp; Built Environment</td><td>Adaptation of housing to climate change</td></tr>{% endif %}
    {% if sector == "Transport" %}<tr><td>Transport &amp; Mobility</td><td>Transition to electric vehicles</td></tr>{% endif %}
    {% endfor %}
  </tbody>
</table>
