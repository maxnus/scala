#!/usr/bin/env bash
# Scala's runner setup: makes this computer a runner machine of the Scala server below, as a user of its own, sc2scala,
# with rootless Docker, so that a bot that breaks out of its container lands as a user who owns nothing of yours.
# The server serves it at /runner/setup.sh with its own address filled in; its pages give the commands:
#
#   curl -fsSL <server>/runner/setup.sh | sudo bash -s -- SETUP-CODE [--name NAME] [--replace]
#   curl -fsSL <server>/runner/setup.sh | sudo bash                    # updates a machine it set up
#   curl -fsSL <server>/runner/setup.sh | sudo bash -s -- --remove     # undoes what it did
#
# It asks nothing, since its input may be its own download: what it may not decide alone, it refuses, and names the
# flag that allows it. SC2SCALA_PACKAGE, a wheel's path or address, is the Scala it installs; Scala is not on PyPI yet,
# and until it is, nothing else is installed, lest a stranger's package of that name run here:
#
#   curl -fsSL <server>/runner/setup.sh | sudo SC2SCALA_PACKAGE=/path/to/sc2scala.whl bash -s -- SETUP-CODE
set -euo pipefail

SCALA_SERVER="__SCALA_SERVER__"
RUNNER_USER=sc2scala
SERVICE=sc2scala-runner
MARKER_FOLDER=/var/lib/sc2scala-setup
MARKER="$MARKER_FOLDER/made-user"
PACKAGE="${SC2SCALA_PACKAGE:-}"
# What a machine of 16 GiB may lack in MemTotal, which leaves out what the kernel reserves.
MOST_MEMORY_MISSING_KIB=$((1024 * 1024))
MEMORY_GIB=16
FREE_GB=50
UBUNTU=24.04
DOCKER_CONTRIB=/usr/share/docker.io/contrib
# Ubuntu 24.04's docker.io lacks Docker's rootless scripts, which 26.04's ships: these, from Docker's own repository at
# the same version, are byte for byte the ones 26.04 ships, and are checked against their SHA-256 before they are kept.
ROOTLESS_SCRIPTS_URL=https://raw.githubusercontent.com/moby/moby/docker-v29.1.3/contrib
ROOTLESS_SCRIPT_HASHES=(
	"8eb00d61b5e78ffc0dcd628f056389b5859cc61d6fe3ce17b4a3a4b08143c8a5  dockerd-rootless.sh"
	"e68ef6550fea1b5c01b5cc7a400a6b44ee28159d7714758c3845759e03e079dc  dockerd-rootless-setuptool.sh"
)
ROOTLESS_SCRIPTS_FOLDER=/usr/local/libexec/sc2scala-setup

say() { printf '\n== %s\n' "$*"; }
note() { printf '   %s\n' "$*"; }
fail() {
	printf '\nStopped: %s\n' "$*" >&2
	exit 1
}

usage() {
	cat <<EOF
Makes this computer a runner machine of $SCALA_SERVER, as the user $RUNNER_USER, with rootless Docker.

  sudo bash setup.sh SETUP-CODE [--name NAME] [--replace]   set it up, by a code from $SCALA_SERVER/runners/add
  sudo bash setup.sh                                        update a machine this script set up
  sudo bash setup.sh --remove                               remove the machine, the user and its Docker's images

--name NAME   the machine's name (default: this computer's host name, made one)
--replace     register under a name one of your machines has already, when this computer is that machine set up again
EOF
}

setup_code=""
machine_name=""
replace=""
remove=""
while [ $# -gt 0 ]; do
	case "$1" in
	--name)
		[ $# -ge 2 ] || fail "--name needs a name"
		machine_name="$2"
		shift 2
		;;
	--replace)
		replace=yes
		shift
		;;
	--remove)
		remove=yes
		shift
		;;
	-h | --help)
		usage
		exit 0
		;;
	-*) fail "No option $1: see --help" ;;
	*)
		[ -z "$setup_code" ] || fail "One setup code at most, not also $1"
		setup_code="$1"
		shift
		;;
	esac
done

runner_home() { getent passwd "$RUNNER_USER" | cut -d: -f6; }
runner_uid() { id -u "$RUNNER_USER"; }

as_runner() {
	# Runs a command as the runner's user, in its home, with what its systemd and Docker need to be found.
	local home uid
	home="$(runner_home)"
	uid="$(runner_uid)"
	# The inner bash expands what the single quotes keep from this one.
	# shellcheck disable=SC2016
	runuser -u "$RUNNER_USER" -- env -i \
		HOME="$home" USER="$RUNNER_USER" LOGNAME="$RUNNER_USER" SHELL=/bin/bash LANG=C.UTF-8 \
		PATH="$home/.local/bin:$ROOTLESS_SCRIPTS_FOLDER:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
		XDG_RUNTIME_DIR="/run/user/$uid" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$uid/bus" \
		bash -c 'cd "$HOME" && exec "$@"' bash "$@"
}

