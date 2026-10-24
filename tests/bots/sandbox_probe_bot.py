"""A bot that tries, before it joins the game, to do what the sandbox forbids, prints what came of it, and leaves.

The line it prints begins with `SANDBOX_PROBE ` and holds a JSON object, each attempt mapped to what came of it.
"""

import json
import os
import socket
import subprocess
from collections.abc import Callable
from pathlib import Path

from sc2.bot_ai import BotAI
from sc2.data import Race
from sc2.player import Bot

_PROCESSES_TO_START = 1100


def _outcome_of(attempt: Callable[[], object]) -> str:
    try:
        attempt()
    except Exception as error:
        return f"failed: {type(error).__name__}"
    return "succeeded"


def _connect(host: str, port: int) -> None:
    socket.create_connection((host, port), timeout=3).close()


def _status_field(name: str) -> str:
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith(f"{name}:"):
            return line.split(":", 1)[1].strip()
    return "missing"


def _processes_started(count: int) -> int:
    children: list[subprocess.Popen[bytes]] = []
    try:
        for _ in range(count):
            children.append(subprocess.Popen(["sleep", "30"]))
    except OSError:
        pass
    finally:
        for child in children:
            child.kill()
            child.wait()
    return len(children)


def probe_the_sandbox() -> dict[str, object]:
    bot_folder = Path(__file__).resolve().parent
    return {
        "user": os.getuid(),
        "capabilities": _status_field("CapEff"),
        "no_new_privileges": _status_field("NoNewPrivs"),
        "bots visible": sorted(os.listdir("/bots")),
        "resolve github.com": _outcome_of(lambda: socket.getaddrinfo("github.com", 443)),
        "connect 1.1.1.1:443": _outcome_of(lambda: _connect("1.1.1.1", 443)),
        "resolve sc2_controller": _outcome_of(lambda: socket.getaddrinfo("sc2_controller", 8083)),
        "resolve bot_controller2": _outcome_of(lambda: socket.getaddrinfo("bot_controller2", 8082)),
        "resolve bot_controller1": _outcome_of(lambda: socket.getaddrinfo("bot_controller1", 8081)),
        "connect proxy_controller:8080": _outcome_of(lambda: _connect("proxy_controller", 8080)),
        "write /": _outcome_of(lambda: Path("/probe").write_text("probe")),
        "write its own folder": _outcome_of(lambda: (bot_folder / "probe").write_text("probe")),
        "write its data folder": _outcome_of(lambda: (bot_folder / "data" / "probe").write_text("probe")),
        "write /tmp": _outcome_of(lambda: Path("/tmp/probe").write_text("probe")),
        f"processes started of {_PROCESSES_TO_START}": _processes_started(_PROCESSES_TO_START),
    }


class ProbeBot(BotAI):
    async def on_step(self, iteration: int) -> None:
        await self.client.leave()


print("SANDBOX_PROBE " + json.dumps(probe_the_sandbox()), flush=True)
BOT = Bot(Race.Terran, ProbeBot())
