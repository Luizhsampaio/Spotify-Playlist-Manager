import httpx

from music_curator.models import Playlist
from music_curator.spotify import TOKEN_URL

PROTOTYPE_PLAYLISTS = [
    Playlist(id="syntheticRock", name="Horizontes de guitarra"),
    Playlist(id="syntheticJazz", name="Café com improviso"),
    Playlist(id="syntheticAmbient", name="Pausa para respirar"),
    Playlist(id="syntheticDisco", name="Depois das seis"),
    Playlist(id="syntheticIndie", name="Entre descobertas"),
    Playlist(id="syntheticSoul", name="Domingo sem pressa"),
]


class DemoCredentialStore:
    """Synthetic credentials in memory, used only by the explicit demo mode."""

    def __init__(self) -> None:
        self.token: str | None = None

    def load(self) -> str | None:
        return self.token

    def save(self, refresh_token: str) -> None:
        self.token = refresh_token

    def delete(self) -> None:
        self.token = None


def demo_response(request: httpx.Request) -> httpx.Response:
    if str(request.url) == TOKEN_URL:
        return httpx.Response(
            200,
            json={
                "access_token": "synthetic-access",
                "refresh_token": "synthetic-refresh",
                "token_type": "Bearer",
                "expires_in": 3600,
            },
        )
    return httpx.Response(
        200,
        json={
            "items": [
                {"id": "syntheticRock", "name": "Horizontes de guitarra"},
                {"id": "syntheticJazz", "name": "Cafe com improviso"},
                {"id": "syntheticAmbient", "name": "Pausa para respirar"},
            ],
            "next": None,
        },
    )
