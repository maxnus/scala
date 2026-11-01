"""The setup script: as the server serves it, as the pages show it, and what it refuses before it changes anything.

What it does as root, on a fresh Ubuntu, `tools/try_runner_setup.py` tries in a VM.
"""

import hashlib
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sc2scala.cli import main
from sc2scala.server.runner_setup import setup_script
from tests.conftest import sign_in_dev
from tests.match_helpers import LadderWithBots, ladder_with_bots
from tests.test_runner_setup import add_runner_form

_BASH = shutil.which("bash")
_SHELLCHECK = shutil.which("shellcheck")


@pytest.fixture
def served_script(server: TestClient, tmp_path: Path) -> Path:
    """The script the server serves, saved as the page's download-and-check commands save it."""
    response = server.get("/runner/setup.sh")
    assert response.status_code == 200
    script = tmp_path / "setup.sh"
    script.write_bytes(response.content)
    return script


def run_script(script: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    assert _BASH is not None
    return subprocess.run([_BASH, str(script), *arguments], capture_output=True, text=True, check=False, timeout=30)


def test_the_server_serves_the_script_with_its_address(server: TestClient) -> None:
    response = server.get("/runner/setup.sh")
    assert response.headers["content-type"] == "text/x-shellscript; charset=utf-8"
    assert 'SCALA_SERVER="http://localhost"\n' in response.text
    assert "__SCALA_SERVER__" not in response.text


def test_the_code_page_shows_the_command_and_the_scripts_hash(server: TestClient) -> None:
    ladder: LadderWithBots = ladder_with_bots(server)
    page = " ".join(add_runner_form(ladder.server, "alice", serves_public="true").text.split())
    script_hash = hashlib.sha256(server.get("/runner/setup.sh").content).hexdigest()
    assert "curl -fsSL http://localhost/runner/setup.sh | sudo bash -s -- scala_setup_" in page
    assert f'echo "{script_hash} setup.sh" | sha256sum --check' in page


def test_the_account_page_shows_how_to_update_and_remove_a_machine(server: TestClient) -> None:
    ladder = ladder_with_bots(server)
    sign_in_dev(ladder.server, "alice")
    page = " ".join(ladder.server.get("/account").text.split())
    assert "curl -fsSL http://localhost/runner/setup.sh | sudo bash</code>" in page
    assert "curl -fsSL http://localhost/runner/setup.sh | sudo bash -s -- --remove</code>" in page


@pytest.mark.parametrize(
    "public_url", ['https://scala.example.org/"; rm -rf /; "', "https://$(whoami).example.org", "https://a`b`.org"]
)
def test_an_address_that_could_be_more_is_refused(public_url: str) -> None:
    with pytest.raises(ValueError, match="not an address the setup script may hold"):
        setup_script(public_url)


@pytest.mark.skipif(_BASH is None, reason="no bash")
def test_the_script_says_how_to_use_it_and_refuses_what_it_does_not_know(served_script: Path) -> None:
    usage = run_script(served_script, "--help")
    assert usage.returncode == 0
    assert "Makes this computer a runner machine of http://localhost, as the user sc2scala" in usage.stdout
    unknown = run_script(served_script, "--force")
    assert (unknown.returncode, unknown.stderr.strip()) == (1, "Stopped: No option --force: see --help")
    two_codes = run_script(served_script, "scala_setup_one", "scala_setup_two")
    assert two_codes.stderr.strip() == "Stopped: One setup code at most, not also scala_setup_two"


@pytest.mark.skipif(_BASH is None, reason="no bash")
def test_the_script_changes_nothing_unless_it_runs_as_root(served_script: Path) -> None:
    if subprocess.run(["id", "-u"], capture_output=True, text=True, check=True).stdout.strip() == "0":
        pytest.skip("the tests run as root")
    refused = run_script(served_script, "scala_setup_code")
    assert refused.returncode == 1
    assert "Stopped: Run it as root: curl -fsSL http://localhost/runner/setup.sh | sudo bash" in refused.stderr
    removal = run_script(served_script, "--remove")
    assert "Stopped: Run it as root" in removal.stderr


@pytest.mark.skipif(_SHELLCHECK is None, reason="no shellcheck: uv sync --extra dev")
def test_shellcheck_finds_nothing_in_the_script(served_script: Path) -> None:
    assert _SHELLCHECK is not None
    command = [_SHELLCHECK, "-s", "bash", str(served_script)]
    checked = subprocess.run(command, capture_output=True, text=True, check=False)
    assert checked.returncode == 0, checked.stdout


def test_runner_pull_pulls_the_images_docker_lacks(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    commands: list[list[str]] = []

    def docker(command: list[str], **options: Any) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        here = command[:3] == ["docker", "image", "inspect"] and command[3].startswith("aiarena/arenaclient-sc2")
        return subprocess.CompletedProcess(command, 0 if here or command[1] == "pull" else 1)

    monkeypatch.setattr("sc2scala.runner_commands.subprocess.run", docker)
    assert main(["runner", "pull"]) == 0
    assert [command[1] for command in commands] == ["image", "image", "pull", "image", "pull", "image", "pull"]
    assert [command[2] for command in commands if command[1] == "pull"] == [
        "aiarena/arenaclient-bot:v0.8.0",
        "aiarena/arenaclient-proxy:v0.8.0",
        "aiarena/arenaclient-bot-base:v0.8.0",
    ]
    assert capsys.readouterr().out == "aiarena/arenaclient-sc2:v0.8.0: here already\n"


def test_runner_pull_says_when_docker_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def docker(command: list[str], **options: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1)

    monkeypatch.setattr("sc2scala.runner_commands.subprocess.run", docker)
    assert main(["runner", "pull"]) == 1
    assert "Docker could not pull aiarena/arenaclient-sc2:v0.8.0" in capsys.readouterr().err
