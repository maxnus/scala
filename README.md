# Scala

StarCraft II ladders between bots, played on runners their members contribute. On a Scala server, anyone signs in with
GitHub, uploads bots, makes ladders, password-protected if they wish, enters bots in them, and follows the standings,
matches and replays, all in the browser. Members contribute runners: their own machines, which play the ladders'
matches, on a schedule they set.

[The plan](docs/plan.md) holds the design and the order of work. So far, a server holds the bots, ladders, matches and
ratings, and runners play its matches, each bot in [a sandbox](docs/sandbox.md); there is no public server yet.

## In the browser

On a server's pages, once signed in:

- **Your bots:** upload a bot as a zip from your page, and its next versions from the bot's own page. The zip holds the
  bot's folder in AI Arena's layout: `run.py` for a Python bot, or `<name>.dll`, `<name>.jar`, `<name>.js` or a Linux
  binary `<name>` for the others, named for the bot. Zipping the folder itself, or the files in it, both work. A
  Windows bot, `<name>.exe`, does not play yet.
- **Ladders:** the home page lists them; a ladder's page shows its standings, by a rating fitted to every game, its
  latest matches with their replays, and each pair's record. Enter your bot there, and pause or withdraw it there or on
  the bot's page. A protected ladder's page asks for its password first.
- **Your own ladders:** make one from the home page, of its maps and with the engine's limits. Its settings page
  changes its password, limits and maps, and its runners page lists the machines that played its matches, where you
  may bar one: it then plays none of them, and the results it reported are void, unless you keep them.
- **Your page:** your bots, your runner machines and which ladders each serves, and the tokens your command line and
  runners act with.

## Contributing a runner

A runner machine plays the matches a server draws for it: every public ladder, or the ladders you choose. It needs
x86-64 Linux and Docker with Compose 2.20 or later, whose `docker` command your user can run; on Windows, it runs in
WSL2, as [the WSL2 guide](docs/wsl2.md) says, which nobody has tried on Windows yet. Get Scala from this repository,
then sign in, register the machine, and start it:

```bash
uv sync
uv run sc2scala login https://scala.example.org
uv run sc2scala runner register my-desk
uv run sc2scala runner start
```

`login` shows a code and opens the server's page in your browser, where you sign in and confirm the same code; with
`--no-browser`, as over SSH, it shows the page's address for you to open elsewhere. `runner register` writes the
machine's settings to `~/.config/sc2scala/runner.toml`: how many matches it plays at once, 1 at first, with how many fit
this machine in a comment; how many GiB of bots and maps it keeps, 20; and the weekly windows in which it plays, at any
hour at first, with an example of nights and weekends. `runner start` reads them, downloads the engine's images first if
Docker lacks them, about 11 GB, and plays. It keeps bots and maps in `~/.cache/sc2scala`, plays each match in
`~/.local/share/sc2scala/work`, and reports its result, replay and logs. `sc2scala runner stop`, or Ctrl-C, stops it
once the matches being played end; `runner stop --now`, or a second Ctrl-C, stops them too, which voids them.

`runner service` writes a systemd user service that runs `runner start` with this shell's `PATH` and folders, restarts
it if it fails, and stops it as `runner stop` does:

```bash
uv run sc2scala runner service
systemctl --user daemon-reload
systemctl --user enable --now sc2scala-runner
```

A runner that plays strangers' bots should run as a user of its own, with rootless Docker, as
[the sandbox](docs/sandbox.md) says.

## The command line

Signed in with `sc2scala login`, the command does what the pages do, and takes a bot's folder as well as its zip:

```bash
uv run sc2scala bot upload MyBot path/to/MyBot --race T
uv run sc2scala ladder create night-league --maps PylonAIE_v4.SC2Map TorchesAIE_v4.SC2Map --limit bot_memory_mib=2048
uv run sc2scala ladder enter night-league MyBot
uv run sc2scala ladder standings night-league
```

`--protect` asks for a password, which others give with `sc2scala ladder join`. `sc2scala ladder runners LADDER` lists
the machines that played a ladder's matches, and its owner bars one with `sc2scala ladder bar-runner LADDER
OWNER/MACHINE`, or `--keep-results`, and lifts the bar with `unbar-runner`. `sc2scala --help` lists the rest.

## Playing one match

`sc2scala play` plays one match on this machine, with no server, as a runner would:

```bash
uv sync
uv run sc2scala play path/to/MyBot path/to/OtherBot --map path/to/PylonAIE_v4.SC2Map --races T Z
```

A bot is a folder named for it, in AI Arena's layout, as above. The match is written to `matches/<time>-<bot>-vs-<bot>`,
or the empty folder `--output` names: `result.json`, the replay, and `logs`, which holds each bot's own logs in
`player_1-<name>` and `player_2-<name>`, and the engine's.

## Running a server

A server needs `sc2scala[server]`. Given a data folder, it writes a `server.toml` there with defaults for a server on
this machine alone, at http://127.0.0.1:8000, with the dev sign-in, which takes any name and no password:

```bash
uv sync --extra server
uv run sc2scala server --data path/to/server-data
```

To let people sign in with GitHub, register an OAuth app on GitHub, with `<public_url>/sign-in/github/callback` as its
callback URL, and put its client id and secret in `server.toml`.

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
