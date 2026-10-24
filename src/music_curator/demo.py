from datetime import UTC, datetime

import httpx

from music_curator.models import Album, Artist, Playlist, SavedTrack, Track
from music_curator.spotify import TOKEN_URL

PROTOTYPE_PLAYLISTS = [
    Playlist(id="syntheticRock", name="Horizontes de guitarra"),
    Playlist(id="syntheticJazz", name="Café com improviso"),
    Playlist(id="syntheticAmbient", name="Pausa para respirar"),
    Playlist(id="syntheticDisco", name="Depois das seis"),
    Playlist(id="syntheticIndie", name="Entre descobertas"),
    Playlist(id="syntheticSoul", name="Domingo sem pressa"),
]

PROTOTYPE_TRACKS = [
    SavedTrack(
        added_at=datetime(2026, 1, 15, 12, tzinfo=UTC),
        track=Track(
            id=f"syntheticTrack{index}",
            name=name,
            artists=[
                Artist(
                    name=artist,
                    id={
                        "Banda Aurora": "syntheticAurora",
                        "Clara do Sol": "syntheticClara",
                        "Trio Horizonte": "syntheticTrio",
                        "Coletivo Vento": "syntheticColetivo",
                    }[artist],
                )
            ],
            album=Album(name=album, release_date=release_date),
            duration_ms=duration,
        ),
    )
    for index, (name, artist, album, release_date, duration) in enumerate(
        [
            ("Horizonte de papel", "Banda Aurora", "Novos caminhos", "2023-08-18", 213000),
            ("Café na varanda", "Clara do Sol", "Pequenas pausas", "2024-04", 184000),
            ("Cidade em movimento", "Trio Horizonte", "Depois da chuva", "2022", 265000),
            ("Um lugar tranquilo", "Banda Aurora", "Novos caminhos", "2023-08-18", 198000),
            ("Luzes da estação", "Coletivo Vento", "Entre chegadas", "2025-11-07", 231000),
            ("Domingo azul", "Clara do Sol", "Pequenas pausas", "2024-04", 176000),
        ],
        start=1,
    )
]


class DemoCredentialStore:
    """Synthetic credentials in memory, used only by the explicit demo mode."""

    def __init__(self) -> None:
        self.token: str | None = None
        self.scopes: set[str] | None = None

    def load(self) -> str | None:
        return self.token

    def save(self, refresh_token: str) -> None:
        self.token = refresh_token

    def load_scopes(self) -> set[str] | None:
        return set(self.scopes) if self.scopes is not None else None

    def save_scopes(self, scopes: set[str]) -> None:
        self.scopes = set(scopes)

    def delete(self) -> None:
        self.token = None
        self.scopes = None


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
    if request.url.path == "/v1/me/tracks":
        return httpx.Response(
            200,
            json={
                "items": [item.model_dump(mode="json") for item in PROTOTYPE_TRACKS],
                "next": None,
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
