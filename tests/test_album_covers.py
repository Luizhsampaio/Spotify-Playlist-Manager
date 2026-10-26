import re

import httpx
import pytest
from fastapi.testclient import TestClient

from music_curator.config import ORIGIN, Settings
from music_curator.demo import PROTOTYPE_TRACKS, DemoCredentialStore, demo_response
from music_curator.models import Album, ManagedPlaylistDetails
from music_curator.spotify import TOKEN_URL
from music_curator.web import create_app


@pytest.mark.parametrize(
    "url",
    [
        "http://i.scdn.co/image/syntheticCover",
        "https://evil.example/image/syntheticCover",
        "https://i.scdn.co.evil.example/image/syntheticCover",
        "https://i.scdn.co@evil.example/image/syntheticCover",
        "https://synthetic-user@i.scdn.co/image/syntheticCover",
        "https://i.scdn.co:443/image/syntheticCover",
        "https://i.scdn.co/image/syntheticCover?token=synthetic-secret",
        "https://i.scdn.co/image/syntheticCover#fragment",
        "https://i.scdn.co/image/../unsafe",
        "https://i.scdn.co/other/syntheticCover",
        "https://i.scdn.co/image/",
        "https://i.scdn.co/image/syntheticCover\n",
        "data:image/svg+xml,synthetic",
        "javascript:synthetic",
        "/static/untrusted.svg",
    ],
)
def test_cover_urls_only_allow_official_https_image_paths(url: str) -> None:
    album = Album.model_validate({"name": "Album sintetico", "images": [{"url": url}]})
    assert album.cover_url is None


def test_cover_uses_smallest_trusted_image_in_spotify_size_order() -> None:
    album = Album.model_validate(
        {
            "name": "Album sintetico",
            "images": [
                {"url": "https://i.scdn.co/image/syntheticLarge", "width": 640, "height": 640},
                {"url": "https://i.scdn.co/image/syntheticSmall", "width": 64, "height": 64},
            ],
        }
    )
    assert album.cover_url == "https://i.scdn.co/image/syntheticSmall"
    assert Album(name="Sem capa").cover_url is None
    assert (
        Album.model_validate(
            {
                "name": "Album sintetico",
                "images": [{"url": album.cover_url}, {"url": "https://evil.example/unsafe"}],
            }
        ).cover_url
        == album.cover_url
    )


def test_managed_playlist_cover_uses_only_trusted_spotify_image_urls() -> None:
    details = ManagedPlaylistDetails.model_validate(
        {
            "id": "syntheticPlaylist",
            "name": "Playlist sintética",
            "public": True,
            "collaborative": False,
            "owner": {"id": "syntheticAccount"},
            "images": [
                {"url": "https://i.scdn.co/image/syntheticLarge", "width": 640, "height": 640},
                {"url": "https://i.scdn.co/image/syntheticSmall", "width": 64, "height": 64},
                {"url": "https://evil.example/synthetic-secret"},
            ],
        }
    )
    assert details.cover_url == "https://i.scdn.co/image/syntheticSmall"

    unsafe = ManagedPlaylistDetails.model_validate(
        {
            "id": "syntheticPlaylist",
            "name": "Playlist sintética",
            "public": True,
            "collaborative": False,
            "owner": {"id": "syntheticAccount"},
            "images": [{"url": "https://evil.example/synthetic-secret"}],
        }
    )
    assert unsafe.cover_url is None


@pytest.mark.parametrize("kind", ["official", "missing", "unsafe", "unavailable"])
def test_cover_rendering_keeps_library_read_only_and_tokens_out_of_images(kind: str) -> None:
    item = PROTOTYPE_TRACKS[0].model_dump(mode="json")
    item["track"]["album"]["images"] = (
        [{"url": "https://i.scdn.co/image/syntheticCover"}]
        if kind == "official"
        else [{"url": "https://evil.example/synthetic-secret"}]
        if kind == "unsafe"
        else []
    )
    if kind == "unavailable":
        item["track"] = None
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if str(request.url) == TOKEN_URL:
            return demo_response(request)
        assert request.method == "GET"
        assert request.url.path == "/v1/me/tracks"
        return httpx.Response(200, json={"items": [item]})

    store = DemoCredentialStore()
    store.save("synthetic-refresh")
    app = create_app(
        Settings(client_id="0" * 32),
        store=store,
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    with TestClient(app, base_url=ORIGIN) as browser:
        page = browser.get("/app/musicas")
        assert page.status_code == 200
        assert "músicas importadas" not in page.text
        csp = page.headers["content-security-policy"]
        assert "img-src 'self' https://i.scdn.co/image/;" in csp
        assert "script-src 'self';" in csp
        assert "unsafe-inline" not in csp
        assert "synthetic-access" not in page.text
        assert "synthetic-refresh" not in page.text
        assert "synthetic-secret" not in page.text
        assert "evil.example" not in page.text
        assert calls == ["/api/token", "/v1/me/tracks"]
        assert '<script src="/static/album-covers.js" defer></script>' in page.text
        if kind == "official":
            assert 'src="https://i.scdn.co/image/syntheticCover"' in page.text
            assert 'loading="lazy"' in page.text
            assert 'referrerpolicy="no-referrer"' in page.text
            assert 'aria-label="Abrir Horizonte de papel no Spotify"' in page.text
            assert 'class="track-cover-status" role="status" hidden' in page.text
        else:
            assert "data-album-cover" not in page.text
            if kind == "unavailable":
                assert "Faixa indisponível" in page.text
            else:
                assert 'role="status" >Capa indisponível</small>' in page.text


def test_demo_covers_are_local_and_remote_image_csp_stays_closed(prototype: TestClient) -> None:
    login = prototype.get("/entrar")
    match = re.search(r'name="csrf" value="([^"]+)"', login.text)
    assert match
    prototype.post("/demo/entrar", data={"csrf": match[1]}, headers={"Origin": ORIGIN})
    page = prototype.get("/app/musicas")
    assert page.status_code == 200
    assert page.text.count('src="/static/demo-album-cover.svg"') == 6
    assert "https://i.scdn.co" not in page.text
    assert "img-src 'self';" in page.headers["content-security-policy"]
    assert "https://i.scdn.co" not in page.headers["content-security-policy"]
    assert prototype.get("/static/demo-album-cover.svg").status_code == 200
    assert prototype.get("/static/album-covers.js").status_code == 200
