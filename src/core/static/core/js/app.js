// Message toasts are visible without JavaScript; here they get an 8 second auto-hide.
// Bootstrap pauses the timer while a toast is hovered or holds keyboard focus.
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".toast").forEach((element) => {
    new bootstrap.Toast(element, { autohide: true, delay: 8000 }).show();
  });
});
