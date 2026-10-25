for (const image of document.querySelectorAll("img[data-playlist-cover]")) {
  image.addEventListener("error", () => image.remove(), { once: true });
}

for (const opener of document.querySelectorAll("[data-open-playlist-notes]")) {
  const dialog = document.getElementById(opener.dataset.openPlaylistNotes);
  if (!dialog) continue;
  opener.addEventListener("click", () => dialog.showModal());
  dialog.querySelector("[data-close-playlist-notes]")
    .addEventListener("click", () => dialog.close());
  dialog.addEventListener("close", () => opener.focus({ preventScroll: true }));
}

const artistDialog = document.querySelector("#artist-playlist-dialog");

if (artistDialog) {
  const artistSearch = artistDialog.querySelector("[data-artist-search]");
  let artistOptions = Array.from(artistDialog.querySelectorAll("[data-artist-option]"));
  const artistForm = artistDialog.querySelector("form");
  const refreshStatus = artistDialog.querySelector("[data-artist-refresh-status]");
  let lastOpener = document.querySelector("[data-open-artist-dialog]");
  let submitting = false;
  let refreshController = null;
  let refreshTask = null;
  let refreshedInPlace = false;

  function updateSelection() {
    const count = artistForm.querySelectorAll('input[name="artists"]:checked').length;
    artistDialog.querySelector("[data-artist-selection-count]").textContent =
      `${count} ${count === 1 ? "artista selecionado" : "artistas selecionados"}`;
  }

  function searchArtists() {
    const normalize = value => value.normalize("NFD").replace(/\p{M}/gu, "").toLocaleLowerCase("pt-BR");
    const query = normalize(artistSearch.value.trim());
    let visible = 0;
    for (const option of artistOptions) {
      option.hidden = !normalize(option.dataset.artistName).includes(query);
      if (!option.hidden) visible++;
    }
    artistDialog.querySelector("[data-artist-no-results]").hidden = visible !== 0;
  }

  function showRefreshStatus(message, error = false) {
    refreshStatus.textContent = message;
    refreshStatus.hidden = false;
    refreshStatus.classList.toggle("feedback-error", error);
    refreshStatus.setAttribute("role", error ? "alert" : "status");
  }

  async function refreshArtists(submit) {
    const buttons = Array.from(artistForm.querySelectorAll('button[type="submit"]'));
    const originalLabel = submit.textContent;
    for (const button of buttons) button.disabled = true;
    submit.textContent = "Atualizando...";
    artistForm.setAttribute("aria-busy", "true");
    showRefreshStatus("Atualizando músicas e artistas...");
    refreshController = new AbortController();
    try {
      const body = new URLSearchParams();
      new FormData(artistForm).forEach((value, name) => {
        if (typeof value === "string" && ["csrf", "revision", "artists"].includes(name)) {
          body.append(name, value);
        }
      });
      const response = await fetch(submit.formAction, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Accept": "application/json" },
        body,
        signal: refreshController.signal,
      });
      if (!response.headers.get("content-type")?.includes("application/json")) {
        throw new Error("Invalid refresh response");
      }
      const result = await response.json();
      if (typeof result.message !== "string" || typeof result.html !== "string" ||
          typeof result.success !== "boolean" || typeof result.revision !== "string" ||
          !Array.isArray(result.selected_artists) ||
          !result.selected_artists.every(key => typeof key === "string")) {
        throw new Error("Invalid refresh response");
      }
      const incoming = new DOMParser().parseFromString(result.html, "text/html");
      const incomingOptions = incoming.querySelector(".artist-options");
      const savedSelection = new Set(result.selected_artists);
      if (result.success && !incomingOptions) throw new Error("Missing refreshed artists");
      if (result.success) {
        const options = artistDialog.querySelector(".artist-options");
        const scrollTop = options.scrollTop;
        const activeKey = document.activeElement.matches('input[name="artists"]')
          ? document.activeElement.value : null;
        const currentSelection = new Map(
          Array.from(artistForm.querySelectorAll('input[name="artists"]'))
            .map(input => [input.value, input.checked])
        );
        const newKeys = new Set();
        for (const input of incomingOptions.querySelectorAll('input[name="artists"]')) {
          newKeys.add(input.value);
          input.defaultChecked = savedSelection.has(input.value);
          input.checked = currentSelection.has(input.value)
            ? currentSelection.get(input.value) : savedSelection.has(input.value);
        }
        options.replaceChildren(...Array.from(incomingOptions.childNodes));
        artistOptions = Array.from(options.querySelectorAll("[data-artist-option]"));
        options.scrollTop = scrollTop;
        searchArtists();
        updateSelection();
        if (activeKey) {
          options.querySelector(`input[value="${CSS.escape(activeKey)}"]`)
            ?.focus({ preventScroll: true });
        }
        const removed = Array.from(currentSelection)
          .filter(([key, checked]) => checked && !newKeys.has(key)).length;
        if (removed && !result.message.includes("nao estao mais")) {
          result.message += " Alguns artistas marcados saíram da coleção. Revise antes de configurar.";
        }
      }
      for (const input of artistForm.querySelectorAll('input[name="artists"]')) {
        const checked = input.checked;
        input.defaultChecked = savedSelection.has(input.value);
        input.checked = checked;
      }
      const revision = artistForm.querySelector('input[name="revision"]');
      revision.value = result.revision;
      revision.defaultValue = result.revision;
      const primary = artistForm.querySelector("[data-artist-save-submit]");
      const incomingPrimary = incoming.querySelector("[data-artist-save-submit]");
      if (incomingPrimary) {
        primary.textContent = incomingPrimary.textContent;
        primary.removeAttribute("name");
        primary.removeAttribute("value");
      }
      artistDialog.querySelector(".playlist-conflicts")?.remove();
      artistDialog.querySelectorAll(".feedback:not([data-artist-refresh-status])")
        .forEach(feedback => feedback.remove());
      artistDialog.removeAttribute("data-playlist-conflicts");
      artistDialog.removeAttribute("data-artist-draft");
      refreshedInPlace = true;
      showRefreshStatus(result.message, !response.ok || !result.success);
    } catch (error) {
      if (error.name === "AbortError") {
        refreshStatus.hidden = true;
      } else {
        showRefreshStatus(
          "Não foi possível atualizar. A lista anterior foi mantida. Tente novamente ou reabra a configuração.",
          true
        );
      }
    } finally {
      refreshController = null;
      submitting = false;
      artistForm.removeAttribute("aria-busy");
      for (const button of buttons) button.disabled = false;
      submit.textContent = originalLabel;
      if (artistDialog.open && !artistDialog.contains(document.activeElement)) {
        submit.focus({ preventScroll: true });
      }
    }
  }

  async function startRefresh(submit) {
    if (submitting || !artistDialog.open) return;
    submitting = true;
    const task = refreshArtists(submit);
    refreshTask = task;
    try {
      await task;
    } finally {
      if (refreshTask === task) refreshTask = null;
    }
  }

  for (const opener of document.querySelectorAll("[data-open-artist-dialog]")) {
    opener.addEventListener("click", async () => {
      if (artistDialog.open) return;
      lastOpener = opener;
      artistForm.reset();
      artistSearch.value = "";
      searchArtists();
      updateSelection();
      artistDialog.showModal();
      if (refreshTask) await refreshTask;
      await startRefresh(artistForm.querySelector("[data-artist-refresh-submit]"));
    });
  }
  for (const closer of artistDialog.querySelectorAll("[data-close-artist-dialog]")) {
    closer.addEventListener("click", () => artistDialog.close());
  }
  artistDialog.addEventListener("keydown", event => {
    if (event.key === "Escape") {
      event.preventDefault();
      artistDialog.close();
    }
  });
  artistDialog.addEventListener("close", () => {
    artistForm.reset();
    updateSelection();
    if (!refreshedInPlace && (artistDialog.hasAttribute("data-playlist-conflicts") ||
        artistDialog.hasAttribute("data-artist-draft"))) {
      window.location.assign("/app/playlists");
    } else {
      lastOpener.focus({ preventScroll: true });
    }
  });
  window.addEventListener("pagehide", () => refreshController?.abort());
  artistDialog.querySelector("[data-select-all-artists]").addEventListener("click", () => {
    for (const input of artistForm.querySelectorAll('input[name="artists"]')) input.checked = true;
    updateSelection();
  });
  artistDialog.querySelector("[data-clear-artists]").addEventListener("click", () => {
    for (const input of artistForm.querySelectorAll('input[name="artists"]')) input.checked = false;
    updateSelection();
  });
  artistSearch.addEventListener("input", searchArtists);
  artistSearch.addEventListener("keydown", event => {
    if (event.key === "Enter") event.preventDefault();
  });
  artistForm.addEventListener("keydown", event => {
    if (event.key === "Enter" && event.target.matches('input[name="artists"]')) {
      event.preventDefault();
      artistForm.requestSubmit(artistForm.querySelector("[data-artist-save-submit]"));
    }
  });
  artistForm.addEventListener("change", updateSelection);
  artistForm.addEventListener("submit", async event => {
    const submit = event.submitter;
    const refreshing = submit?.hasAttribute("data-artist-refresh-submit");
    if (refreshing) {
      event.preventDefault();
      await startRefresh(submit);
      return;
    }
    if (submitting) {
      event.preventDefault();
      return;
    }
    submitting = true;
    if (submit?.name) {
      const confirmation = document.createElement("input");
      confirmation.type = "hidden";
      confirmation.name = submit.name;
      confirmation.value = submit.value;
      artistForm.append(confirmation);
    }
    for (const button of artistForm.querySelectorAll('button[type="submit"]')) button.disabled = true;
    if (submit) submit.textContent = "Validando e salvando...";
  });
  updateSelection();
  if (artistDialog.hasAttribute("data-open-on-load")) artistDialog.showModal();
}
