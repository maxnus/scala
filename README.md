# Scala

StarCraft II ladders between bots, played on runners their members contribute. Anyone with the app can register bots,
create a ladder, password-protected if they wish, enter bots in ladders, and contribute their own machine's runners to
play the ladders' matches, on a schedule they set.

[The plan](docs/plan.md) holds the design and the order of work. So far, Scala plays one match on this machine, with
each bot in [a sandbox](docs/sandbox.md).

## Playing one match

Scala plays its matches with AI Arena's match controller, in its Docker images, so it needs x86-64 Linux and Docker
with Compose 2.20 or later, whose `docker` command your user can run. The first match downloads the images, about 11 GB.

```bash
uv sync
uv run sc2scala play path/to/MyBot path/to/OtherBot --map path/to/PylonAIE_v4.SC2Map --races T Z
```

A bot is a folder named for it, in AI Arena's layout: `run.py` for a Python bot, `<name>.dll`, `<name>.jar`,
`<name>.js` or a binary `<name>` for the others. A Windows bot, `<name>.exe`, does not play yet. The match is written
to `matches/<time>-<bot>-vs-<bot>`, or the empty folder `--output` names: `result.json`, the replay, and `logs`, which
holds each bot's own logs in `player_1-<name>` and `player_2-<name>`, and the engine's.

## Developing

| Task | Command |
|---|---|
| Set up | `uv sync --extra dev` |
| Run tests | `uv run pytest` |
| Run the tests that play a match | `SC2SCALA_TEST_MAP=path/to/map.SC2Map uv run pytest -m integration` |
| Lint | `uv run ruff check .` and `uv run ruff format --check .` |
| Type check | `uv run pyright` |
| Measure one match's memory and CPU | `uv run python tools/measure_match.py BOT BOT --map MAP` |
