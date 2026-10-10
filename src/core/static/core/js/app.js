// Message toasts are visible without JavaScript; here they get an 8 second auto-hide.
// Bootstrap pauses the timer while a toast is hovered or holds keyboard focus.
// Toasts appended later by an HTMX response (out-of-band swap) get the same treatment.
const tlShowToasts = (root) => {
  root.querySelectorAll(".toast:not([data-tl-toast])").forEach((element) => {
    element.dataset.tlToast = "1";
    new bootstrap.Toast(element, { autohide: true, delay: 8000 }).show();
  });
};
document.addEventListener("DOMContentLoaded", () => tlShowToasts(document));
document.addEventListener("htmx:oobAfterSwap", () => tlShowToasts(document));
