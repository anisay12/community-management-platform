// "@first.last" suggestions for a textarea carrying data-mention-url and aria-controls (the
// id of a role="listbox" list). The textarea keeps its textbox role (a textarea cannot be a
// combobox, so no aria-expanded): aria-autocomplete="list" and aria-activedescendant tell
// assistive technologies about the list, and a polite status announces how many members
// match. While the word before the caret starts with "@", the list is filled through htmx
// with the community members matching it. Keyboard: ArrowDown/ArrowUp
// move through the options (aria-activedescendant), Enter or Tab insert the handle, Escape
// closes the list. Without JavaScript, members are mentioned by typing the handle in full.
(() => {
  const TOKEN = /(^|[\s(])@([\w.-]{1,60})$/u;

  const setup = (textarea) => {
    const list = document.getElementById(textarea.getAttribute("aria-controls"));
    if (!list || !window.htmx) return;
    let active = -1;
    let timer = null;

    const options = () => Array.from(list.querySelectorAll("[role=option]"));
    const close = () => {
      list.hidden = true;
      list.innerHTML = "";
      active = -1;
      textarea.removeAttribute("aria-activedescendant");
    };
    const highlight = (index) => {
      const items = options();
      items.forEach((item, position) => {
        item.setAttribute("aria-selected", position === index ? "true" : "false");
        item.classList.toggle("active", position === index);
      });
      active = index;
      if (items[index]) textarea.setAttribute("aria-activedescendant", items[index].id);
    };
    const currentToken = () => {
      const before = textarea.value.slice(0, textarea.selectionStart);
      const match = before.match(TOKEN);
      return match ? match[2] : null;
    };
    const insert = (option) => {
      const caret = textarea.selectionStart;
      const before = textarea.value.slice(0, caret).replace(/@[\w.-]*$/u, `@${option.dataset.handle} `);
      textarea.value = before + textarea.value.slice(caret);
      textarea.selectionStart = textarea.selectionEnd = before.length;
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
      close();
      textarea.focus();
    };

    textarea.setAttribute("aria-autocomplete", "list");
    const status = document.getElementById(list.dataset.statusId || "");
    const announce = (count) => {
      if (!status) return;
      const template = count === 1 ? status.dataset.one : status.dataset.many;
      status.textContent = count ? (template || "").replace("__count__", count) : "";
    };

    textarea.addEventListener("input", () => {
      window.clearTimeout(timer);
      const token = currentToken();
      if (!token) {
        close();
        return;
      }
      timer = window.setTimeout(() => {
        const url = `${textarea.dataset.mentionUrl}?q=${encodeURIComponent(token)}`;
        htmx.ajax("GET", url, { target: list, swap: "innerHTML" }).then(() => {
          const count = options().length;
          list.hidden = count === 0;
          announce(count);
          active = -1;
        });
      }, 200);
    });

    textarea.addEventListener("keydown", (event) => {
      if (list.hidden) return;
      const items = options();
      if (event.key === "ArrowDown") {
        event.preventDefault();
        highlight((active + 1) % items.length);
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        highlight((active - 1 + items.length) % items.length);
      } else if ((event.key === "Enter" || event.key === "Tab") && items[active]) {
        event.preventDefault();
        insert(items[active]);
      } else if (event.key === "Escape") {
        event.preventDefault();
        close();
      }
    });

    list.addEventListener("mousedown", (event) => {
      const option = event.target.closest("[role=option]");
      if (option) {
        event.preventDefault();
        insert(option);
      }
    });
    textarea.addEventListener("blur", () => window.setTimeout(close, 150));
  };

  const init = () => document.querySelectorAll("textarea[data-mention-url]").forEach(setup);
  // This deferred script may run before htmx (loaded at the end of the page): wait for it.
  if (window.htmx && document.readyState !== "loading") {
    init();
  } else {
    window.addEventListener("load", init, { once: true });
  }
})();
