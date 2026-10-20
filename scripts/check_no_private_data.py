"""Check tracked filenames only; never open private library files."""

import subprocess
from pathlib import PurePosixPath

FORBIDDEN_PARTS = {".local-data", ".venv", "__pycache__"}


def is_private(name: str) -> bool:
    path = PurePosixPath(name)
    return bool(FORBIDDEN_PARTS.intersection(path.parts)) or (
        path.name != ".env.example"
        and (path.name == ".env" or path.name.startswith((".env.", "config.local.", ".token")))
    )


def main() -> int:
    result = subprocess.run(
        ["git", "ls-files", "-z"], capture_output=True, check=True, encoding="utf-8"
    )
    private = [name for name in result.stdout.split("\0") if name and is_private(name)]
    if private:
        print(
            "ERRO: arquivos privados rastreados pelo Git. Remova-os do indice antes de continuar."
        )
        return 1
    print("OK: nenhum caminho privado rastreado pelo Git.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
