# Plan

Scala runs StarCraft II ladders between bots. Anyone with the app can register bots, create a ladder, password-protected
if they wish, enter bots in ladders, and contribute runners: their own machine playing the ladders' matches, on a
schedule they set. Bots are paired at random for now, and ranked by a rating fitted to their results.

This page holds what the owner decided on 2026-10-24, the design built on it, and the order of work. **Decided** stands
until the owner reverses it; **Design** is a proposal, to be changed in review; **Open** is settled when its step comes.

## Decided

1. **One server, which anyone can host.** It holds the accounts, bots, ladders, matches, replays and ratings, and serves
   the ladders' pages. The owner hosts the first one; nothing in the app assumes it is the only one. Peer to peer was
   dropped: ratings and the match queue need one record of what happened.
2. **The games are played by AI Arena's match controller**,
   [`aiarena/sc2-ai-match-controller`](https://github.com/aiarena/sc2-ai-match-controller) (Rust, GPL-3.0), in the
   Docker images [`local-play-bootstrap`](https://github.com/aiarena/local-play-bootstrap) runs: `arenaclient-sc2`,
   `arenaclient-bot` and `arenaclient-proxy`, v0.8.0 at the time of writing. The runner drives them as a separate
   program, from a match file and a compose file, and reads back the result and the replay. They already run each bot
   in a container of its own, put a proxy between the bots and the game that enforces the time limits and blocks debug
   commands, decide a crash's result, and start every bot type AI Arena takes, so an AI Arena bot plays here unchanged.
   The engine sits behind an interface of Scala's own, so that one written on `sc2nachos` could replace it.
3. **A runner serves the ladders its owner picks**: every public ladder, a list of ladders, or both. A
   password-protected ladder's matches go only to runners whose owners gave its password. A bot's files go only to the
   machines of ladders its author entered it in, and a ladder's owner can remove a runner and void its results.
4. **Ratings are a Bradley-Terry fit, redone after every result**, on the Elo scale, with an interval. A bot does not
   change between uploads, so a fit of all its games is more accurate than an update that assumes skill drifts, as
   Glicko-2 and TrueSkill do, and it does not depend on the order the games were played in. Computer chess rating
   lists rate engines this way (CCRL, with BayesElo). A new version of a bot starts a new rating.
5. **The engine is Linux, and Windows runs it in WSL2.** The app, Docker and the engine run inside the WSL2
   distribution, and the server's pages open in the Windows browser. Windows bots do not play for now: the engine's
   code starts a `cppwin32` bot under Wine, but its v0.8.0 bot image holds none (found in step 2).
6. **A repository of its own, in Python**: `maxnus/scala`, shipped as `sc2scala`, the package, the import and the
   command, as NachOS ships as `sc2nachos`. Bots talk to the engine through the ladder's command line (`--GamePort`,
   `--StartPort`, `--LadderServer`, `--OpponentId`), so a bot on any library plays.
7. **People sign in with GitHub** (the owner, 2026-10-24, for step 3). The server keeps no passwords; the CLI and the
   runners act for a user with tokens made on the account page and kept as hashes. A dev sign-in by any name serves
   tests and a server on one's own machine, and is refused unless the server listens on a local address.
8. **The server renders its pages**, with no JavaScript build. In step 3 they only show; what changes something goes
   through the CLI.

## Design

### Bots

A bot is a name, a race (Terran, Zerg, Protoss or random), AI Arena's type for it and an upload, a zip in AI Arena's
layout. Scala reads the type from the first of these the bot's folder holds (`BotType.from_folder`), and the engine
starts the bot as its type says:

| The folder holds | Started as | AI Arena's type |
|---|---|---|
| `run.py` | `python run.py` | `python` |
| `<name>.dll` | `dotnet <name>.dll` | `dotnetcore` |
| `<name>.jar` | `java -jar <name>.jar` | `java` |
| `<name>.js` | `node <name>.js` | `nodejs` |
| `<name>.exe` | `wine <name>.exe`, refused: the v0.8.0 bot image holds no Wine | `cppwin32` |
| `<name>` | `./<name>` | `cpplinux` |

The engine finds the entry point by the bot's name, so a name follows its rules, and an upload is checked for one when
it arrives. Every upload is a version, kept by its hash and never changed. A ladder plays each entered bot's latest
version, and a version's rating and games stay in the bot's history once a newer one replaces it.

### Ladders

A ladder is a name, an owner, an optional password, a map pool and the engine's limits: the game time, the time a bot
may take for one step, and the real time a match may take. They default to those of AI Arena's local setup: 80640
steps, an hour of game time; 40 ms, with ten strikes; and two hours. A game that reaches its game time ends as a tie.
Anyone can create a ladder.

Whoever gives a protected ladder's password once becomes a member. Members alone can enter bots, serve it with a
runner, and see its pages, matches and replays. A public ladder's pages are open to everyone, and anyone signed in can
enter a bot.

Whoever sees a ladder sees the runner machines that played its matches, and its owner can bar one, by its owner and
name. A barred machine draws none of the ladder's matches; those it is playing there are void, and so are its results
there, unless the owner keeps them, as for a runner barred for being slow rather than dishonest. The ratings are then
fitted without them, and an entry their failed starts paused plays again. A match names its runner when it is drawn,
so that a machine's results can be voided after it is removed, and the bar holds the name if it is registered again.

### Matches

A runner asks the server for a match when it has a free slot, and the server makes one then; there is no queue of
matches made in advance. The server picks:

1. a ladder among those the runner serves that have two active bots or more, each with weight its number of active
   bots, so that a bot gets about as many games whichever ladder it is in;
2. two different active bots of it, uniformly at random, in random seats;
3. a map of its pool, uniformly at random.

The match is leased to the runner until a deadline, the ladder's real-time limit and a margin. A result after the
deadline, or none, voids it; the next match is drawn afresh, so none is played again. A bot may play several matches at
once, since nothing it keeps carries from one to the next ([open](#open)).

### Results and ratings

The runner uploads the engine's result, the game's steps, each bot's average step time, the replay and both bots'
logs. A replay is for anyone who can see the ladder, and a bot's log for its author alone.

| Engine result | Counts as |
|---|---|
| `Player1Win`, `Player2Win` | a win and a loss |
| `Tie`, the game time included | half a win each |
| `Player1Crash`, `Player1TimeOut`, and player 2's | a loss for the bot that crashed or ran out of time |
| `InitializationError`, `Error`, a void match | nothing; listed in the history |

The fit is Bradley-Terry: bot A beats bot B with probability `1 / (1 + 10^((R_B - R_A) / 400))`. It is the most likely
set of ratings under a normal prior centered on the ladder's mean, which keeps a bot of few games near the middle, and
each rating's interval comes from the fit's curvature, about the ladder's mean. The prior's width, 100 Elo points, was
chosen in step 3 as the one that best predicted AI Arena's 2026 season, from
[the matchmaking prototype](https://github.com/maxnus/sc2-matchmaking-prototype)'s data. A refit of a few thousand games
takes milliseconds. Since many match-ups are one-sided beyond what the ratings predict (the prototype's finding), a
ladder's page also shows each pair's record.

### Runners

A runner plays one match at a time, on a machine that runs `sc2scala runner`, a service: systemd on Linux, and in WSL2,
which runs systemd too. The machine's config file sets how many runners it contributes, the ladders they serve, and the
weekly windows in which they play, in the machine's time zone:

```toml
# ~/.config/sc2scala/runner.toml
server = "https://scala.example.org"
runners = 4
ladders = ["public", "friday-league"]

[[window]]
days = "mon-fri"
from = "23:00"
to = "07:00"

[[window]]
days = "sat-sun"
```

A runner asks for a match only inside a window. When a window ends, it takes no new match and lets the one it is
playing finish, which the real-time limit bounds. `sc2scala runner stop` does the same at once, and with `--now` stops
the matches being played, which voids them. Every connection goes out from the machine, so a runner needs no open port,
behind a home router or in WSL2. The machine keeps downloaded bot versions and maps by their hash.

The game client, the Linux StarCraft II 4.10, comes in the `arenaclient-sc2` image, so a machine needs only x86-64
Linux, Docker Engine with Compose, and the app.

### The sandbox

A runner executes strangers' code, so every match runs sandboxed, as [the sandbox](sandbox.md) says in full:

- each bot's container with its own bot folder alone, read-only but for its `data` folder and `/tmp`, held in memory;
- on internal networks, one for each bot and one for the game, so that a bot reaches the proxy and nothing else;
- as the runner's user, with no capabilities and no way to gain any;
- within each bot's limits of CPU, memory, processes and logs, `ResourceLimits`, set by the ladder;
- in fresh containers, with nothing of the runner's own, its token least of all, mounted anywhere.

A runner that plays strangers' bots runs as a user of its own, with rootless Docker, so that a bot that breaks out
lands as a user who owns nothing of its owner's.

### The server

Python, FastAPI, PostgreSQL in deployment and SQLite in tests, and files on disk: bot versions, maps, replays and logs.
The app's commands (`sc2scala bot upload`, `sc2scala ladder create`, `sc2scala ladder join`) and the runners call the
same HTTP API, with a token of the user's.

### Testing

`pytest`, as in NachOS. The server and the runner are tested against a fake engine that returns recorded results.
Tests that start the engine need Docker and are marked `integration`.

## Order of work

Each step ends in something that runs, and is a pull request of its own.

1. **One match, no server.** Done: `sc2scala play <bot folder> <bot folder> --map <map>` plays one match through the
   engine and writes the result and the replay, and `tools/measure_match.py` measures the memory and CPU of one match,
   which sets how many runners a machine can take.
2. **The sandbox.** Done: as above, tested by bots that try to break each wall. The rootless setup is documented, and
   not yet tried.
3. **The server and one runner**, done, in four pull requests:
   - 3a, done: the server's config and database, sign-in with GitHub and the dev sign-in, tokens, and `sc2scala server`,
     `login`, `whoami` and `logout`.
   - 3b, done: bots and versions, ladders, maps and membership, and their commands. A bot's entry pauses after 5
     failed starts in a row, which 3c counts.
   - 3c, done: the matches as above, results, and `sc2scala runner`, which plays one match at a time, at any hour.
     An `InitializationError` counts as a failed start for both bots, since it names neither.
   - 3d, done: ratings, the prior's width chosen on the prototype's AI Arena results (100 Elo points, by
     `tools/choose_rating_prior.py`), and each ladder's pages: standings, matches with their replays, and each pair's
     record.
4. **Many runners, windows and deadlines.** The config file above, `runner stop`, a ladder's owner barring a runner
   and voiding its results, and pruning the runner's cache. The server voids matches past their deadline since 3c.
5. **Windows.** An install guide for WSL2, tested on a Windows machine.
6. **The first public server**, on the owner's VPS.

## Open

- **The license.** NachOS is MIT. The engine is GPL-3.0, and Scala only runs its images, as separate programs.
- **What a bot keeps between games**, as AI Arena's `data` folder: left out. With it, a bot plays one match at a time.
- **Checking a result against its replay**, against a runner that reports falsely.
- **Matchmaking beyond random**: the prototype compares random pairing with round-robin, rung and a stochastic
  matchmaker on AI Arena's results.
- **Seasons**: whether a ladder restarts its ratings.

## Prior art

[AI Arena](https://aiarena.net) runs its own competitions, on arena clients that fetch its matches, and its code is
open: the site, [`aiarena-web`](https://github.com/aiarena/aiarena-web) (Django), and the match controller above, both
GPL-3.0. Scala adds what AI Arena does not have: anyone's ladders, protected ladders, and runners that choose their
ladders and their hours.
