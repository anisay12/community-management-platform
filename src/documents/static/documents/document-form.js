// Document form: the minimum role to read only matters for a restricted document, so its
// field is hidden while "Everyone who can read the community" is selected. Without
// JavaScript the field stays visible (its help text explains when it applies).
document.addEventListener("DOMContentLoaded", () => {
  const form = document.querySelector("[data-documents-form]");
  if (!form) return;
  const select = form.querySelector("[data-documents-min-role]");
  const radios = form.querySelectorAll('input[name="visibility"]');
  if (!select || !radios.length) return;
  const wrapper = select.closest(".tl-field") || select.parentElement;
  const sync = () => {
    const checked = form.querySelector('input[name="visibility"]:checked');
    wrapper.hidden = !checked || checked.value !== "restricted";
  };
  radios.forEach((radio) => radio.addEventListener("change", sync));
  sync();
});
