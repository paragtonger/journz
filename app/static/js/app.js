const editor = document.querySelector("[data-autosave-url]");
const permanentDeleteForms = document.querySelectorAll("[data-confirm-delete]");

if (
  document.documentElement.dataset.theme === "system" &&
  window.matchMedia("(prefers-color-scheme: dark)").matches
) {
  document.documentElement.classList.add("system-dark");
}

permanentDeleteForms.forEach((form) => {
  form.addEventListener("submit", (event) => {
    if (!window.confirm("Permanently delete this journal? This cannot be undone.")) {
      event.preventDefault();
    }
  });
});

if (editor) {
  const title = editor.querySelector("[name=title]");
  const content = editor.querySelector("[name=content]");
  const status = editor.querySelector("[data-save-status]");
  let timer;
  let saving = false;
  let dirty = false;
  let retryCount = 0;
  const draftKey = `journz-draft-${editor.dataset.autosaveUrl}`;
  const draft = localStorage.getItem(draftKey);
  if (draft) {
    try {
      const savedDraft = JSON.parse(draft);
      if (savedDraft.title !== title.value || savedDraft.content !== content.value) {
        status.textContent = "Recovered unsaved draft";
        title.value = savedDraft.title;
        content.value = savedDraft.content;
        dirty = true;
      }
    } catch {
      localStorage.removeItem(draftKey);
    }
  }

  const save = async () => {
    if (saving || !dirty) return;
    saving = true;
    dirty = false;
    status.textContent = "Saving...";
    try {
      const response = await fetch(editor.dataset.autosaveUrl, {
        method: "PUT",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": document.cookie
            .split("; ")
            .find((row) => row.startsWith("journz_csrf="))
            ?.split("=")[1] || "",
        },
        body: JSON.stringify({
          title: title.value,
          content: content.value,
          version: Number(editor.querySelector("[name=version]").value),
        }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Unable to save.");
      editor.querySelector("[name=version]").value = result.version;
      status.textContent = "Saved";
      retryCount = 0;
      localStorage.removeItem(draftKey);
    } catch (error) {
      status.textContent = error.message;
      localStorage.setItem(draftKey, JSON.stringify({title: title.value, content: content.value}));
      if (retryCount < 1) {
        retryCount += 1;
        setTimeout(save, 2000);
      }
    } finally {
      saving = false;
      if (dirty) save();
    }
  };

  editor.addEventListener("input", () => {
    dirty = true;
    localStorage.setItem(draftKey, JSON.stringify({title: title.value, content: content.value}));
    status.textContent = "Unsaved";
    clearTimeout(timer);
    timer = setTimeout(save, 900);
  });

  window.addEventListener("beforeunload", (event) => {
    if (dirty) {
      event.preventDefault();
      event.returnValue = "";
    }
  });
}
