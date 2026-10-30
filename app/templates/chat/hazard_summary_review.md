<article class="hazard-summary-review">
{% if error_reason %}
<div class="hazard-summary-review-error" role="alert">
  <strong>{{ error_reason }}</strong>
</div>
{% endif %}
<div class="hazard-summary-review-header">
  <span class="hazard-summary-review-eyebrow">Final review</span>
  <h2 class="hazard-summary-review-title">Review hazard title and summary</h2>
  <p>Review the proposed title and summary. Confirm them or choose a revision option.</p>
</div>

<div class="hazard-summary-review-original">
  <span class="hazard-summary-review-label">Hazard to be created</span>
  <p><strong>{{ hazard }}</strong></p>
</div>

<div class="hazard-summary-review-generated">
  <div class="hazard-summary-review-field">
    <span class="hazard-summary-review-label">Generated title</span>
    <h3 class="hazard-summary-review-generated-title">{{ generated_title }}</h3>
  </div>
  <div class="hazard-summary-review-field hazard-summary-review-summary">
    <span class="hazard-summary-review-label">Generated summary</span>
    <p>{{ summary }}</p>
  </div>
</div>

<div class="hazard-summary-review-action">
  <span class="hazard-summary-review-action-icon" aria-hidden="true">✓</span>
  <div>
    <strong>Does this title and summary describe your hazard?</strong>
    <p>Confirm and save, or choose a revision option and enter the change you want in the text area.</p>
  </div>
</div>
</article>
