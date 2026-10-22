import unicodedata
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, field_validator

from music_curator.models import SavedTrack


def _search_text(value: str) -> str:
    return "".join(
        char
        for char in unicodedata.normalize("NFKD", value.casefold())
        if not unicodedata.combining(char)
    )


class SongFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    q: str = Field(default="", max_length=200)
    artist: str = Field(default="", max_length=512)
    album: str = Field(default="", max_length=512)
    year: str = Field(default="", pattern=r"^(?:[0-9]{4})?$")

    @field_validator("q")
    @classmethod
    def trim_query(cls, value: str) -> str:
        return value.strip()

    @property
    def active(self) -> bool:
        return bool(self.q or self.artist or self.album or self.year)


@dataclass
class SongCollection:
    tracks: list[SavedTrack]
    artists: list[str]
    albums: list[str]
    years: list[str]


def song_collection(items: list[SavedTrack], filters: SongFilters) -> SongCollection:
    artists: set[str] = set()
    albums: set[str] = set()
    years: set[str] = set()
    visible: list[SavedTrack] = []
    terms = _search_text(filters.q).split()
    for item in items:
        track = item.track
        if track is None:
            if not filters.active:
                visible.append(item)
            continue
        names = [artist.name for artist in track.artists]
        artists.update(names)
        albums.add(track.album.name)
        if track.album.release_year:
            years.add(track.album.release_year)
        if filters.artist and filters.artist not in names:
            continue
        if filters.album and filters.album != track.album.name:
            continue
        if filters.year and filters.year != track.album.release_year:
            continue
        text = _search_text(" ".join([track.name, *names, track.album.name]))
        if all(term in text for term in terms):
            visible.append(item)
    return SongCollection(
        visible,
        sorted(artists, key=_search_text),
        sorted(albums, key=_search_text),
        sorted(years, reverse=True),
    )
