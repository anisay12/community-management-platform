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

// Side menu: the collapse button switches between full labels and icons only (lg and up),
// and remembers the choice for the next pages.
document.addEventListener("DOMContentLoaded", () => {
  const button = document.querySelector("[data-tl-sidebar-toggle]");
  if (!button) return;
  const root = document.documentElement;
  const sync = () => {
    const collapsed = root.classList.contains("tl-sidebar-collapsed");
    button.setAttribute("aria-pressed", String(collapsed));
  };
  sync();
  button.addEventListener("click", () => {
    const collapsed = root.classList.toggle("tl-sidebar-collapsed");
    try {
      localStorage.setItem("tl-sidebar", collapsed ? "collapsed" : "expanded");
    } catch (error) {
      // Storage blocked: the choice lasts for this page only.
    }
    sync();
  });
});

// Keyboard focus survives HTMX swaps: htmx moves the focus to the new element with the same
// id, but a swap that lands while the browser is still handling the key press can leave it on
// <body>. Remember the focused id before the swap and restore it if the focus was lost.
let tlFocusedId = null;
document.addEventListener("htmx:beforeSwap", () => {
  const active = document.activeElement;
  tlFocusedId = active && active !== document.body && active.id ? active.id : null;
});
document.addEventListener("htmx:afterSettle", () => {
  if (!tlFocusedId) return;
  const id = tlFocusedId;
  tlFocusedId = null;
  const active = document.activeElement;
  if (active && active !== document.body) return;
  const element = document.getElementById(id);
  if (element) element.focus();
});
