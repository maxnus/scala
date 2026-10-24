"""Plays one match, as `sc2scala play` does, and measures the memory and CPU each of its containers took.

    uv run python tools/measure_match.py BOT_FOLDER BOT_FOLDER --map MAP [--races RACE RACE] [--output FOLDER]

It samples the cgroup of every container of a Compose project named `sc2scala-*` once a second, so play no other match
on the machine meanwhile. It needs cgroup v2, as Ubuntu has it. Memory is what the cgroup charges, page cache included;
a container's peak is the kernel's own `memory.peak`, the sum's peak the highest of the sums sampled, which the sum of
the containers' peaks bounds from above.
"""

import argparse
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from sc2scala import AiArenaEngine, Bot, Match, MatchResult, Race

_CGROUP_ROOT = Path("/sys/fs/cgroup")
_PROJECT_PREFIX = "sc2scala-"
_MEBIBYTE = 1024 * 1024


@dataclass
class ContainerUsage:
    """What one container of the match took, as last sampled."""

    service: str
    cgroup: Path
    memory_peak_bytes: int = 0
    cpu_microseconds: int = 0


@dataclass
class MatchUsage:
    """What the containers of the match took, sampled while it played."""

    containers: dict[str, ContainerUsage] = field(default_factory=dict)
    summed_memory_peak_bytes: int = 0
    samples: int = 0

    def sample(self) -> None:
        for container_id, service in _running_match_containers():
            if container_id not in self.containers:
                cgroup = _cgroup_of(container_id)
                if cgroup is None:
                    continue
                self.containers[container_id] = ContainerUsage(service=service, cgroup=cgroup)
        summed_memory_bytes = 0
        for usage in self.containers.values():
            try:
                memory_bytes = int((usage.cgroup / "memory.current").read_text())
                peak_file = usage.cgroup / "memory.peak"
                peak_bytes = int(peak_file.read_text()) if peak_file.exists() else memory_bytes
                cpu_microseconds = _cpu_microseconds_of(usage.cgroup)
            except (OSError, ValueError):
                continue  # The container has stopped since it was listed.
            summed_memory_bytes += memory_bytes
            usage.memory_peak_bytes = max(usage.memory_peak_bytes, peak_bytes, memory_bytes)
            usage.cpu_microseconds = max(usage.cpu_microseconds, cpu_microseconds)
        self.summed_memory_peak_bytes = max(self.summed_memory_peak_bytes, summed_memory_bytes)
        self.samples += 1


def _running_match_containers() -> list[tuple[str, str]]:
    """Each running container of a project named `sc2scala-*`, as its id and its service."""
    listing = subprocess.run(
        [
            "docker",
            "ps",
            "--no-trunc",
            "--filter",
            "label=com.docker.compose.project",
            "--format",
            '{{.ID}}\t{{.Label "com.docker.compose.project"}}\t{{.Label "com.docker.compose.service"}}',
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    containers = []
    for line in listing.splitlines():
        container_id, project, service = line.split("\t")
        if project.startswith(_PROJECT_PREFIX):
            containers.append((container_id, service))
    return containers


def _cgroup_of(container_id: str) -> Path | None:
    """The cgroup v2 folder of the container's main process, or `None` once it has stopped."""
    pid = subprocess.run(
        ["docker", "inspect", "--format", "{{.State.Pid}}", container_id], capture_output=True, text=True, check=False
    ).stdout.strip()
    if not pid or pid == "0":
        return None
    try:
        membership = Path(f"/proc/{pid}/cgroup").read_text()
    except OSError:
        return None
    for line in membership.splitlines():
        if line.startswith("0::"):
            return _CGROUP_ROOT / line.removeprefix("0::").lstrip("/")
    return None


def _cpu_microseconds_of(cgroup: Path) -> int:
    for line in (cgroup / "cpu.stat").read_text().splitlines():
        name, value = line.split()
        if name == "usage_usec":
            return int(value)
    raise ValueError(f"{cgroup / 'cpu.stat'} holds no usage_usec")


def _report(match: Match, result: MatchResult, usage: MatchUsage, real_time_seconds: float) -> str:
    lines = [
        f"{match.player_1.name} vs {match.player_2.name} on {match.map_name}: {result.code.value}, "
        f"{result.game_steps} steps ({result.game_seconds:.0f} s of game time), "
        f"{real_time_seconds:.0f} s of real time, {usage.samples} samples",
        "",
        f"{'container':<20} {'peak memory (MiB)':>18} {'CPU (s)':>9}",
    ]
    by_service = sorted(usage.containers.values(), key=lambda container: container.service)
    for container in by_service:
        lines.append(
            f"{container.service:<20} {container.memory_peak_bytes / _MEBIBYTE:>18.0f} "
            f"{container.cpu_microseconds / 1e6:>9.1f}"
        )
    total_cpu_seconds = sum(container.cpu_microseconds for container in by_service) / 1e6
    summed_peaks_bytes = sum(container.memory_peak_bytes for container in by_service)
    lines += [
        f"{'all, at once':<20} {usage.summed_memory_peak_bytes / _MEBIBYTE:>18.0f} {total_cpu_seconds:>9.1f}",
        f"{'sum of the peaks':<20} {summed_peaks_bytes / _MEBIBYTE:>18.0f}",
        "",
        f"CPU cores in use on average: {total_cpu_seconds / real_time_seconds:.2f}",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("bots", nargs=2, type=Path, metavar="BOT_FOLDER")
    parser.add_argument("--map", required=True, type=Path, dest="map_file", metavar="MAP")
    parser.add_argument("--races", nargs=2, default=["R", "R"], metavar="RACE", help="T, Z, P or R (default: R R)")
    parser.add_argument("--output", type=Path, default=Path("matches") / f"measured-{time.strftime('%Y%m%d-%H%M%S')}")
    args = parser.parse_args()

    match = Match(
        player_1=Bot.from_folder(args.bots[0], race=Race(args.races[0].upper())),
        player_2=Bot.from_folder(args.bots[1], race=Race(args.races[1].upper())),
        map_file=args.map_file,
    )
    usage = MatchUsage()
    outcome: dict[str, MatchResult] = {}

    def play() -> None:
        outcome["result"] = AiArenaEngine().play(match, output_folder=args.output)

    player = threading.Thread(target=play)
    player.start()
    while player.is_alive():
        usage.sample()
        player.join(timeout=1.0)
    if "result" not in outcome:
        raise SystemExit("The match did not finish; see the error above")
    result = outcome["result"]
    print(_report(match, result, usage, result.real_time_seconds))


if __name__ == "__main__":
    main()
