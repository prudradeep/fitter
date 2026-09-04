<article class="hazard-summary-review">
{% if error_reason %}
<div class="hazard-summary-review-error" role="alert">
  <strong>{{ error_reason }}</strong>
</div>
{% endif %}
<div class="hazard-summary-review-header">
  <span class="hazard-summary-review-eyebrow">Final review</span>
  <h2 class="hazard-summary-review-title">Review Summary</h2>
  <p>Check the generated content before saving this custom hazard.</p>
</div>

<div class="hazard-summary-review-original">
  <span class="hazard-summary-review-label">Hazard to be co-created</span>
  <p><strong>{{ hazard }}</strong></p>
</div>

<div class="hazard-summary-review-generated">
{% if generated_title and generated_title != hazard %}
  <div class="hazard-summary-review-field">
    <span class="hazard-summary-review-label">Generated title</span>
    <h3 class="hazard-summary-review-generated-title">{{ generated_title }}</h3>
  </div>
{% endif %}
  <div class="hazard-summary-review-field hazard-summary-review-summary">
    <span class="hazard-summary-review-label">Generated summary</span>
    <p>{{ summary }}</p>
  </div>
</div>

<div class="hazard-summary-review-action">
  <span class="hazard-summary-review-action-icon" aria-hidden="true">✓</span>
  <div>
    <strong>Ready to save?</strong>
    <p>Choose <strong>Continue</strong> to confirm and save. To make changes, enter revision instructions or additional context in the text area.</p>
  </div>
</div>
</article>
