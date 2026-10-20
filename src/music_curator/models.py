from pydantic import BaseModel, ConfigDict, Field, SecretStr


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


class PlaylistPage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    items: list[Playlist]
    next: str | None = None
