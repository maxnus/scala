# A runner on Windows, in WSL2

> **Untested.** Nobody has followed this guide on a Windows machine yet. It is written from Microsoft's documentation
> of WSL and from the Linux setup it repeats, as of October 2026. If a step does not work as written, please open an
> issue with what you saw, so that the guide can say what Windows does.

On Windows, Scala runs as it does on Linux, inside a Linux distribution that WSL2 runs in a virtual machine: the app,
Docker and the engine all run there, and the server's pages open in the Windows browser. What differs from Linux is
only how the distribution is made, how much of the machine it gets, and how it is kept running. Windows bots do not
play, here or on Linux (see [the plan](plan.md), decision 5).

A runner needs Windows 11, or Windows 10 22H2, on an x86-64 machine with virtualization turned on in its firmware, and
about 50 GB free on the drive WSL keeps its disks on, `C:` by default: the engine's images take 28 GB once unpacked.

## 1. Install WSL2 and Ubuntu

In PowerShell, run as administrator:

```powershell
wsl --install -d Ubuntu-24.04
```

Restart Windows if it asks, then open Ubuntu from the Start menu and make its user. Check, in PowerShell, that WSL is
the current one and runs Ubuntu as version 2:

```powershell
wsl --version
wsl --list --verbose
```

`wsl --version` prints WSL's version only if it is the one from the Microsoft Store; `wsl --update` brings an older
one up to date.

## 2. Turn on systemd

The runner runs as a systemd service, which WSL starts only if the distribution asks for it in `/etc/wsl.conf`, as
Ubuntu's may already. In Ubuntu, `cat /etc/wsl.conf` shows whether it does; if no `systemd=true` follows `[boot]`, add
these lines, with `sudo nano /etc/wsl.conf`, keeping what the file holds already:

```ini
[boot]
systemd=true
```

Then restart the distribution from PowerShell with `wsl --shutdown`, open Ubuntu again, and check that systemd runs:

```bash
systemctl is-system-running
```

It answers `running`, or `degraded` if some unit of the distribution's own failed, which does not matter here.

## 3. Give WSL the machine's share it may use

WSL's virtual machine takes, by default, half of Windows' memory and all of its processors, and shuts itself down a
while after nothing uses it, runner or not. Write `%UserProfile%\.wslconfig`, in Notepad say, with what the runners
may take and no idle shutdown:

```ini
[wsl2]
memory=24GB
processors=12
vmIdleTimeout=-1

[general]
instanceIdleTimeout=-1
```

The figures are an example: `sc2scala runner register` suggests one runner per 4 cores and 8 GiB of what WSL is given,
so these fit 3. `vmIdleTimeout` is read on Windows 11 alone. Run `wsl --shutdown` for the file to take effect; it stops
the runner too, and voids the matches it was playing.

## 4. Install Docker inside Ubuntu

Install Docker Engine and Compose in the distribution, as on Ubuntu itself, rather than Docker Desktop:

```bash
sudo apt update
sudo apt install docker.io docker-compose-v2
sudo usermod -aG docker $USER
```

Close every Ubuntu window and run `wsl --terminate Ubuntu-24.04` for the group to take effect, then check, in Ubuntu,
that Docker runs, that Compose is 2.20 or later, and that Docker runs on cgroup v2 with the CPU, memory and process
limits the sandbox needs:

```bash
docker run --rm hello-world
docker compose version
docker info --format '{{.CgroupVersion}}'
cat /sys/fs/cgroup/cgroup.controllers
```

The third prints `2`, and the fourth lists `cpu`, `memory` and `pids`. WSL mounts cgroup v2 unless `/etc/wsl.conf`
asks for v1.

Docker Desktop also runs in WSL2, but its containers run in a distribution of its own and its `docker` replaces the
one above; Scala has not been tried with it.

## 5. Install Scala and register the runner

As on Linux, in Ubuntu, and in Ubuntu's own file system, under `~`, rather than under `/mnt/c`, which is far slower.
The repository is private for now, so cloning it needs access to it:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
git clone https://github.com/maxnus/scala.git ~/scala
cd ~/scala
uv sync
uv run sc2scala login https://scala.example.org
uv run sc2scala runner register my-pc
```

`runner register` suggests how many runners fit what WSL was given in step 3, and writes the runner's settings,
`~/.config/sc2scala/runner.toml`, as the [README](../README.md#running-matches) says.

## 6. Run the runner as a service

```bash
uv run sc2scala runner service
systemctl --user daemon-reload
systemctl --user enable --now sc2scala-runner
sudo loginctl enable-linger $USER
```

The last line starts the user's services whenever the distribution starts, with no Ubuntu window open. Its log is
`journalctl --user --unit sc2scala-runner --follow`.

## 7. Keep the distribution running

WSL stops a distribution once no Windows program uses it, even with systemd services running in it, and the idle
settings of step 3 do not always prevent it. A program that never ends keeps it running: a task that starts one when
you log on to Windows, which this makes, in PowerShell:

```powershell
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument '-WindowStyle Hidden -Command "wsl.exe -d Ubuntu-24.04 --exec sleep infinity"'
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan) -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "Keep WSL running for sc2scala" -Action $action -Trigger $trigger -Settings $settings
```

Once you log on, `wsl --list --running` lists Ubuntu-24.04, and the runner starts with it. The task runs only while you
are logged on; one that starts at boot, with nobody logged on, is not covered here.

## 8. Mind sleep and updates

While Windows sleeps, the runner stops, and a match it was playing runs out of time and is void; the next is drawn
afresh, so nothing is lost but the match. Set the runner's windows in `runner.toml` to the hours the machine is awake,
or keep it from sleeping then, in Settings > System > Power. A restart for Windows Update stops the runner the same way,
and the task of step 7 starts it again at the next logon.

## A runner of its own

A runner that plays strangers' bots should run as a user of its own, with rootless Docker, as
[the sandbox](sandbox.md#a-runner-of-its-own-with-rootless-docker) says for Linux. In WSL2, the same steps should work
inside the distribution, with step 6 run as that user, whose lingering services the task of step 7 keeps running with
the distribution; they have not been tried there.
