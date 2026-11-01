"""Plays AI Arena's test bots through `sc2scala play` on each engine, a bot of each type and each way a match can end,
and prints what each engine decided beside what was expected, with the game's time, the real time and the step times.

    git clone https://github.com/aiarena/aiarena-test-bots ~/aiarena-test-bots
    uv run python tools/try_engines.py ~/aiarena-test-bots --map ~/StarCraftII/maps/AIE/TorchesAIE_v4.SC2Map
        [--engines scala aiarena] [--cases java tie] [--output FOLDER]

The test bots are AI Arena's own, of its repository aiarena-test-bots: Python bots on its copy of python-sc2 for each
way a match can end, and a bot each of Java, Node.js and a Linux binary, in Rust. Each plays the one that only mines,
`loser_bot`; the Java and Node.js bots do not attack either, and the game itself calls a tie after 22 minutes of game
time. The cases named `computer-` play the game's built-in computer instead, which Scala's engine alone plays; none of
them wins, since every bot here that attacks rushes with its workers, which wins against the easiest computer only
sometimes. A case takes from 10 to 50 seconds. A Windows bot, which neither engine plays, and a .NET bot, which AI
Arena's test bots lack, are left out.
"""

import argparse
import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from sc2scala.computer import is_computer_name
from sc2scala.engine_names import ENGINE_NAMES, ENGINES_PLAYING_COMPUTERS

_REPOSITORY = Path(__file__).resolve().parent.parent
_OPPONENT = "loser_bot"


@dataclass(frozen=True)
class TrialCase:
    """A match of `bot`, playing `race`, against `opponent`, by default the bot that only mines, or the computer of the
    name given, and the result it should have."""

    name: str
    bot: str
    race: str
    expected: str
    opponent: str = _OPPONENT

    def plays_on(self, engine: str) -> bool:
        return engine in ENGINES_PLAYING_COMPUTERS or not is_computer_name(self.opponent)


CASES = (
    TrialCase("python-sc2", "basic_bot", "T", "Player1Win"),
    TrialCase("java", "java_bot", "R", "Tie"),
    TrialCase("nodejs", "js_bot", "R", "Tie"),
    TrialCase("linux-binary", "rust_bot", "T", "Player1Win"),
    TrialCase("crash", "crash_bot", "T", "Player1Crash"),
    TrialCase("instant-crash", "instant_crash_bot", "T", "Player1Crash"),
    TrialCase("infinite-loop", "infinite_loop_bot", "T", "Player1TimeOut"),
    TrialCase("leaver", "leaver_bot", "T", "Player2Win"),
    TrialCase("never-connects", "connect_timeout_bot", "T", "InitializationError"),
    TrialCase("tie", "tie_bot", "T", "Tie"),
    TrialCase("computer-loss", "loser_bot", "T", "Player2Win", "Computer-VeryHard-Random-RandomBuild"),
    TrialCase("computer-crash", "crash_bot", "T", "Player1Crash", "Computer-VeryEasy-Protoss-Macro"),
    TrialCase("computer-leaver", "leaver_bot", "T", "Player2Win", "Computer-VeryEasy-Terran-Air"),
    TrialCase(
        "computer-never-connects", "connect_timeout_bot", "T", "InitializationError", "Computer-Easy-Zerg-Timing"
    ),
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("test_bots", type=Path, help="a clone of aiarena/aiarena-test-bots")
    parser.add_argument("--map", required=True, type=Path, dest="map_file")
    parser.add_argument("--engines", nargs="+", choices=ENGINE_NAMES, default=list(ENGINE_NAMES))
    parser.add_argument("--cases", nargs="+", choices=[case.name for case in CASES], help="only these cases")
    parser.add_argument(
        "--output", type=Path, default=Path("matches") / f"engine-trials-{time.strftime('%Y%m%d-%H%M%S')}"
    )
    args = parser.parse_args()
    rows = []
    for case in CASES:
        if args.cases and case.name not in args.cases:
            continue
        for engine in [engine for engine in args.engines if case.plays_on(engine)]:
            row = _played(case, engine, test_bots=args.test_bots, map_file=args.map_file, output=args.output)
            rows.append(row)
            print(json.dumps(row), flush=True)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "trials.json").write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    unexpected = [row for row in rows if row["result"] != row["expected"]]
    print(f"{len(rows) - len(unexpected)} of {len(rows)} as expected; written to {args.output}")


def _played(case: TrialCase, engine: str, *, test_bots: Path, map_file: Path, output: Path) -> dict[str, object]:
    """Plays `case` on `engine` and returns what came of it."""
    match_folder = output / engine / case.name
    started = time.monotonic()
    command = [
        *("uv", "run", "sc2scala", "play", str(test_bots / case.bot), _opponent_argument(case, test_bots)),
        *("--map", str(map_file), "--races", case.race, "T", "--engine", engine, "--output", str(match_folder)),
    ]
    finished = subprocess.run(command, cwd=_REPOSITORY, capture_output=True, text=True, check=False)
    row: dict[str, object] = {"case": case.name, "engine": engine, "expected": case.expected}
    result_file = match_folder / "result.json"
    if finished.returncode != 0 or not result_file.is_file():
        return {**row, "result": f"failed: {finished.stderr.strip()[-300:]}"}
    result = json.loads(result_file.read_text(encoding="utf-8"))
    step_times = [player["average_step_time"] for player in result["players"]]
    return {
        **row,
        "result": result["result"],
        "game_seconds": result["game_seconds"],
        "real_time_seconds": result["real_time_seconds"],
        "wall_seconds": round(time.monotonic() - started, 1),
        "step_ms": [None if step_time is None else round(step_time * 1000, 3) for step_time in step_times],
        "replay": result["replay"] is not None,
    }


def _opponent_argument(case: TrialCase, test_bots: Path) -> str:
    return case.opponent if is_computer_name(case.opponent) else str(test_bots / case.opponent)


if __name__ == "__main__":
    main()
