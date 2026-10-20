import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "--quiet", str(path)], check=True)


def scan(path: Path, script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / script)],
        cwd=path,
        capture_output=True,
        text=True,
        check=False,
    )


def test_secret_scanner_rejects_fixture_without_printing_it(tmp_path: Path) -> None:
    init_repo(tmp_path)
    fake = "synthetic-password-fixture-not-a-real-credential"
    (tmp_path / "example.py").write_text("password = " + repr(fake), encoding="utf-8")
    result = scan(tmp_path, "check_secrets.py")
    assert result.returncode == 1
    assert fake not in result.stdout + result.stderr
    assert "possivel segredo" in result.stdout


def test_secret_scanner_ignores_private_files_without_reading_them(tmp_path: Path) -> None:
    init_repo(tmp_path)
    (tmp_path / ".gitignore").write_text(".env\n", encoding="utf-8")
    (tmp_path / ".env").write_text("password=synthetic-private-value", encoding="utf-8")
    (tmp_path / "example.py").write_text("print('Hello')", encoding="utf-8")
    result = scan(tmp_path, "check_secrets.py")
    assert result.returncode == 0
    assert "2 arquivos" in result.stdout


def test_tracked_private_path_is_rejected(tmp_path: Path) -> None:
    init_repo(tmp_path)
    (tmp_path / ".env").write_text("synthetic", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", ".env"], check=True)
    assert scan(tmp_path, "check_no_private_data.py").returncode == 1
    assert scan(tmp_path, "check_secrets.py").returncode == 1
