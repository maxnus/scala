# The sandbox

A runner executes strangers' code: every bot it plays was written by someone else. This page says what a bot may do in
a match, and how to run a runner as a user of its own, so that a bot that breaks out of its container lands as a user
who owns nothing of yours.

## What a bot may do

| | |
|---|---|
| **Network** | It reaches the match controller, which relays it to the game, and nothing else: not the internet, not the game client itself, not the other bot. No name resolves but those of its own network. |
| **Files** | It sees its own folder alone, and may only read it. It writes to its `data` folder and `/tmp`, which are held in memory and gone after the match, and to its logs. |
| **Privileges** | It runs as the runner's user, with no capabilities and no way to gain any. Under rootless Docker it runs as its container's root, which is that user. |
| **CPU** | 2 cores. |
| **Memory** | 4 GiB, with no swap; its `data` folder and `/tmp` count against it. A bot that needs more is killed, which loses it the match. |
| **Processes** | 1024, threads included. |
| **Logs** | 100 MiB. A bot whose logs grow past that is stopped, and loses the match; its logs are cut to the limit. |

The game's two clients share 4 cores and 6 GiB, and the match controller, which relays every message of the bots',
takes 1 core and 1 GiB. A ladder sets all of these in its `ResourceLimits`.

A Windows bot does not play: AI Arena's v0.8.0 bot image holds no Wine, and `sc2scala play` refuses one.

The sandbox does not guard against a bot that exploits the kernel itself, which only a stronger layer, such as gVisor,
would. Nor does it make machines alike: a bot that budgets by the clock plays differently on a slower one. AI Arena's
match controller tells the two bots apart by the port each connects from, so a bot that guessed its opponent's could
try to pose as it. Scala's takes a seat's connection only from the address of that seat's bot, which a bot with no
capabilities cannot forge, and runs from a copy of its own code alone, mounted read-only.

## A runner of its own, with rootless Docker

The runner, and the Docker that plays its matches, run as a user made for them, here `sc2scala`. Docker runs rootless:
its daemon and its containers' root are that user, not root.

On Ubuntu 24.04 and later, the command the server's *Add a runner* page gives does all of the steps below, and checks
what they check; its script is `src/sc2scala/server/runner_setup.sh`. The steps are for doing it by hand, as on another
Linux, where the script stops.

These steps are for Ubuntu 24.04 and later, and need sudo once. Ubuntu restricts the user namespaces rootless Docker
makes (`kernel.apparmor_restrict_unprivileged_userns`); its `apparmor` package allows them to Ubuntu's own
`/usr/bin/rootlesskit`, so no AppArmor rule of your own is needed.

1. Install Docker, Compose and what rootless Docker needs:

   ```bash
   sudo apt install docker.io docker-compose-v2 rootlesskit slirp4netns uidmap dbus-user-session
   ```

2. Make the user, which gets its range of subordinate ids in `/etc/subuid` and `/etc/subgid`, and let its services
   run while nobody is logged in as it:

   ```bash
   sudo useradd --create-home --shell /bin/bash sc2scala
   sudo loginctl enable-linger sc2scala
   ```

3. As that user, set up rootless Docker as a service of the user's own:

   ```bash
   sudo -iu sc2scala
   export XDG_RUNTIME_DIR=/run/user/$(id -u)
   mkdir -p ~/.local/libexec/docker-rootless && cd ~/.local/libexec/docker-rootless
   ln -sf /usr/share/docker.io/contrib/dockerd-rootless.sh /usr/share/docker.io/contrib/dockerd-rootless-setuptool.sh .
   ln -sf "$(command -v docker)" .
   PATH=~/.local/libexec/docker-rootless:$PATH dockerd-rootless-setuptool.sh install
   docker context use rootless
   ```

   Docker's setup tool, `dockerd-rootless-setuptool.sh`, looks for `docker` in its own folder, and for
   `dockerd-rootless.sh`, which the service runs, on `PATH`. Ubuntu's docker.io keeps both scripts in
   `/usr/share/docker.io/contrib`, apart from `docker`, so a folder of links holds them together. Ubuntu 24.04's
   docker.io lacks the two scripts: download them, rather than link them, from Docker's repository at the version it
   installs, as `https://raw.githubusercontent.com/moby/moby/docker-v29.1.3/contrib/dockerd-rootless.sh` and the same
   for the setup tool. The setup script does all of this, and checks the downloads against their SHA-256.

4. Check, still as that user, that Docker runs rootless, and that the user may limit CPU, memory and processes, which
   the sandbox needs; Ubuntu's systemd gives every user all three:

   ```bash
   docker info --format '{{json .SecurityOptions}}'
   cat /sys/fs/cgroup/user.slice/user-$(id -u).slice/user@$(id -u).service/cgroup.controllers
   ```

   The first lists `name=rootless`, the second `cpu memory pids`.

5. Install `sc2scala` as that user. Its first match downloads AI Arena's images into that user's Docker, about 11 GB,
   28 GB once unpacked, whatever your own Docker holds already.

6. Still as that user, in the shell of step 3, register the machine, and run it as the user's service, which the
   linger of step 2 starts at boot. The setup code comes from the server's *Add a runner* page, in your own browser,
   signed in as the server's user the machine serves for; it needs no sign-in on the machine.

   ```bash
   sc2scala runner register --server https://scala.example.org --setup-code scala_setup_...
   sc2scala runner service
   systemctl --user daemon-reload
   systemctl --user enable --now sc2scala-runner
   ```

The engine asks Docker whether it runs rootless, and runs its containers as their root if it does, and as the user
who started it if it does not.
