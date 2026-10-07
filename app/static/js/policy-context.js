const policyContextMount = document.querySelector("#policyContextMount");
const selectedCountry = new URLSearchParams(window.location.search).get("country")?.trim() || "";
const selectedRegion = new URLSearchParams(window.location.search).get("region")?.trim() || "";
const selectedLocation = [selectedRegion, selectedCountry].filter(Boolean).join(", ")
  || "your region and country";

document.querySelectorAll("[data-selected-location]").forEach((element) => {
  element.textContent = selectedLocation;
});
document.querySelectorAll("[data-selected-region]").forEach((element) => {
  element.textContent = selectedRegion || "your region";
});
if (selectedRegion || selectedCountry) {
  document.title = `${document.title} — ${selectedLocation}`;
}

if (policyContextMount) {
  fetch("/static/policy-context.html")
    .then((response) => {
      if (!response.ok) throw new Error(`Policy context request failed: ${response.status}`);
      return response.text();
    })
    .then((html) => {
      const template = document.createElement("template");
      template.innerHTML = html;
      policyContextMount.replaceWith(template.content);
    })
    .catch((error) => {
      console.error(error);
      policyContextMount.textContent = "Policy guidance is temporarily unavailable.";
    });
}
