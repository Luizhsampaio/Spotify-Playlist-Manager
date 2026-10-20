"""Scan public project files without emitting secret values or derived hashes."""

import subprocess

from check_no_private_data import is_private
from detect_secrets.core.secrets_collection import SecretsCollection
from detect_secrets.settings import default_settings


def main() -> int:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        capture_output=True,
        check=True,
        encoding="utf-8",
    )
    names = {name for name in result.stdout.split("\0") if name}
    if any(is_private(name) for name in names):
        print("ERRO: caminho privado entre arquivos publicos. Verifique o Git antes de escanear.")
        return 1
    collection = SecretsCollection()
    with default_settings() as settings:
        settings.disable_filters(
            "detect_secrets.filters.common.is_ignored_due_to_verification_policies"
        )
        for name in sorted(names):
            collection.scan_file(name)
    if collection:
        print("ERRO: possivel segredo encontrado. Revise localmente antes de fazer commit.")
        return 1
    print(f"OK: scanner de segredos aprovou {len(names)} arquivos publicos.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