is_installed() { dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q 'install ok installed'; }

version_at_least() { printf '%s\n%s\n' "$2" "$1" | sort -V -C; }

registered() { [ -f "$(runner_home)/.config/sc2scala/runner.json" ]; }

check_machine() {
	say "Checking this computer"
	[ "$(id -u)" -eq 0 ] || fail "Run it as root: curl -fsSL $SCALA_SERVER/runner/setup.sh | sudo bash -s -- ..."
	[ "$(uname -m)" = x86_64 ] || fail "A runner needs x86-64, which the game's Docker image is built for, not $(uname -m)"
	# shellcheck disable=SC1091
	. /etc/os-release
	if [ "${ID:-}" != ubuntu ] || ! version_at_least "${VERSION_ID:-0}" "$UBUNTU"; then
		fail "This script knows Ubuntu $UBUNTU and later alone, not ${PRETTY_NAME:-this system}: Scala's guide to the sandbox says how to set a runner up by hand"
	fi
	[ -d /run/systemd/system ] || fail "systemd does not run here; in WSL2, turn it on as Scala's WSL2 guide says"
	local memory_kib memory_gib
	memory_kib="$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)"
	memory_gib="$(awk -v kib="$memory_kib" 'BEGIN {printf "%.1f", kib / 1048576}')"
	if [ "$memory_kib" -lt $((MEMORY_GIB * 1024 * 1024 - MOST_MEMORY_MISSING_KIB)) ]; then
		fail "A runner needs a machine of $MEMORY_GIB GiB, for the 15 GiB one match may take under a ladder's default limits; this one's kernel has $memory_gib GiB"
	fi
	if ! registered 2>/dev/null; then
		local free_gb
		free_gb="$(df -BG --output=avail /home | tail -1 | tr -dc '0-9')"
		[ "$free_gb" -ge "$FREE_GB" ] || fail "A runner needs $FREE_GB GB free in /home, 28 for the game's images and 20 for its cache; /home has $free_gb GB"
	fi
	note "$PRETTY_NAME, $(uname -m), $memory_gib GiB of memory as the kernel counts it"
}

