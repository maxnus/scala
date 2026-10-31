# A public server

How to run a Scala server for others: on a Linux machine, with a domain pointed at it, behind
[Caddy](https://caddyserver.com), which gets its HTTPS certificate and limits the size of an upload. The steps are for
Ubuntu 24.04. The first public server, step 7b of [the plan](plan.md), followed them on 2026-10-31, on a machine that
serves another site behind the same Caddy, and corrected them.

## What it needs

- A Linux machine that the internet reaches on ports 80 and 443, and a domain name whose DNS points at it. Without a
  domain of your own, [sslip.io](https://sslip.io) names every address: `203-0-113-7.sslip.io` resolves to
  `203.0.113.7`, and Caddy gets its certificate as for any other name. The server serves the pages and runs nothing of
  the bots, since the runners play the matches, so it may share the machine with other sites behind one Caddy.
- About 1 GB of memory for the server, a single process, which took 120 MB at rest on the first; and disk for what it
  keeps: every version of every bot, up to 500 MiB each; every replay, a few hundred KiB each; and the logs of the last
  30 days of matches, up to about 200 MiB each, though most are far smaller.
- A GitHub account, which registers the app people sign in with.

## 1. A user of its own

The server runs as a user made for it, `scala`, whose home, `/srv/scala`, holds the install and the data folder:

```bash
sudo useradd --create-home --home-dir /srv/scala --shell /bin/bash scala
sudo -iu scala
```

The next two steps run as that user.

## 2. Install

Install [uv](https://docs.astral.sh/uv/), then Scala with its server extra:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.local/bin/env
uv tool install "sc2scala[server] @ git+https://github.com/maxnus/scala"
```

While the repository is private, the server cannot fetch it. Build the package on a machine that can, by `uv build
--wheel` in a clone, copy the wheel it writes in `dist/` to `/srv/scala`, owned by `scala`, and install that:

```bash
uv tool install './sc2scala-0.1.0.dev0-py3-none-any.whl[server]'
```

## 3. Its settings

Make the data folder, and a folder for backups:

```bash
mkdir -p /srv/scala/data /srv/scala/backups
```

and write the server's settings in the data folder, `/srv/scala/data/server.toml`:

```toml
host = "127.0.0.1"
port = 8000
public_url = "https://scala.example.org"
dev_sign_in = false

[github]
client_id = ""
client_secret = ""
```

```bash
chmod 600 /srv/scala/data/server.toml
exit
```

The server listens on this machine alone, and Caddy serves it to everyone. Any free port will do, and `ss -ltn` lists
those taken: the first server took 8100, since the other site's had 8000. Left out, the sections `[uploads]`,
`[runners]` and `[storage]` take the defaults that `sc2scala server` writes into a new data folder's `server.toml`: a
bot's zip of 500 MiB at most, and a match's logs kept 30 days.

Left empty, `[github]` turns GitHub's sign-in off: the server runs, and its sign-in page says that nobody can sign in.
So the server can be started, and its address checked, before step 7 registers the app.

## 4. A service

Write `/etc/systemd/system/scala-server.service`:

```ini
[Unit]
Description=Scala server
Wants=network-online.target
After=network-online.target

[Service]
User=scala
ExecStart=/srv/scala/.local/bin/sc2scala server --data /srv/scala/data
Restart=on-failure
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/srv/scala/data

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now scala-server
journalctl -u scala-server -f
```

The log's first line names the sign-in: `none` until step 7, then `GitHub`. The server writes only to its data folder,
and receives each upload whole in its own `/tmp` before it checks it. Each hour it drops the logs of the matches that
ended more than `keep_logs_days` ago, and removes the files no bot, map or match holds any longer.

## 5. Caddy

```bash
sudo apt install caddy
```

Write `/etc/caddy/Caddyfile`, or, if Caddy serves other sites already, add this block to it beside theirs:

```caddy
scala.example.org {
	encode zstd gzip
	request_body {
		max_size 600MB
	}
	reverse_proxy 127.0.0.1:8000
}
```

Check the file before Caddy reloads it, the more so when other sites depend on it:

```bash
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
sudo systemctl reload caddy
```

Caddy gets the domain's certificate once the domain points at the machine and ports 80 and 443 are open. `encode`
compresses the pages; with no `match` of its own, it leaves zips and replays as they are. The server receives an upload
whole before it checks who sent it, so Caddy refuses a body past `max_size`. 600 MB holds a bot's zip of 500 MiB and
the form around it, and a match's result, its replay and its logs, of which each bot's are cut at 100 MiB. Raise it if
you raise the server's `max_bot_mib`, or if a ladder lets each bot log more than 200 MiB.

Caddy runs on the same machine, since the server trusts the forwarded headers of a proxy at 127.0.0.1 alone: from
them it learns each visitor's address, which limits how many sign-ins may wait from one, and that the pages are served
over HTTPS.

## 6. A firewall

```bash
sudo ufw allow OpenSSH
sudo ufw allow http
sudo ufw allow https
sudo ufw enable
```

A machine that serves other sites has these open already, as `sudo ufw status` says.

## 7. GitHub's sign-in

Register an OAuth app on GitHub, at <https://github.com/settings/applications/new>:

| Field | Value |
|---|---|
| Homepage URL | `https://scala.example.org`, your domain |
| Authorization callback URL, which GitHub's newer form calls Redirect URI | `https://scala.example.org/sign-in/github/callback` |

Leave Enable Device Flow off: `sc2scala login` signs in through the server's pages, not by GitHub's device flow.
Whether user access tokens expire makes no difference, since the server uses GitHub's token once, to read who signed
in, and does not keep it. A form that asks for a webhook and permissions too registers a GitHub App, another kind.

Generate a client secret on the app's page. Put the client id and the secret in `[github]` in `server.toml`, by an
editor run as root, which keeps the file's owner and mode, and restart the server:

```bash
sudo nano /srv/scala/data/server.toml
sudo systemctl restart scala-server
```

## 8. Sign in

Open `https://scala.example.org`, sign in with GitHub, and make the first ladder.

## Updating

Back up first, then:

```bash
sudo -iu scala uv tool upgrade sc2scala
sudo systemctl restart scala-server
```

Installed from a wheel, Scala does not upgrade so: `uv tool upgrade` finds nothing to upgrade, and installing again
says it is installed already. Install the new wheel with `--reinstall` instead:

```bash
sudo -iu scala uv tool install --reinstall './sc2scala-0.1.0.dev0-py3-none-any.whl[server]'
```

The server brings its database up to date as it starts.

## Backing up

The data folder holds the database, `scala.db`, the files, `files/`, `server.toml`, and `secret_key`, which signs the
pages' sign-ins: lost, it signs everyone out, and nothing else. `sc2scala server backup` copies the database whole, as
it stands at one moment, while the server writes to it; a plain copy of the folder may catch it halfway through a
write. The files never change once written, so a plain copy keeps them.

As the `scala` user, `crontab -e`, a copy of the database each night, kept two weeks:

```text
15 4 * * * /srv/scala/.local/bin/sc2scala server backup --data /srv/scala/data /srv/scala/backups/scala-$(date +\%F).db
45 4 * * * find /srv/scala/backups -name 'scala-*.db' -mtime +14 -delete
```

Then copy `/srv/scala/backups` and `/srv/scala/data/files` off the machine, as with `rsync` from another one.

To restore, stop the server, put the copy in place of `scala.db`, remove `scala.db-wal` and `scala.db-shm` beside it,
and start the server.
