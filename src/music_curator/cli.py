import logging

import typer
import uvicorn
from pydantic import ValidationError

from music_curator.config import APP_NAME, ORIGIN, Settings, load_settings
from music_curator.web import create_app

cli = typer.Typer(help=f"{APP_NAME}: curadoria musical e playlists gerenciadas localmente.")


@cli.command()
def serve(
    demo: bool = typer.Option(False, "--demo", help="Usar apenas dados ficticios, sem Spotify."),
) -> None:
    """Abrir o servidor local em http://127.0.0.1:8765."""
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    if demo:
        app = create_app(Settings(), demo=True)
    else:
        try:
            settings = load_settings()
        except ValidationError:
            typer.echo(
                "Configuracao invalida: confira o Client ID (32 caracteres hexadecimais) "
                "e mantenha SPOTIFY_AI_POLICY_MODE=blocked.",
                err=True,
            )
            raise typer.Exit(code=1) from None
        app = create_app(settings)
    typer.echo(f"Abra {ORIGIN} no navegador. Para encerrar, pressione Ctrl+C.")
    if demo:
        typer.echo("DEMONSTRACAO: dados ficticios; nenhuma conexao real sera feita.")
    uvicorn.run(app, host="127.0.0.1", port=8765, access_log=False, log_level="warning")


@cli.callback()
def root() -> None:
    """Interface local para entrar pelo Spotify e abrir seu painel."""


def main() -> None:
    cli()
