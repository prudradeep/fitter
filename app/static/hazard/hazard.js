const mostRelevantList = document.getElementById("mostRelevant");
const additionalList = document.getElementById("additional");
const mostRelevantEmpty = document.getElementById("mostRelevantEmpty");
const additionalEmpty = document.getElementById("additionalEmpty");

function renderList(list, values, emptyMessage) {
  list.replaceChildren();
  values.forEach((value) => {
    const item = document.createElement("li");
    item.textContent = value;
    list.appendChild(item);
  });
  emptyMessage.hidden = values.length > 0;
}

window.addEventListener("message", (event) => {
  if (event.origin !== window.location.origin || event.data?.type !== "hazard-guide:update") return;
  renderList(mostRelevantList, Array.isArray(event.data.mostRelevant) ? event.data.mostRelevant : [], mostRelevantEmpty);
  renderList(additionalList, Array.isArray(event.data.additional) ? event.data.additional : [], additionalEmpty);
});
