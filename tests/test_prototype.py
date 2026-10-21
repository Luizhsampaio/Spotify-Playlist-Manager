import re
from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from music_curator.config import ORIGIN, Settings
from music_curator.demo import PROTOTYPE_PLAYLISTS, DemoCredentialStore
from music_curator.web import create_app

HEADERS = {"Origin": ORIGIN}


def csrf(browser: TestClient) -> str:
    response = browser.get("/entrar")
    match = re.search(r'name="csrf" value="([^"]+)"', response.text)
    assert match
    return match[1]


@pytest.fixture
def prototype() -> Iterator[TestClient]:
    class ForbiddenStore(DemoCredentialStore):
        def load(self) -> str | None:
            pytest.fail("Prototype must not read credentials")

        def save(self, refresh_token: str) -> None:
            pytest.fail("Prototype must not save credentials")

        def delete(self) -> None:
            pytest.fail("Prototype must not delete real credentials")

    def forbidden_request(request: httpx.Request) -> httpx.Response:
        pytest.fail("Prototype must not call Spotify or any external API")

    app = create_app(
        Settings(),
        demo=True,
        store=ForbiddenStore(),
        http=httpx.AsyncClient(transport=httpx.MockTransport(forbidden_request)),
    )
    with TestClient(app, base_url=ORIGIN) as browser:
        yield browser


def test_home_is_public_and_explains_prototype(prototype: TestClient) -> None:
    response = prototype.get("/")
    assert response.status_code == 200
    assert 'href="/entrar"' in response.text
    assert "Protótipo navegável" in response.text
    assert "Nenhuma conexão real" in response.text
    assert "Como funciona" in response.text
    assert "Antes de conectar" not in response.text
    assert prototype.get("/static/prototype.css").status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "HttpOnly" in response.headers["set-cookie"]


def test_login_page_has_no_registration_or_password(prototype: TestClient) -> None:
    page = prototype.get("/entrar")
    assert page.status_code == 200
    assert "Continuar com Spotify" in page.text
    assert 'action="/demo/entrar"' in page.text
    assert "simula a entrada" in page.text
    assert 'type="password"' not in page.text
    assert 'type="email"' not in page.text
    assert "Nenhuma base de usuários" in page.text


def test_complete_prototype_flow(prototype: TestClient) -> None:
    token = csrf(prototype)
    response = prototype.post("/demo/entrar", data={"csrf": token}, headers=HEADERS)
    assert response.url.path == "/app"
    assert response.status_code == 200
    assert "Sua coleção começa aqui" in response.text
    assert "Spotify · simulado" in response.text
    assert "Conta de demonstração" in response.text
    assert "6 playlists fictícias" in response.text
    for playlist in PROTOTYPE_PLAYLISTS:
        assert playlist.name in response.text
    assert "Sua conta Spotify não está conectada" in response.text
    assert "accounts.spotify.com/authorize" not in response.text
    assert prototype.get("/entrar", follow_redirects=False).headers["location"] == "/app"
    assert "Abrir painel" in prototype.get("/").text
    dashboard_token = re.search(r'name="csrf" value="([^"]+)"', response.text)
    assert dashboard_token
    assert dashboard_token[1] != token
    assert prototype.post("/demo/sair", data={"csrf": token}, headers=HEADERS).status_code == 403
    page = prototype.post("/demo/sair", data={"csrf": dashboard_token[1]}, headers=HEADERS)
    assert page.url.path == "/"
    assert "Abrir painel" not in page.text
    assert prototype.get("/app", follow_redirects=False).headers["location"] == "/entrar"


def test_dashboard_requires_demo_session(prototype: TestClient) -> None:
    page = prototype.get("/app", follow_redirects=False)
    assert page.status_code == 303
    assert page.headers["location"] == "/entrar"


def test_sessions_are_independent(prototype: TestClient) -> None:
    prototype.post("/demo/entrar", data={"csrf": csrf(prototype)}, headers=HEADERS)
    with TestClient(prototype.app, base_url=ORIGIN) as other:
        assert other.get("/app", follow_redirects=False).headers["location"] == "/entrar"
        assert "Abrir painel" not in other.get("/").text


@pytest.mark.parametrize("path", ["/demo/entrar", "/demo/sair"])
def test_demo_actions_require_csrf_and_same_origin(prototype: TestClient, path: str) -> None:
    token = csrf(prototype)
    assert prototype.post(path, data={"csrf": token}).status_code == 403
    assert prototype.post(path, data={"csrf": "invalid"}, headers=HEADERS).status_code == 403
    assert (
        prototype.post(
            path, data={"csrf": token}, headers={"Origin": "https://external.example"}
        ).status_code
        == 403
    )


@pytest.mark.parametrize("path", ["/connect", "/playlists", "/disconnect", "/callback"])
def test_real_operations_are_unavailable_in_demo(prototype: TestClient, path: str) -> None:
    response = (
        prototype.get(path)
        if path == "/callback"
        else prototype.post(path, data={"csrf": csrf(prototype)}, headers=HEADERS)
    )
    assert response.status_code == 404


def test_prototype_session_expiry(prototype: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    prototype.post("/demo/entrar", data={"csrf": csrf(prototype)}, headers=HEADERS)
    now = time.monotonic()
    monkeypatch.setattr("music_curator.web.time.monotonic", lambda: now + 9 * 60 * 60)
    assert prototype.get("/app", follow_redirects=False).headers["location"] == "/entrar"


def test_demo_routes_are_not_added_to_real_mode(browser: TestClient) -> None:
    assert browser.get("/entrar").status_code == 404
    assert browser.get("/app").status_code == 404
    assert browser.post("/demo/entrar", headers=HEADERS).status_code == 404
