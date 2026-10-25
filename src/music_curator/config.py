from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field

ORIGIN = "http://127.0.0.1:8765"
CALLBACK = f"{ORIGIN}/callback"
APP_NAME = "harmonia playlists"


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    client_id: str = Field(default="", pattern=r"^(?:[a-fA-F0-9]{32})?$")
    policy_mode: Literal["blocked"] = "blocked"


def load_settings() -> Settings:
    import os

    local = dotenv_values(Path.cwd() / ".env")
    return Settings.model_validate(
        {
            "client_id": os.environ.get("SPOTIFY_CLIENT_ID", local.get("SPOTIFY_CLIENT_ID") or ""),
            "policy_mode": os.environ.get(
                "SPOTIFY_AI_POLICY_MODE", local.get("SPOTIFY_AI_POLICY_MODE") or "blocked"
            ),
        }
    )
