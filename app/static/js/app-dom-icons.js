function createSvgPathElement(pathData) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", String(pathData || ""));
  svg.appendChild(path);
  return svg;
}

function stageIconElement(pathData) {
  return createElement("span", { className: "stage-icon", attrs: { "aria-hidden": "true" } }, [
    createSvgPathElement(pathData),
  ]);
}

function svgPathIconElement(className, pathData) {
  return createElement("span", { className, attrs: { "aria-hidden": "true" } }, [
    createSvgPathElement(pathData),
  ]);
}
