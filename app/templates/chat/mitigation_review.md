## Mitigation Review

Selected hazard:

- **{{ hazard }}**

Mitigation measure:

- **{{ mitigation_measure }}**

Reason:

{{ reason }}

{% if target_population %}
Target population:

- **{{ target_population }}**
{% endif %}

{% if show_target_population_venn %}
<div
  class="mitigation-venn-chart js-mitigation-venn-chart"
  data-affected='{{ affected_target_population_json | e }}'
  data-mitigation='{{ mitigation_target_population_json | e }}'
  role="img"
  aria-label="Venn diagram comparing affected-profile and mitigation target populations"
></div>
<div class="mitigation-venn-populations" aria-label="Target populations shown in the Venn diagram">
  <div class="mitigation-venn-population-list mitigation-venn-population-list--affected">
    <strong>Hazard profiles target population</strong>
    <ul>
      {% for population in affected_target_populations %}
      <li>{{ population | e }}</li>
      {% endfor %}
    </ul>
  </div>
  <div class="mitigation-venn-population-list mitigation-venn-population-list--measure">
    <strong>Mitigation measure target population</strong>
    <ul>
      {% for population in mitigation_target_populations %}
      <li>{{ population | e }}</li>
      {% endfor %}
    </ul>
  </div>
</div>
{% endif %}

{{ review }}

Choose **Move to next step** when you are ready to evaluate this mitigation measure.
