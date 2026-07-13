(function () {
  function downloadJson(filename, payload) {
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }

  async function saveJsonFile(filename, payload) {
    const json = JSON.stringify(payload, null, 2);
    const blob = new Blob([json], { type: "application/json" });
    const canPickSavePath =
      typeof window.showSaveFilePicker === "function"
      && !navigator.webdriver;
    if (!canPickSavePath) {
      downloadJson(filename, payload);
      return "download";
    }

    const handle = await window.showSaveFilePicker({
      suggestedName: filename,
      types: [
        {
          description: "JSON files",
          accept: { "application/json": [".json"] },
        },
      ],
    });
    const writable = await handle.createWritable();
    await writable.write(blob);
    await writable.close();
    return "saved";
  }

  function exportFilename(title = "session") {
    const safeTitle = String(title || "session")
      .trim()
      .replace(/[^a-z0-9_-]+/gi, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 80) || "session";
    return `dr-transition-${safeTitle}-${new Date().toISOString().slice(0, 10)}.json`;
  }

  window.DrTransitionSessionExport = {
    downloadJson,
    exportFilename,
    saveJsonFile,
  };
}());