install_packages() {
	say "Installing what rootless Docker needs"
	local packages missing=()
	if is_installed docker-ce; then
		packages=(docker-ce-rootless-extras docker-compose-plugin uidmap dbus-user-session slirp4netns curl ca-certificates)
	else
		packages=(docker.io docker-compose-v2 rootlesskit uidmap dbus-user-session slirp4netns curl ca-certificates)
	fi
	local package
	for package in "${packages[@]}"; do
		is_installed "$package" || missing+=("$package")
	done
	if [ ${#missing[@]} -eq 0 ]; then
		note "All there already"
		return
	fi
	local docker_was_installed=""
	is_installed docker.io && docker_was_installed=yes
	note "Installing ${missing[*]}"
	# Waits for the lock that the daily upgrades, or a fresh machine's first, may hold.
	apt-get -o DPkg::Lock::Timeout=600 update -qq
	DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=600 install -y -qq "${missing[@]}" >/dev/null
	if [ -z "$docker_was_installed" ] && is_installed docker.io; then
		# Ubuntu's package starts Docker's daemon as root, which a rootless runner does not need.
		systemctl disable --now docker.service docker.socket >/dev/null 2>&1 || true
		note "Docker's daemon as root, which Ubuntu's package started, is off again: the runner has its own"
	fi
}

make_user() {
	say "Making the user $RUNNER_USER"
	if getent passwd "$RUNNER_USER" >/dev/null; then
		[ -f "$MARKER" ] || fail "A user $RUNNER_USER exists that this script did not make, as by the sandbox guide's steps by hand: it leaves that user alone. Use that setup, or remove the user first"
		note "Made by this script before"
	else
		useradd --create-home --shell /bin/bash "$RUNNER_USER"
		mkdir -p "$MARKER_FOLDER"
		echo "$RUNNER_USER" >"$MARKER"
		note "Made $RUNNER_USER, home $(runner_home)"
	fi
	grep -q "^$RUNNER_USER:" /etc/subuid || fail "$RUNNER_USER has no subordinate ids in /etc/subuid, which rootless Docker needs; give it a range of its own, as usermod --add-subuids does"
	loginctl enable-linger "$RUNNER_USER"
	systemctl start "user@$(runner_uid).service"
	local waited=0
	until [ -S "/run/user/$(runner_uid)/bus" ]; do
		[ "$waited" -lt 30 ] || fail "The systemd of $RUNNER_USER did not start within 30 seconds"
		sleep 1
		waited=$((waited + 1))
	done
	note "Its services run while nobody is logged in, and from boot"
}

add_rootless_scripts() {
	# Docker's setup tool looks for docker beside itself. Docker's own packages keep both in /usr/bin; Ubuntu's docker.io
	# keeps its rootless scripts apart, or, in 24.04, lacks them, so a folder of this script's own holds them beside a
	# link to docker: linked to the package's whenever it has them, or else downloaded and checked.
	[ ! -x /usr/bin/dockerd-rootless-setuptool.sh ] || return 0
	install -d -m 755 "$ROOTLESS_SCRIPTS_FOLDER"
	if [ -x "$DOCKER_CONTRIB/dockerd-rootless-setuptool.sh" ]; then
		if [ "$(readlink "$ROOTLESS_SCRIPTS_FOLDER/dockerd-rootless-setuptool.sh")" != "$DOCKER_CONTRIB/dockerd-rootless-setuptool.sh" ]; then
			ln -sf "$DOCKER_CONTRIB/dockerd-rootless.sh" "$DOCKER_CONTRIB/dockerd-rootless-setuptool.sh" \
				"$ROOTLESS_SCRIPTS_FOLDER/"
			note "Linked docker.io's rootless scripts into $ROOTLESS_SCRIPTS_FOLDER"
		fi
	elif [ ! -x "$ROOTLESS_SCRIPTS_FOLDER/dockerd-rootless-setuptool.sh" ]; then
		local download line
		download="$(mktemp -d)"
		for line in "${ROOTLESS_SCRIPT_HASHES[@]}"; do
			curl -fsSL -o "$download/${line##* }" "$ROOTLESS_SCRIPTS_URL/${line##* }"
		done
		(cd "$download" && printf '%s\n' "${ROOTLESS_SCRIPT_HASHES[@]}" | sha256sum --check --quiet) ||
			fail "Docker's rootless scripts from $ROOTLESS_SCRIPTS_URL do not match their SHA-256"
		install -m 755 "$download"/*.sh "$ROOTLESS_SCRIPTS_FOLDER/"
		rm -rf "$download"
		note "Added Docker's rootless scripts, which this docker.io lacks, to $ROOTLESS_SCRIPTS_FOLDER"
	fi
	ln -sf "$(command -v docker)" "$ROOTLESS_SCRIPTS_FOLDER/docker"
}

check_apparmor() {
	# Where the kernel restricts unprivileged user namespaces, as Ubuntu's does, rootlesskit needs an AppArmor profile
	# that allows them, which Ubuntu's apparmor package ships, 24.04's among them; a profile in /etc/apparmor.d, but not
	# in its disable folder, is loaded as the system starts.
	local restricted=/proc/sys/kernel/apparmor_restrict_unprivileged_userns
	[ -r "$restricted" ] && [ "$(cat "$restricted")" = 1 ] || return 0
	grep -rlsqE --exclude-dir=disable '^[^#]*[[:space:]]/usr/bin/rootlesskit[[:space:]]' /etc/apparmor.d ||
		fail "The kernel restricts user namespaces, and no AppArmor profile lets rootlesskit make them, as Ubuntu's apparmor package's does: apt-get install --only-upgrade apparmor"
}

set_up_docker() {
	say "Setting up rootless Docker for $RUNNER_USER"
	add_rootless_scripts
	check_apparmor
	local setup_log
	setup_log="$(mktemp)"
	if ! as_runner dockerd-rootless-setuptool.sh install >"$setup_log" 2>&1; then
		sed 's/^/   /' "$setup_log" >&2
		fail "Docker's setup tool could not set up rootless Docker for $RUNNER_USER, as it says above"
	fi
	rm -f "$setup_log"
	note "Docker runs as $RUNNER_USER, as a service of its own"
	as_runner docker context use rootless >/dev/null 2>&1
	local security uid controllers compose
	security="$(as_runner docker info --format '{{json .SecurityOptions}}')"
	[[ "$security" == *name=rootless* ]] || fail "Docker does not report rootless for $RUNNER_USER: $security"
	uid="$(runner_uid)"
	controllers="$(cat "/sys/fs/cgroup/user.slice/user-$uid.slice/user@$uid.service/cgroup.controllers")"
	local controller
	for controller in cpu memory pids; do
		[[ " $controllers " == *" $controller "* ]] || fail "$RUNNER_USER may not limit $controller, which the sandbox needs: its systemd delegates $controllers"
	done
	compose="$(as_runner docker compose version --short)"
	version_at_least "${compose#v}" 2.20 || fail "The engine needs Docker Compose 2.20 or later, not $compose"
	note "Rootless, limits CPU, memory and processes, Compose $compose"
}

install_scala() {
	say "Installing Scala for $RUNNER_USER"
	if [ -z "$PACKAGE" ]; then
		as_runner test -x .local/bin/sc2scala ||
			fail "Scala is not on PyPI yet: give its wheel, as sudo SC2SCALA_PACKAGE=/path/to/sc2scala.whl bash ..."
		note "Kept $(as_runner .local/bin/uv tool list | grep '^sc2scala '): Scala is not on PyPI yet, so SC2SCALA_PACKAGE updates it"
		return
	fi
	if ! as_runner test -x .local/bin/uv; then
		as_runner sh -c 'curl -LsSf https://astral.sh/uv/install.sh | UV_NO_MODIFY_PATH=1 sh' >/dev/null
		note "Installed uv"
	fi
	local spec="$PACKAGE"
	if [ -f "$PACKAGE" ]; then
		spec="./$(basename "$PACKAGE")"
		install -o "$RUNNER_USER" -g "$RUNNER_USER" -m 644 "$PACKAGE" "$(runner_home)/$(basename "$PACKAGE")"
	fi
	as_runner uv tool install --quiet --reinstall-package sc2scala "$spec"
	note "Installed $(as_runner uv tool list | grep '^sc2scala ')"
}

register() {
	say "Registering this computer with $SCALA_SERVER"
	if registered; then
		note "It is a runner machine already: the setup code, --name and --replace are not needed"
		return
	fi
	[ -n "$setup_code" ] || fail "This computer is no runner machine yet: give the setup code from $SCALA_SERVER/runners/add"
	local arguments=(runner register --server "$SCALA_SERVER" --setup-code "$setup_code")
	[ -z "$machine_name" ] || arguments+=("$machine_name")
	[ -z "$replace" ] || arguments+=(--replace)
	as_runner sc2scala "${arguments[@]}" | sed 's/^/   /'
}

start_runner() {
	say "Downloading the game's images, about 11 GB at first"
	as_runner sc2scala runner pull
	say "Starting the runner"
	as_runner sc2scala runner service >/dev/null
	as_runner systemctl --user daemon-reload
	as_runner systemctl --user enable --quiet "$SERVICE"
	if as_runner systemctl --user is-active --quiet "$SERVICE"; then
		as_runner systemctl --user restart --no-block "$SERVICE"
		note "It restarts on the new Scala once the matches it plays end"
	else
		as_runner systemctl --user start "$SERVICE"
		note "It runs as a service of $RUNNER_USER's, from boot"
	fi
}

summary() {
	local machine
	machine="$(as_runner python3 -c 'import json; print(json.load(open(".config/sc2scala/runner.json"))["machine"])')"
	say "Done: this computer is the runner machine $machine of $SCALA_SERVER"
	note "How many matches it plays at once, 1 at first, pausing it, its ladders, and removing it: $SCALA_SERVER/account"
	note "The most matches it may play at once, and when it plays: $(runner_home)/.config/sc2scala/runner.toml, then"
	note "  sudo -iu $RUNNER_USER XDG_RUNTIME_DIR=/run/user/$(runner_uid) systemctl --user restart $SERVICE"
	note "Its log: sudo journalctl _SYSTEMD_USER_UNIT=$SERVICE.service --follow"
}

remove_runner() {
	say "Removing the runner machine and the user $RUNNER_USER"
	[ "$(id -u)" -eq 0 ] || fail "Run it as root: curl -fsSL $SCALA_SERVER/runner/setup.sh | sudo bash -s -- --remove"
	getent passwd "$RUNNER_USER" >/dev/null || fail "There is no user $RUNNER_USER: nothing to remove"
	[ -f "$MARKER" ] || fail "This script did not make the user $RUNNER_USER, so it leaves it alone"
	local uid
	uid="$(runner_uid)"
	if [ -S "/run/user/$uid/bus" ]; then
		as_runner sc2scala runner stop --now >/dev/null 2>&1 || true
		as_runner systemctl --user disable --now "$SERVICE" >/dev/null 2>&1 || true
	fi
	rm -f "$(runner_home)/.config/systemd/user/$SERVICE.service"
	if registered; then
		as_runner sc2scala runner remove | sed 's/^/   /' || note "The server could not be told; remove the machine on $SCALA_SERVER/account"
	fi
	loginctl disable-linger "$RUNNER_USER"
	systemctl stop "user@$uid.service" >/dev/null 2>&1 || true
	pkill -KILL -u "$RUNNER_USER" >/dev/null 2>&1 || true
	userdel --remove "$RUNNER_USER" 2>/dev/null || userdel --remove --force "$RUNNER_USER"
	rm -rf "$ROOTLESS_SCRIPTS_FOLDER" "$MARKER_FOLDER"
	say "Removed: the user $RUNNER_USER, its home, its Docker and the game's images. The packages it installed stay"
}

if [ -n "$remove" ]; then
	remove_runner
	exit 0
fi
check_machine
install_packages
make_user
set_up_docker
install_scala
register
start_runner
summary
