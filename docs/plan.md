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
2. **The games are played by Scala's own engine**, in Python, in AI Arena's Docker images for the game and for the
   bots (the owner, 2026-11-01, for step 8, reversing the first choice, AI Arena's match controller). Steps 1 to 7 ran
   [`aiarena/sc2-ai-match-controller`](https://github.com/aiarena/sc2-ai-match-controller) (Rust, GPL-3.0) in the
   images [`local-play-bootstrap`](https://github.com/aiarena/local-play-bootstrap) runs, v0.8.0. Those come from
   upstream's `v0` branch, which AI Arena keeps for playing on one's own machine and changed once since it split from
   `master` in March 2025, while aiarena.net runs `master`, rewritten for its Kubernetes cluster, which hard-codes the
   game's limits; and "v0.8.0" names the bot environment inside, not a release, the last GitHub release being v0.6.10
   of 2024. Everything wanted next needed changes to it: [computer opponents](#computer-opponents), which it cannot
   seat; the step-time limit, which it reads and ignores; and the bot environment aiarena.net has moved on to. Scala's
   engine keeps what made AI Arena's worth taking, its contract with the bots, so that an AI Arena bot plays here
   unchanged, as [the engine](#the-engine) says.
3. **A runner serves the ladders its owner picks**: every public ladder, a list of ladders, or both. A
   password-protected ladder's matches go only to runners whose owners gave its password. A bot's files go only to the
   machines of ladders its author entered it in, and a ladder's owner can remove a runner and void its results.
4. **Ratings are a Bradley-Terry fit, redone after every result**, on the Elo scale, with an interval. A bot does not
   change between uploads, so a fit of all its games is more accurate than an update that assumes skill drifts, as
   Glicko-2 and TrueSkill do, and it does not depend on the order the games were played in. Computer chess rating
   lists rate engines this way (CCRL, with BayesElo). A new version of a bot starts a new rating.
5. **The engine is Linux, and Windows runs it in WSL2.** The app, Docker and the engine run inside the WSL2
   distribution, and the server's pages open in the Windows browser, as [the WSL2 guide](wsl2.md) says, untested so
   far. Windows bots do not play for now: AI Arena's engine starts a `cppwin32` bot under Wine, but its bot image
   holds none (found in step 2), so Scala's refuses one.
6. **A repository of its own, in Python**: `maxnus/scala`, shipped as `sc2scala`, the package, the import and the
   command, as NachOS ships as `sc2nachos`. Bots talk to the engine through the ladder's command line (`--GamePort`,
   `--StartPort`, `--LadderServer`, `--OpponentId`), so a bot on any library plays.
7. **People sign in with GitHub** (the owner, 2026-10-24, for step 3). The server keeps no passwords; the CLI and the
   runners act for a user with tokens kept as hashes: made on the account page, or by `sc2scala login`, which makes
   its token itself and has the user confirm its code in the browser (step 6c). A dev sign-in by any name serves
   tests and a server on one's own machine, and is refused unless the server listens on a local address.
8. **The server renders its pages**, with no JavaScript build, and people do in them everything but run a runner
   (the owner, 2026-10-24, reversing step 3's pages that only showed): a bot's author needs no install. The CLI does
   the same through the API, and runs the runners.
9. **The server keeps SQLite**, with write-ahead logging, until a server outgrows it (the owner, 2026-10-31, for step
   7a): one process, with a few runners reporting a result every few minutes, is well within it.
10. **A bot or a ladder is deleted only if it never played**, and its name is free again; one that played is retired
    instead, so that every match keeps its players and its ladder (the owner, 2026-10-31, for step 7a).
11. **Adding a runner is far smoother than [the sandbox](sandbox.md)'s steps by hand**, a dozen commands with sudo
    among them, since contributors add runners to ladders that are not theirs (the owner, 2026-10-31, for step 7c).
    [Adding a runner](#adding-a-runner) proposes one command.
12. **A ladder may hold the game's built-in computer** as opponents for its bots (the owner, 2026-11-01, for step 8):
    each of a race, Terran, Zerg, Protoss or random, a difficulty, one of the game's ten, and a build, one of its
    five or random, and as many as its owner adds. A random race is the game's own, so that the bot is not told it,
    rather than one Scala rolls. Two computers never play each other.

## Design

### Bots

A bot is a name, a race (Terran, Zerg, Protoss or random), AI Arena's type for it and an upload, a zip in AI Arena's
layout, its files at the zip's root or in the one folder it holds. Scala reads the type from the first of these the
bot's folder holds (`BotType.from_folder`), and the engine starts the bot as its type says:

| The folder holds | Started as | AI Arena's type |
|---|---|---|
| `run.py` | `python run.py` | `python` |
| `<name>.dll` | `dotnet <name>.dll` | `dotnetcore` |
| `<name>.jar` | `java -jar <name>.jar` | `java` |
| `<name>.js` | `node <name>.js` | `nodejs` |
| `<name>.exe` | refused: AI Arena's bot image holds no Wine to run it | `cppwin32` |
| `<name>` | `./<name>` | `cpplinux` |

The engine finds the entry point by the bot's name, so a name follows its rules, and an upload is checked for one when
it arrives. Every upload is a version, kept by its hash and never changed. A ladder plays each entered bot's latest
version, and a version's rating and games stay in the bot's history once a newer one replaces it.

Its author deletes a bot that was never drawn for a match, with its versions. One that was is retired instead: it
leaves every ladder but the retired ones, takes no new version, and is left out of the list of bots, while its
matches, ratings and name stay.

### Ladders

A ladder is a name, an owner, an optional password, a map pool and the engine's limits: the game time, the time a bot
may take for one step, and the real time a match may take. They default to those of AI Arena's local setup: 80640
steps, an hour of game time; 40 ms, with ten strikes; and two hours. A game that reaches its game time ends as a tie.
AI Arena's v0.8.0 engine reads the step-time limit but never applies it, so that until step 8a a bot may take up to
the 30 seconds it allows any request. Anyone can create a ladder, and its owner may add
[computer opponents](#computer-opponents) to it.

Whoever gives a protected ladder's password once becomes a member. Members alone can enter bots, serve it with a
runner, and see its pages, matches and replays. A public ladder's pages are open to everyone, and anyone signed in can
enter a bot.

Its owner deletes a ladder that never drew a match. One that did is retired instead: it draws no more matches, takes
no new entry, no machine serves it, and it is left out of the list of ladders, while its standings, as they ended, its
matches and its name stay. A match being played when it retires still counts once reported.

Whoever sees a ladder sees the runner machines that played its matches, and its owner can bar one, by its owner and
name. A barred machine draws none of the ladder's matches; those it is playing there are void, and so are its results
there, unless the owner keeps them, as for a runner barred for being slow rather than dishonest. The ratings are then
fitted without them, and an entry their failed starts paused plays again. A match keeps its runner's owner and name
when it is drawn, so that a machine's results can be voided after it is removed; a bar holds whatever the owner's login
becomes, and the name if it is registered again. A bar holds one machine, so its owner's other machines play on until
barred too.

### Matches

A runner asks the server for a match when it has a free slot, and the server makes one then; there is no queue of
matches made in advance. The server picks:

1. a ladder among those the runner serves that have two active bots or more, each with weight its number of active
   bots, so that a bot gets about as many games whichever ladder it is in;
2. two different active bots of it, uniformly at random, in random seats;
3. a map of its pool, uniformly at random.

A ladder with [computer opponents](#computer-opponents) draws its two players among its bots and its computers alike,
any pair but two computers, and plays once it has one bot and one computer; its weight then allows for the seats its
computers take, so that a bot still gets about as many games whichever ladder it is in.

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

### Computer opponents

A ladder's owner adds the game's built-in computer to it, on its settings page or by `sc2scala ladder add-computer`,
as many as they like, each of:

- a race: Terran, Zerg, Protoss or random;
- a difficulty: `VeryEasy`, `Easy`, `Medium`, `MediumHard`, `Hard`, `Harder`, `VeryHard`, `CheatVision`,
  `CheatMoney` or `CheatInsane`;
- a build: `Rush`, `Timing`, `Power`, `Macro`, `Air` or random.

A computer is named for these, as *Computer VeryHard Zerg Rush*, and a ladder holds each such computer once. It plays
through [Scala's engine](#the-engine), which creates the game with a seat for the bot and one for the computer, in one
game client, which the bot joins alone; AI Arena's engine always creates a game for two bots.

A random race and a random build are the game's own, `Random` and `RandomBuild`, rather than ones Scala rolls: the bot
is told its opponent is random, as of a random bot, and the match records the race as random, since the game tells no
player which race a computer got (NachOS's sweeps found its `race_actual` never filled). The game does tell a bot the
computer's difficulty and build, in its game information, which stays as it is, as any bot playing the built-in
computer sees them; the bot's `--OpponentId` is the computer's name.

A computer neither crashes nor runs out of time, and has no logs or step time; an `InitializationError` counts only
for the bot. It is rated in its ladder as a bot is, a row of the standings, and as one player for good, since it never
changes: so it anchors the ladder's ratings over time, as no bot's version does. Its owner pauses or removes it, and
one that played is retired rather than deleted, as a bot is (decision 10), so that its matches keep their player.

Only a runner whose Scala plays computers draws a match with one: a runner names its engine when it asks for a match.

### Runners

A machine contributes runners: it plays as many matches at once as it has runners, each a thread of one
`sc2scala runner start`, which runs as a service, systemd's on Linux and in WSL2, which runs systemd too. The ladders it
serves are the server's to know, set by `sc2scala runner register`, `runner set` and the pages, and
[the setup command](#adding-a-runner) does all of it in one. How many runners it has, how much disk its cache may take,
and the weekly windows in which it plays, in its own time zone, are its own, in a file beside its token that `runner
register` writes with the defaults:

```toml
# ~/.config/sc2scala/runner.toml
runners = 4
cache_gib = 20

[[window]]
days = "mon-fri"
from = "23:00"
to = "07:00"

[[window]]
days = "sat-sun"
```

A runner asks for a match only inside a window; one that crosses midnight belongs to the days it starts on, and with
none, a runner plays at any hour. When a window closes, the runners take no new match and let those they play finish,
which the real-time limit bounds, then wait for the next window to open. A match that is ready to start too late to end
by its deadline, with 5 minutes to start and report, its bots slow to download say, is reported unplayed rather than
started. `sc2scala runner stop`, or SIGTERM, as systemd sends, stops the runners once their matches end, and `--now`,
SIGQUIT or a second Ctrl-C stops the matches too, which voids them. One `runner start` runs per user. Every connection
goes out from the machine, so a runner needs no open port, behind a home router or in WSL2. The machine keeps the bot
versions and maps it downloaded, by their hash, the least recently used going once they pass `cache_gib`, and the logs
of the 10 latest matches it could not play or whose result the server did not take.

A match of light bots takes about 2.2 GB and 1.5 cores at its peak (step 1), so `runner register` suggests one runner
per 4 cores and 8 GiB. A ladder's default limits let one match take up to 15 GiB: 4 for each bot, 6 for the game and 1
for the match controller.

The game client, the Linux StarCraft II 4.10, comes in AI Arena's `arenaclient-sc2` image, so a machine needs only
x86-64 Linux, Docker Engine with Compose, and the app.

### The sandbox

A runner executes strangers' code, so every match runs sandboxed, as [the sandbox](sandbox.md) says in full:

- each bot's container with its own bot folder alone, read-only but for its `data` folder and `/tmp`, held in memory;
- on internal networks, one for each bot and one for the game, so that a bot reaches the match controller and nothing
  else;
- as the runner's user, with no capabilities and no way to gain any;
- within each bot's limits of CPU, memory, processes and logs, `ResourceLimits`, set by the ladder;
- in fresh containers, with nothing of the runner's own, its token least of all, mounted anywhere.

A runner that plays strangers' bots runs as a user of its own, with rootless Docker, so that a bot that breaks out
lands as a user who owns nothing of its owner's.

### The engine

Scala's engine plays one match in Docker containers, as AI Arena's did, behind the same interface, `MatchEngine`, so
that the runner, the server and the sandbox stay as they are:

- **the game**: a Linux StarCraft II 4.10 for each bot, from AI Arena's `arenaclient-sc2` image, started directly
  rather than by the image's own controller;
- **each bot**, in AI Arena's bot environment, the image `arenaclient-bot-base`, started as AI Arena starts it: in its
  folder, as [its type](#bots) says, with `--GamePort` and `--StartPort` naming the match controller's port,
  `--LadderServer` its address and `--OpponentId` the opponent's name, and its output in its log; a bot that exits
  within its first seconds failed to start;
- **the match controller**, Scala's own, between the bots and the game, in a container on every network of the match.

The match controller does what AI Arena's does, as its v0.8.0 source shows:

- it creates the game itself, with a seat for each bot, or for the bot and [the computer](#computer-opponents);
- it passes each bot's `JoinGame` on with its seat's race, its name and the match's ports, or no ports for a bot that
  plays the computer, alone in its game, then each of its requests to the game and the answer back;
- it answers a debug request itself, with an empty answer, so that no bot cheats;
- in the game's information, it names both players, and shows the opponent's race as requested, so that a random
  opponent stays random;
- it ends a game that reaches its game time as a tie, and saves the replay however the game ends;
- it decides the result: the game's; a crash for a bot whose connection breaks; a time-out for one that sends nothing
  for 30 seconds; and a loss for one that leaves the game;
- it reports the game's steps, and each bot's average step time, its own time between the game's answers and its next
  requests over the game's steps, as AI Arena's counts it.

Beyond AI Arena's, it applies the ladder's step-time limit. A bot's step lasts from the game's answer to one step
request to its next, less the time the game took meanwhile to answer its other requests; each step longer than
`max_step_time_ms` is a strike, and a bot that reaches `step_time_strikes` loses by a time-out. The results stay those
of the table above.

The images are AI Arena's, at tags Scala pins; the bot environment's is Scala's choice, v0.8.0 as now or v0.9.0 as
aiarena.net's, which step 8a settles by trying bots on both. The match controller runs from an image the runner pulls
anyway, with Scala's code and the few packages it needs mounted read-only, so that Scala publishes no image of its own;
which image, step 8a settles too.

The match controller is tested in-process, against a fake game and fake bots, for each way a game can end, with no
Docker; tests that start containers are marked `integration`, as now. Until step 8c, AI Arena's engine stays, chosen
by `engine` in `runner.toml` and `sc2scala play --engine`, so that both can play the same matches. Before it goes,
bots of every type play test-ladder through Scala's: Python on `sc2nachos` and on python-sc2, and a bot each of .NET,
Java, Node.js and a Linux binary.

### Adding a runner

A contributor adds a runner as one adds a self-hosted runner to GitHub: a page gives them a command to paste, and the
command does the rest.

1. **On the pages.** *Add a runner*, on one's account page and on the page of each ladder one may serve, asks which
   ladders the machine serves: from the account page, every public ladder, ticked at first; from a ladder's page, that
   ladder alone; and each protected ladder one is a member of, unticked. It answers with a setup code, which works
   once, within an hour, and with the command that uses it:

   ```bash
   curl -fsSL https://scala.example.org/runner/setup.sh | sudo bash -s -- SETUP-CODE
   ```

   It shows the script's SHA-256 beside it, and the same in three lines that download the script, check it against
   that hash, and run it, for those who read a script before running it as root. The account page shows the command
   without a code for a machine already set up, which updates it.

2. **The script**, which says what it does at each step:
   - checks the machine first, and stops with the reason: run as root, x86-64, Ubuntu 24.04 or later with systemd,
     which Ubuntu in WSL2 has once it is turned on, untested until a Windows machine tries it; 16 GiB of memory, for
     the 15 GiB one match may take under a ladder's default limits and the system beside it; and 50 GB free where the
     user's home will be, `/home`, 28 for the images once unpacked and 20 for the runner's cache;
   - installs what is missing of Docker, Compose and what rootless Docker needs, from Ubuntu's packages, or Docker's
     own rootless extras where Docker's packages are installed; Ubuntu 24.04's docker.io lacks Docker's two rootless
     scripts, which 26.04's ships, so the script downloads them from Docker's repository at the same version, checked
     against their SHA-256, byte for byte 26.04's;
   - makes the user `sc2scala`, whose services run while nobody is logged in, and marks it as the script's;
   - as that user, sets up rootless Docker, and checks what the sandbox needs: that Docker reports `rootless`, and
     that the user may limit CPU, memory and processes; a failed check stops it;
   - installs uv and Scala for that user;
   - registers the machine by the setup code, under the machine's host name made a valid machine name, letters,
     digits and '-', 40 at most, or under `--name`, with as many runners as the machine's cores and memory suggest;
   - downloads AI Arena's images, about 11 GB, showing its progress, and then starts the runner's service;
   - ends by naming the machine, its owner, the ladders it serves, and the page that shows it.

   It asks nothing, since its input is its own download: what it may not decide alone, it refuses, and names the flag
   that allows it. A user `sc2scala` it did not make, as the sandbox's steps by hand make one, it leaves alone and
   stops. Run again on a machine it set up, it updates Scala and keeps the registration, and ignores a setup code, so
   that the command in the shell's history works too. `--remove` removes the machine from the server, by its own
   token, and from the computer what the script made: the service, the user, and its Docker's images.

3. **Afterwards**, everything is in the pages, as since step 6b: the ladders a machine serves, and removing it. Its
   number of runners and its windows stay in `runner.toml`, as the guides say.

On the server, a setup code is kept as its hash, with its user, its ladders and when it expires. Since exchanging it
for a machine takes no other token, it is as long and as random as a token, unlike a login request's code, which only
works once the user confirms it in the browser. The exchange gives the machine's token, once. It refuses a name the
user's machines have already, unless the script passes `--replace`, for a reinstalled computer, which then keeps its
machine and its matches; host names repeat too often, `ubuntu` the first server's among them, for a second computer
to take over the first's machine unasked. The code shows in the shell's history and the process list while the script
runs, which matters little, since it works once and for an hour.

The script is part of the package, and the server serves it as it is, with its own address filled in: whoever controls
a server runs code as root on each machine that runs its setup command, the first time and at every update, so the
script a contributor runs is the one in the repository, and its hash on the page lets them check that. The runner the
script leaves runs as `sc2scala`, never as root.

The script refuses what it does not know, and points to [the sandbox](sandbox.md)'s steps, which stay, for doing it by
hand on another Linux. Two other ways were weighed and left out:

- **A runner as one's own user, with no sudo**: a bot that broke out of its container would land as the contributor,
  with their files.
- **A runner shipped as a Docker image**: it would need the host's Docker socket to start the match's containers,
  which is root's, so a bot that broke out would own the machine.

Scala comes from PyPI once it is published there, which needs the license and a public repository. Until then the
script takes a wheel, by `SC2SCALA_PACKAGE`, a path or an address, so that the owner's machines test it first, and
installs nothing without one: the name `sc2scala` is free on PyPI, and a stranger's package of that name would run as
the runner's user.

### The server

Python, FastAPI, SQLite with write-ahead logging, and files on disk, each kept once by its hash: bot versions, maps,
replays and logs. A match's logs are kept 30 days by default, its replay for good, and each hour the server removes the
files no row holds. `sc2scala server backup` copies the database while the server runs.
The pages' forms, signed in, and the app's commands (`sc2scala bot upload`, `sc2scala ladder create`, `sc2scala ladder
join`) and the runners, by a token of the user's, do the same through functions they share: each form calls what its
API route calls.

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
4. **Many runners, windows and barring runners**, done, in two pull requests. The server voids matches past their
   deadline since 3c.
   - 4a, done: the runners above, their file, windows, `runner stop` and `runner service`, and pruning the cache.
   - 4b, done: a ladder's runners listed, and its owner barring one, which voids its results there.
5. **Windows.** Done, untested: [a guide to a runner in WSL2](wsl2.md), written from Microsoft's documentation of WSL,
   since no Windows machine was at hand (the owner, 2026-10-24). The first runner on Windows tests it, and its report
   corrects it.
6. **The browser**, done: everything but running a runner done in the pages, in three pull requests.
   - 6a, done: bots and entries: a new bot and its versions, joining a protected ladder, and entering, pausing,
     resuming and withdrawing bots. A zip of the bot's folder itself is read as that folder.
   - 6b, done: ladders and runners, for their owners: making a ladder and changing its password, limits and maps; a
     ladder's runners, and barring them; and which ladders one's machines serve, and removing one.
   - 6c, done: `sc2scala login` signed in through the browser, by a code the user confirms, and the README begun in
     the browser.
7. **The first public server**, on the owner's VPS, in three steps:
   - 7a, done: what a public server needs: SQLite writing ahead, logs dropped once old and unused files removed,
     `sc2scala server backup`, deleting and retiring bots and ladders, the list of bots, and
     [a guide to a public server](server.md), behind Caddy, which limits a request's body, since the app receives an
     upload whole before it checks who sent it.
   - 7b, done: the owner's VPS serves the first ladder, by the guide, which its install corrected, since 2026-10-31:
     at an sslip.io name, with the owner's GitHub app, beside another site behind the same Caddy, with a copy of its
     database each night on the VPS itself. Copying the copies and the files off the VPS is not set up yet.
   - 7c: adding a runner by one command, as [above](#adding-a-runner), in two pull requests: first, done, the setup
     codes, *Add a runner*, registering by a code, which the CLI's `runner register --setup-code` uses too, and a
     machine removing itself by its token, `runner remove` with no name; then, done, the script, `runner pull`, and
     `tools/try_runner_setup.py`, which tried it in fresh Ubuntu 24.04 and 26.04 VMs under KVM on max-9950, each from
     nothing to a runner that played a match, against a server of the branch rather than the public one, and then
     updated and removed it. The guides, [the sandbox](sandbox.md) and [WSL2](wsl2.md), send people to the command,
     and keep the steps by hand for the rest. max-9950 itself serves test-ladder on the public server since
     2026-11-01, set up by the command, and waits for the ladder's bots.
8. **Scala's own engine, and computer opponents**, as [the engine](#the-engine) and
   [computer opponents](#computer-opponents) say, in three pull requests:
   - 8a: Scala's engine playing bot against bot, with the step-time limit applied, and AI Arena's still chosen by
     `runner.toml`; tried with bots of every type in `sc2scala play`.
   - 8b: computer opponents, on the server, its pages and the CLI, in the draw and the ratings, and in the engine.
   - 8c: AI Arena's engine removed, once Scala's has played test-ladder with bots of every type on the owner's
     machines; `runner pull`, the setup script and the guides then fetch only the images Scala's uses.

## Open

- **The license.** NachOS is MIT. AI Arena's code is GPL-3.0; Scala runs its images as separate programs, and its
  engine is written to the behavior AI Arena's source and its bots show, not translated from that code. It is needed
  before Scala goes on PyPI, from which strangers' runners install it.
- **Updating runners**: running the setup command again updates one. The server could tell a runner that its Scala is
  too old for it, once a change needs every runner updated; step 8b's runner naming its engine is a start.
- **Which bots try each type** in step 8a: open-source ones, whose licenses let a runner play them.
- **A random computer's race**, read from the replay once the match is over, for the match's page.
- **Other Linux distributions** for the setup script, once someone asks. Debian 13 should take it, where Debian 12's
  packages hold no Compose 2.20, which the engine needs; Fedora and Arch have rootless Docker too.
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
