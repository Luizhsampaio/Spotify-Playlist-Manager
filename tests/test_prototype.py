import re

import pytest
from fastapi.testclient import TestClient

from music_curator.config import ORIGIN

HEADERS = {"Origin": ORIGIN}


def csrf(browser: TestClient) -> str:
    response = browser.get("/entrar")
    match = re.search(r'name="csrf" value="([^"]+)"', response.text)
    assert match
    return match[1]


def test_home_is_public_and_explains_prototype(prototype: TestClient) -> None:
    response = prototype.get("/")
    assert response.status_code == 200
    assert 'href="/entrar"' in response.text
    assert "PRÉVIA COM DADOS FICTÍCIOS" in response.text
    assert "Projeto independente · Protótipo" in response.text
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


@pytest.mark.parametrize("signed_in", [False, True])
def test_home_omits_removed_sections(prototype: TestClient, signed_in: bool) -> None:
    if signed_in:
        prototype.post("/demo/entrar", data={"csrf": csrf(prototype)}, headers=HEADERS)
    page = prototype.get("/")
    assert page.status_code == 200
    for removed in (
        'id="como-funciona"',
        'id="seu-espaco"',
        'href="#seu-espaco"',
        "Da sua conta ao seu espaço.",
        "Entre com Spotify",
        "Conheça seu espaço",
        "Continue no controle",
        "Um espaço seu.",
        "Explorar o protótipo",
        'class="prototype-strip"',
        "Protótipo navegável",
        "Explore com dados fictícios. Nenhuma conexão real com o Spotify.",
    ):
        assert removed not in page.text
    assert 'href="#como-funciona">Como funciona</a>' in page.text
    assert "Conheça a ideia" in page.text
    assert 'class="hero wrap"' in page.text
    assert "PRÉVIA COM DADOS FICTÍCIOS" in page.text
    assert '<footer class="site-footer wrap">' in page.text
    assert "Feito para ouvir sua coleção de um jeito novo." in page.text
    assert page.text.count('href="/app"' if signed_in else 'href="/entrar"') == 2


def test_complete_prototype_flow(prototype: TestClient) -> None:
    token = csrf(prototype)
    response = prototype.post("/demo/entrar", data={"csrf": token}, headers=HEADERS)
    assert response.url.path == "/app"
    assert response.status_code == 200
    assert "Sua coleção começa aqui" in response.text
    assert "Spotify · simulado" in response.text
    assert "Conta de demonstração" in response.text
    assert "Playlists e automações ainda não estão disponíveis" in response.text
    assert "collection-card" not in response.text
    assert re.findall(r'<form[^>]+action="([^"]+)"', response.text) == ["/demo/sair"]
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
    assert browser.get("/entrar").status_code == 200
    assert browser.get("/app", follow_redirects=False).headers["location"] == "/entrar"
    assert browser.post("/demo/entrar", headers=HEADERS).status_code == 404
    assert browser.post("/demo/sair", headers=HEADERS).status_code == 404
