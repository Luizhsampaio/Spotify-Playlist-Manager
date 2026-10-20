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
    app = create_app(Settings(client_id=CLIENT_ID), store=store, http=http, demo=True)
    with TestClient(app, base_url=ORIGIN) as client:
        yield client
