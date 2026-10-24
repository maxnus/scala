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

The game's two clients share 4 cores and 6 GiB. A ladder sets all of these in its `ResourceLimits`.

A Windows bot does not play: AI Arena's v0.8.0 bot image holds no Wine, and `sc2scala play` refuses one.

The sandbox does not guard against a bot that exploits the kernel itself, which only a stronger layer, such as gVisor,
would. Nor does it make machines alike: a bot that budgets by the clock plays differently on a slower one.

## A runner of its own, with rootless Docker

The runner, and the Docker that plays its matches, run as a user made for them, here `sc2scala`. Docker runs rootless:
its daemon and its containers' root are that user, not root.

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
   PATH=/usr/share/docker.io/contrib:$PATH dockerd-rootless-setuptool.sh install
   docker context use rootless
   ```

   Ubuntu keeps the setup tool, and the `dockerd-rootless.sh` it starts, in `/usr/share/docker.io/contrib`.

4. Check, still as that user, that Docker runs rootless, and that the user may limit CPU, memory and processes, which
   the sandbox needs; Ubuntu's systemd gives every user all three:

   ```bash
   docker info --format '{{json .SecurityOptions}}'
   cat /sys/fs/cgroup/user.slice/user-$(id -u).slice/user@$(id -u).service/cgroup.controllers
   ```

   The first lists `name=rootless`, the second `cpu memory pids`.

5. Install `sc2scala` as that user. Its first match downloads AI Arena's images into that user's Docker, about 11 GB,
   28 GB once unpacked, whatever your own Docker holds already.

The engine asks Docker whether it runs rootless, and runs its containers as their root if it does, and as the user
who started it if it does not.
