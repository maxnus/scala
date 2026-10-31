# A public server

How to run a Scala server for others: on a Linux machine of its own, with a domain pointed at it, behind
[Caddy](https://caddyserver.com), which gets its HTTPS certificate and limits the size of an upload. The steps are for
Ubuntu 24.04, and nobody has followed them on a real server yet: the first public server, step 7b of
[the plan](plan.md), tries them, and its report corrects them.

## What it needs

- A Linux machine that the internet reaches on ports 80 and 443, and a domain name whose DNS points at it. Its own
  server is best, since it serves the pages, and runs nothing of the bots: the runners play the matches.
- About 1 GB of memory for the server, a single process, and disk for what it keeps: every version of every bot, up to
  500 MiB each; every replay, a few hundred KiB each; and the logs of the last 30 days of matches, up to about 200 MiB
  each, though most are far smaller.
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

While the repository is private, install from a clone made with your access instead: `uv tool install
'./scala[server]'`.

## 3. Its settings, and GitHub's sign-in

Register an OAuth app on GitHub, at <https://github.com/settings/applications/new>:

| Field | Value |
|---|---|
| Homepage URL | `https://scala.example.org`, your domain |
| Authorization callback URL | `https://scala.example.org/sign-in/github/callback` |

Generate a client secret on the app's page. Then make the data folder, and a folder for backups:

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
client_id = "the app's client id"
client_secret = "its client secret"
```

```bash
chmod 600 /srv/scala/data/server.toml
exit
```

The server listens on this machine alone, and Caddy serves it to everyone. Left out, the sections `[uploads]`,
`[runners]` and `[storage]` take the defaults that `sc2scala server` writes into a new data folder's `server.toml`: a
bot's zip of 500 MiB at most, and a match's logs kept 30 days.

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

The server writes only to its data folder, and receives each upload whole in its own `/tmp` before it checks it. Each
hour it drops the logs of the matches that ended more than `keep_logs_days` ago, and removes the files no bot, map or
match holds any longer.

## 5. Caddy

```bash
sudo apt install caddy
```

Write `/etc/caddy/Caddyfile`:

```caddy
scala.example.org {
	request_body {
		max_size 600MB
	}
	reverse_proxy 127.0.0.1:8000
}
```

```bash
sudo systemctl reload caddy
```

Caddy gets the domain's certificate once the domain points at the machine and ports 80 and 443 are open. The server
receives an upload whole before it checks who sent it, so Caddy refuses a body past `max_size`: 600 MB holds a bot's
zip of 500 MiB and the form around it. Raise both together, with the server's `max_bot_mib`.

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

## 7. Sign in

Open `https://scala.example.org`, sign in with GitHub, and make the first ladder.

## Updating

Back up first, then:

```bash
sudo -iu scala uv tool upgrade sc2scala
sudo systemctl restart scala-server
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
