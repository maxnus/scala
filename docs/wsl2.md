# A runner on Windows, in WSL2

> **Untested.** Nobody has followed this guide on a Windows machine yet. It is written from Microsoft's documentation
> of WSL and from the Linux setup it repeats, as of October 2026. If a step does not work as written, please say what
> you saw, in an issue if you can reach the repository, or to the owner of the server you run for, so that the guide
> can say what Windows does.

On Windows, Scala runs as it does on Linux, inside a Linux distribution that WSL2 runs in a virtual machine: the app,
Docker and the engine all run there, and the server's pages open in the Windows browser. What differs from Linux is
only how the distribution is made, how much of the machine it gets, and how it is kept running. Windows bots do not
play, here or on Linux (see [the plan](plan.md), decision 5).

A runner needs Windows 11 on an x86-64 machine with virtualization turned on in its firmware, and about 50 GB free on
the drive WSL keeps its disks on, `C:` by default: the engine's images take 28 GB once unpacked. Microsoft documents
the setting that turns systemd on for Windows 11 and Windows Server 2022 alone; Windows 10 22H2, with WSL from the
Microsoft Store, may work too.

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

[general]
instanceIdleTimeout=-1
```

The figures are an example: `sc2scala runner register` suggests one runner per 4 cores and 8 GiB of what WSL is given,
so these fit 3. `instanceIdleTimeout=-1` keeps WSL from stopping the distribution 15 seconds after its last window
closes, and the virtual machine stops only once no distribution runs. Run `wsl --shutdown` for the file to take effect;
it stops the runner too, and voids the matches it was playing.

## 4. Set up the runner

In Ubuntu, run the command that the server's *Add a runner* page gives, opened in the Windows browser, as the
[README](../README.md#contributing-a-runner) says. It installs Docker and what rootless Docker needs, makes the user
`sc2scala` with rootless Docker, registers the machine, under the computer's name, and starts the runner as that user's
service, which runs whenever the distribution does. Docker Desktop is not needed: if it is installed, first turn off
its WSL integration for Ubuntu-24.04, in its Settings > Resources > WSL integration, so that its `docker` does not take
the place of the distribution's own. The runner's log is `sudo journalctl _SYSTEMD_USER_UNIT=sc2scala-runner.service
--follow`.

## 5. Keep the distribution running

WSL stops a distribution once no Windows program uses it, even with systemd services running in it, and the idle
settings of step 3 do not always prevent it. A program that never ends keeps it running: a task that starts one when
you log on to Windows, which this makes, in PowerShell run as administrator from your own account:

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

## 6. Mind sleep and updates

While Windows sleeps, the runner pauses, and a match it was playing is void if it cannot report by its deadline, the
ladder's real-time limit and 15 minutes after the match was drawn; the next match is drawn afresh, so nothing is lost
but that match. Set the runner's windows in `runner.toml` to the hours the machine is awake, or keep it from sleeping
then, in Settings > System > Power, or Power & battery on a laptop. A restart for Windows Update stops the runner and
voids its matches, and the task of step 5 starts it again at the next logon.

## By hand

On a distribution the setup script does not take, follow the steps of
[the sandbox](sandbox.md#a-runner-of-its-own-with-rootless-docker) inside it, by which the runner runs as a user of its
own, with rootless Docker, whose lingering services the task of step 5 keeps running with the distribution. They have
not been tried in WSL2.
