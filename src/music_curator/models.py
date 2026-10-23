import re
from datetime import date, datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


class AppError(Exception):
    """Only static, user-safe messages belong in this exception."""


class ReconnectRequired(AppError):
    pass


class TokenResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    access_token: SecretStr = Field(min_length=1)
    token_type: str = Field(pattern="(?i)^bearer$")
    expires_in: int = Field(gt=0)
    refresh_token: SecretStr | None = Field(default=None, min_length=1)
    scope: str | None = None


class Playlist(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(pattern=r"^[A-Za-z0-9]+$", min_length=1, max_length=128)
    name: str

    @property
    def url(self) -> str:
        return f"https://open.spotify.com/playlist/{self.id}"


class Artist(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str


class AlbumImage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    url: str


class Album(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    release_date: str | None = None
    release_date_precision: Literal["year", "month", "day"] | None = None
    images: list[AlbumImage] = Field(default_factory=list)

    @property
    def cover_url(self) -> str | None:
        for image in reversed(self.images):
            if re.fullmatch(r"https://i\.scdn\.co/image/[A-Za-z0-9]{1,128}", image.url):
                return image.url
        return None

    @field_validator("release_date")
    @classmethod
    def valid_release_date(cls, value: str | None) -> str | None:
        if value is not None:
            if not re.fullmatch(r"[0-9]{4}(?:-[0-9]{2}){0,2}", value):
                raise ValueError("Invalid album release date")
            date.fromisoformat(value + "-01" * (2 - value.count("-")))
        return value

    @model_validator(mode="after")
    def consistent_release_precision(self) -> Self:
        if self.release_date is not None and self.release_date_precision is not None:
            lengths = {"year": 4, "month": 7, "day": 10}
            if len(self.release_date) != lengths[self.release_date_precision]:
                raise ValueError("Album release precision does not match date")
        return self

    @property
    def release_label(self) -> str:
        if self.release_date is None:
            return "Não informada"
        return "/".join(reversed(self.release_date.split("-")))

    @property
    def release_year(self) -> str | None:
        return self.release_date[:4] if self.release_date else None


class Track(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9]+$", min_length=1, max_length=128)
    name: str
    artists: list[Artist]
    album: Album
    duration_ms: int = Field(ge=0)

    @property
    def url(self) -> str | None:
        return f"https://open.spotify.com/track/{self.id}" if self.id else None

    @property
    def duration(self) -> str:
        minutes, seconds = divmod(self.duration_ms // 1000, 60)
        return f"{minutes}:{seconds:02d}"


class SavedTrack(BaseModel):
    model_config = ConfigDict(extra="ignore")

    added_at: datetime | None = None
    track: Track | None


class CollectionPage[T](BaseModel):
    model_config = ConfigDict(extra="ignore")

    items: list[T]
    next: str | None = None


PlaylistPage = CollectionPage[Playlist]
SavedTrackPage = CollectionPage[SavedTrack]
