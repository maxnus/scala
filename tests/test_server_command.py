import builtins
from typing import Any

import pytest

from sc2scala.cli import main


def refuse_to_import(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def import_without(module: str, *args: Any, **kwargs: Any) -> Any:
        if module == name or module.startswith(f"{name}."):
            raise ModuleNotFoundError(f"No module named {module!r}", name=module)
        return real_import(module, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without)


def test_a_missing_server_package_asks_for_the_extra(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    refuse_to_import("uvicorn", monkeypatch)
    assert main(["server", "--data", str(tmp_path)]) == 1
    assert "sc2scala[server]" in capsys.readouterr().err


def test_a_missing_module_of_scalas_own_is_not_taken_for_the_extra(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    refuse_to_import("sc2scala.server.app", monkeypatch)
    with pytest.raises(ModuleNotFoundError, match="sc2scala.server.app"):
        main(["server", "--data", str(tmp_path)])
