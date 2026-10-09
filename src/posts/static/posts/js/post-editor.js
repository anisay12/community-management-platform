// Post editor and post actions (progressive enhancement: every feature works without it).
// - [data-tl-confirm="<modal id>"] on a submit button opens that confirmation modal instead
//   of submitting (without JavaScript the server answers a confirmation page).
// - [data-tl-counter="<id>"] on a textarea shows "n / max characters" in that element.
// - form[data-tl-single-submit] disables its submit buttons after the first submission.
(() => {
  if (window.tlPostEditorLoaded) return;
  window.tlPostEditorLoaded = true;

  document.addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-tl-confirm]");
    if (!trigger || !window.bootstrap) return;
    const modal = document.getElementById(trigger.dataset.tlConfirm);
    if (!modal) return;
    event.preventDefault();
    bootstrap.Modal.getOrCreateInstance(modal).show(trigger);
  });

  document.addEventListener("shown.bs.modal", (event) => {
    const field = event.target.querySelector("textarea, input:not([type=hidden])");
    if (field) field.focus();
  });

  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!form.matches("[data-tl-single-submit]")) return;
    // Disable after the browser has collected the submitter's name and value.
    window.setTimeout(() => {
      form.querySelectorAll("button[type=submit]").forEach((button) => {
        button.disabled = true;
      });
    }, 0);
  });

  const updateCounter = (textarea) => {
    const output = document.getElementById(textarea.dataset.tlCounter);
    if (!output) return;
    const template = output.dataset.tlCounterTemplate || "__count__ / __max__";
    output.textContent = template
      .replace("__count__", textarea.value.length)
      .replace("__max__", textarea.maxLength);
  };

  const initCounters = () => {
    document.querySelectorAll("textarea[data-tl-counter]").forEach((textarea) => {
      updateCounter(textarea);
      textarea.addEventListener("input", () => updateCounter(textarea));
    });
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initCounters);
  } else {
    initCounters();
  }
})();
