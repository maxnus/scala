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

## Running a server

A server needs `sc2scala[server]`. Given a data folder, it writes a `server.toml` there with defaults for a server on
this machine alone, at http://127.0.0.1:8000, with the dev sign-in, which takes any name and no password:

```bash
uv sync --extra server
uv run sc2scala server --data path/to/server-data
```

To let people sign in with GitHub, register an OAuth app on GitHub, with `<public_url>/sign-in/github/callback` as its
callback URL, and put its client id and secret in `server.toml`. Make a token on the server's account page, and sign
the command in with it:

```bash
uv run sc2scala login http://127.0.0.1:8000
```

## Bots and ladders

Signed in to a server, upload a bot's folder, or its zip, as its next version, and make, join and enter ladders:

```bash
uv run sc2scala bot upload MyBot path/to/MyBot --race T
uv run sc2scala ladder create night-league --maps PylonAIE_v4.SC2Map TorchesAIE_v4.SC2Map --limit bot_memory_mib=2048
uv run sc2scala ladder enter night-league MyBot
uv run sc2scala ladder show night-league
```

`--protect` asks for a password, which others give with `sc2scala ladder join`. `sc2scala bot --help` and
`sc2scala ladder --help` list the rest.

## Running matches

A machine with Docker plays the matches the server draws for it once registered as a runner machine of yours, which
serves every public ladder, or the ladders named:

```bash
uv run sc2scala runner register my-desk
uv run sc2scala runner start
```

`runner register` writes the machine's settings to `~/.config/sc2scala/runner.toml`: how many matches it plays at once,
1 at first, with how many fit this machine in a comment; how many GiB of bots and maps it keeps, 20; and the weekly
windows in which it plays, at any hour at first, with an example of nights and weekends. `runner start` reads them,
downloads the engine's images first if Docker lacks them, about 11 GB, and plays. It keeps bots and maps in
`~/.cache/sc2scala`, plays each match in `~/.local/share/sc2scala/work`, and reports its result, replay and logs.
`sc2scala runner stop`, or Ctrl-C, stops it once the matches being played end; `runner stop --now`, or a second Ctrl-C,
stops them too, which voids them.

`runner service` writes a systemd user service that runs `runner start`, restarts it if it fails, and stops it as
`runner stop` does:

```bash
uv run sc2scala runner service
systemctl --user daemon-reload
systemctl --user enable --now sc2scala-runner
```

`sc2scala ladder standings LADDER` lists a ladder's bots by rating, and `sc2scala ladder matches LADDER` its latest
matches; the server's pages show both, each pair's record, every match with its replay, and every bot. A runner that
plays strangers' bots should run as a user of its own, as [the sandbox](docs/sandbox.md) says.

## Developing

| Task | Command |
|---|---|
| Set up | `uv sync --extra dev` |
| Run tests | `uv run pytest` |
| Run the tests that play a match | `SC2SCALA_TEST_MAP=path/to/map.SC2Map uv run pytest -m integration` |
| Lint | `uv run ruff check .` and `uv run ruff format --check .` |
| Type check | `uv run pyright` |
| Measure one match's memory and CPU | `uv run python tools/measure_match.py BOT BOT --map MAP` |
| Write a migration for a change to the server's models | `uv run python tools/new_migration.py "what changed"` |
