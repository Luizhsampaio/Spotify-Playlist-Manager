from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from music_curator.config import ORIGIN, Settings
from music_curator.demo import DemoCredentialStore, demo_response
from music_curator.web import create_app

CLIENT_ID = "0" * 32


@pytest.fixture
def store() -> DemoCredentialStore:
    return DemoCredentialStore()


@pytest.fixture
def browser(store: DemoCredentialStore) -> Iterator[TestClient]:
    http = httpx.AsyncClient(transport=httpx.MockTransport(demo_response))
    app = create_app(Settings(client_id=CLIENT_ID), store=store, http=http)
    with TestClient(app, base_url=ORIGIN) as client:
        yield client


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
    with TestClient(app, base_url=ORIGIN) as client:
        yield client
