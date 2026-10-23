function normalizeOption(text) {
  return text.normalize("NFD").replace(/\p{M}/gu, "").toLocaleLowerCase("pt-BR");
}

for (const select of document.querySelectorAll("select[data-searchable-select]")) {
  const label = document.querySelector(`label[for="${select.id}"]`);
  const wrapper = document.createElement("div");
  wrapper.className = "searchable-select";
  const trigger = document.createElement("button");
  trigger.type = "button";
  trigger.className = "select-trigger";
  trigger.id = `${select.id}-trigger`;
  trigger.setAttribute("aria-expanded", "false");
  const panel = document.createElement("div");
  panel.id = `${select.id}-panel`;
  panel.className = "select-search-panel";
  panel.hidden = true;
  trigger.setAttribute("aria-controls", panel.id);
  const search = document.createElement("input");
  search.type = "search";
  search.placeholder = `Pesquisar ${label.textContent.toLocaleLowerCase("pt-BR")}`;
  search.setAttribute("aria-label", `Pesquisar opções de ${label.textContent}`);
  search.setAttribute("role", "combobox");
  search.setAttribute("aria-autocomplete", "list");
  search.setAttribute("aria-expanded", "false");
  search.autocomplete = "off";
  const list = document.createElement("div");
  list.id = `${select.id}-options`;
  list.className = "select-options";
  list.tabIndex = -1;
  list.setAttribute("role", "listbox");
  list.setAttribute("aria-label", label.textContent);
  search.setAttribute("aria-controls", list.id);
  const status = document.createElement("p");
  status.className = "select-options-status";
  status.setAttribute("role", "status");
  const options = Array.from(select.options);
  let visible = [];
  let active = -1;

  function close(restoreFocus = false) {
    panel.hidden = true;
    trigger.setAttribute("aria-expanded", "false");
    search.setAttribute("aria-expanded", "false");
    search.removeAttribute("aria-activedescendant");
    if (restoreFocus) trigger.focus({ preventScroll: true });
  }

  function highlight(index) {
    active = index;
    const rows = list.children;
    for (let i = 0; i < rows.length; i++) {
      rows[i].classList.toggle("option-active", i === active);
    }
    if (rows[active]) {
      search.setAttribute("aria-activedescendant", rows[active].id);
      const rowRect = rows[active].getBoundingClientRect();
      const listRect = list.getBoundingClientRect();
      if (rowRect.top < listRect.top) list.scrollTop += rowRect.top - listRect.top;
      else if (rowRect.bottom > listRect.bottom) list.scrollTop += rowRect.bottom - listRect.bottom;
    } else {
      search.removeAttribute("aria-activedescendant");
    }
  }

  function choose(option) {
    select.value = option.value;
    trigger.textContent = option.textContent;
    trigger.setAttribute("aria-label", `${label.textContent}: ${option.textContent}`);
    select.dispatchEvent(new Event("change", { bubbles: true }));
    close(true);
  }

  function render() {
    const query = normalizeOption(search.value.trim());
    visible = options.filter(option => normalizeOption(option.textContent).includes(query));
    list.replaceChildren();
    for (const [index, option] of visible.entries()) {
      const row = document.createElement("button");
      row.type = "button";
      row.tabIndex = -1;
      row.id = `${select.id}-option-${index}`;
      row.setAttribute("role", "option");
      row.setAttribute("aria-selected", String(option.value === select.value));
      row.textContent = option.textContent;
      row.addEventListener("click", () => choose(option));
      list.append(row);
    }
    status.textContent = visible.length === 0 ? "Nenhuma opção encontrada."
      : visible.length === 1 ? "1 opção disponível" : `${visible.length} opções disponíveis`;
    highlight(-1);
  }

  function open() {
    search.value = "";
    panel.hidden = false;
    trigger.setAttribute("aria-expanded", "true");
    search.setAttribute("aria-expanded", "true");
    render();
    search.focus({ preventScroll: true });
  }

  trigger.textContent = select.selectedOptions[0].textContent;
  trigger.setAttribute("aria-label", `${label.textContent}: ${trigger.textContent}`);
  trigger.addEventListener("click", () => panel.hidden ? open() : close());
  trigger.addEventListener("keydown", event => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      open();
      highlight(event.key === "ArrowDown" ? 0 : visible.length - 1);
    }
  });
  search.addEventListener("input", render);
  search.addEventListener("keydown", event => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (visible.length) {
        highlight(event.key === "ArrowDown"
          ? (active + 1) % visible.length
          : (active <= 0 ? visible.length : active) - 1);
      }
    } else if (event.key === "Enter") {
      event.preventDefault();
      if (visible[active]) choose(visible[active]);
      else if (visible.length === 1) choose(visible[0]);
    }
  });
  wrapper.addEventListener("keydown", event => {
    if (event.key === "Escape") {
      event.preventDefault();
      close(true);
    }
  });
  wrapper.addEventListener("focusout", () => {
    setTimeout(() => {
      if (!wrapper.contains(document.activeElement)) close();
    }, 0);
  });
  document.addEventListener("click", event => {
    if (!wrapper.contains(event.target)) close();
  });
  panel.append(search, list, status);
  wrapper.append(trigger, panel);
  select.after(wrapper);
  select.hidden = true;
  label.htmlFor = trigger.id;
}
