const artistDialog = document.querySelector("#artist-playlist-dialog");

if (artistDialog) {
  const artistSearch = artistDialog.querySelector("[data-artist-search]");
  const artistOptions = Array.from(artistDialog.querySelectorAll("[data-artist-option]"));
  const artistForm = artistDialog.querySelector("form");
  const enabledToggle = artistDialog.querySelector("[data-artist-enabled]");
  const settingsFields = artistDialog.querySelector("[data-artist-settings-fields]");
  let lastOpener = document.querySelector("[data-open-artist-dialog]");

  function updateSelection() {
    const count = artistForm.querySelectorAll('input[name="artists"]:checked').length;
    artistDialog.querySelector("[data-artist-selection-count]").textContent =
      `${count} ${count === 1 ? "artista selecionado" : "artistas selecionados"}`;
    settingsFields.disabled = !enabledToggle.checked;
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

  for (const opener of document.querySelectorAll("[data-open-artist-dialog]")) {
    opener.addEventListener("click", () => {
      lastOpener = opener;
      artistForm.reset();
      artistSearch.value = "";
      searchArtists();
      updateSelection();
      artistDialog.showModal();
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
  artistDialog.addEventListener("close", () => lastOpener.focus({ preventScroll: true }));
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
  artistForm.addEventListener("change", updateSelection);
  enabledToggle.addEventListener("change", updateSelection);
  artistForm.addEventListener("submit", () => {
    const submit = artistDialog.querySelector("[data-artist-save-submit]");
    submit.disabled = true;
    submit.textContent = "Salvando...";
  });
  updateSelection();
  if (artistDialog.hasAttribute("data-open-on-load")) artistDialog.showModal();
}
