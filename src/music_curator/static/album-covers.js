function showCoverError(image) {
  image.hidden = true;
  const status = image.closest(".track-info").querySelector("[data-cover-status]");
  status.hidden = false;
}

for (const image of document.querySelectorAll("img[data-album-cover]")) {
  image.addEventListener("error", () => showCoverError(image), { once: true });
  if (image.complete && image.naturalWidth === 0) {
    showCoverError(image);
  }
}
