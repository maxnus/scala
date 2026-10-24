import io
import stat
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import PYTHON_BOT_ZIP, api_headers_of, zip_of


def upload(server: TestClient, headers: dict[str, str], name: str, archive: bytes, race: str | None = "T") -> dict:
    response = server.post(
        f"/api/bots/{name}/versions",
        headers=headers,
        files={"archive": (f"{name}.zip", archive, "application/zip")},
        data={"race": race} if race else None,
    )
    return {"status": response.status_code, **response.json()}


def kept_bot_archives(server: TestClient) -> list[str]:
    folder = server.app.state.file_store.root / "bots"  # type: ignore[attr-defined]
    return sorted(path.name for path in folder.iterdir()) if folder.exists() else []


def test_the_first_upload_makes_the_bot_and_its_first_version(server: TestClient) -> None:
    alice = api_headers_of(server, "alice")
    uploaded = upload(server, alice, "basic_bot", PYTHON_BOT_ZIP)
    assert uploaded["status"] == 200
    assert uploaded["bot"] == {"name": "basic_bot", "author": "alice", "race": "T", "latest_version": 1}
    assert (uploaded["version"]["number"], uploaded["version"]["type"], uploaded["new"]) == (1, "python", True)


def test_a_zip_uploaded_again_is_no_new_version_and_another_is(server: TestClient) -> None:
    alice = api_headers_of(server, "alice")
    upload(server, alice, "basic_bot", PYTHON_BOT_ZIP)
    again = upload(server, alice, "basic_bot", PYTHON_BOT_ZIP, race=None)
    changed = upload(server, alice, "basic_bot", zip_of({"run.py": b"print('better')"}), race="Z")
    assert (again["new"], again["version"]["number"]) == (False, 1)
    assert (changed["new"], changed["version"]["number"], changed["bot"]["race"]) == (True, 2, "Z")
    assert len(kept_bot_archives(server)) == 2


def test_a_new_bot_needs_a_race_and_a_name_the_engine_takes(server: TestClient) -> None:
    alice = api_headers_of(server, "alice")
    assert upload(server, alice, "basic_bot", PYTHON_BOT_ZIP, race=None)["status"] == 422
    assert upload(server, alice, "basic_bot", PYTHON_BOT_ZIP, race="X")["status"] == 422
    assert upload(server, alice, "-bot", PYTHON_BOT_ZIP)["status"] == 422


def test_only_its_author_uploads_to_a_bot_in_any_case(server: TestClient) -> None:
    alice = api_headers_of(server, "alice")
    bob = api_headers_of(server, "bob")
    upload(server, alice, "BasicBot", PYTHON_BOT_ZIP)
    assert upload(server, bob, "basicbot", PYTHON_BOT_ZIP)["status"] == 403
    assert upload(server, alice, "basicbot", zip_of({"run.py": b"v2"}))["bot"]["name"] == "BasicBot"


def test_an_unsafe_or_unplayable_zip_is_refused_and_not_kept(server: TestClient) -> None:
    alice = api_headers_of(server, "alice")
    refused = {
        "a path out of the folder": zip_of({"run.py": b"", "../escape.py": b""}),
        "an absolute path": zip_of({"run.py": b"", "/etc/cron.d/bot": b""}),
        "no entry point": zip_of({"bot.py": b""}),
        "an entry point below the root": zip_of({"basic_bot/run.py": b""}),
        "a Windows bot": zip_of({"basic_bot.exe": b"MZ"}),
        "not a zip": b"PK but not quite",
    }
    for what, archive in refused.items():
        response = upload(server, alice, "basic_bot", archive)
        assert response["status"] == 422, what
    assert kept_bot_archives(server) == []


def test_a_zip_holding_a_symbolic_link_is_refused(server: TestClient) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("run.py", b"")
        link = zipfile.ZipInfo("secrets")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(link, "/home/runner/.ssh/id_ed25519")
    assert upload(server, api_headers_of(server, "alice"), "basic_bot", buffer.getvalue())["status"] == 422


def test_a_version_is_downloaded_by_its_author_alone(server: TestClient) -> None:
    alice = api_headers_of(server, "alice")
    bob = api_headers_of(server, "bob")
    upload(server, alice, "basic_bot", PYTHON_BOT_ZIP)
    downloaded = server.get("/api/bots/basic_bot/versions/1/archive", headers=alice)
    assert downloaded.content == PYTHON_BOT_ZIP
    assert server.get("/api/bots/basic_bot/versions/1/archive", headers=bob).status_code == 403
    assert server.get("/api/bots/basic_bot/versions/2/archive", headers=alice).status_code == 404


def test_a_user_lists_their_own_bots_and_reads_any(server: TestClient) -> None:
    alice = api_headers_of(server, "alice")
    bob = api_headers_of(server, "bob")
    upload(server, alice, "basic_bot", PYTHON_BOT_ZIP)
    upload(server, bob, "other_bot", PYTHON_BOT_ZIP)
    assert [bot["name"] for bot in server.get("/api/bots", headers=alice).json()] == ["basic_bot"]
    details = server.get("/api/bots/other_bot", headers=alice).json()
    assert (details["author"], [version["number"] for version in details["versions"]]) == ("bob", [1])


def test_the_engine_finds_a_version_by_its_hash(server: TestClient, tmp_path: Path) -> None:
    alice = api_headers_of(server, "alice")
    version = upload(server, alice, "basic_bot", PYTHON_BOT_ZIP)["version"]
    assert kept_bot_archives(server) == [f"{version['sha256']}.zip"]
