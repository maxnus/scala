"""Tries the runner setup script in a fresh Ubuntu VM: boots Ubuntu's cloud image under KVM, and runs the script there
as a contributor would, against a server on this machine.

    uv run python tools/try_runner_setup.py --release 24.04 --wheel dist/sc2scala-0.1.0.dev0-py3-none-any.whl \\
        --server-url http://127.0.0.1:8123 --setup-code scala_setup_...

The server is one this machine runs, as `sc2scala server` with the dev sign-in, whose public_url is on 127.0.0.1: an
SSH tunnel makes its port the VM's own, so the address the script holds works in the VM too. The VM keeps running, with
the tunnel, until Ctrl-C, and the tool prints how to reach it by SSH, as to run the script again or with --remove.

It downloads the release's cloud image once, checked against Ubuntu's SHA256SUMS, into the VMs' folder, where each VM
gets a disk of its own on top of it. It needs qemu-system-x86_64, qemu-img, ssh and /dev/kvm. A runner needs 16 GiB, so
the VM gets that, 6 processors and an 80 GB disk, which grows as it fills: the game's images take 28 GB.
"""

import argparse
import hashlib
import http.server
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from collections.abc import Sequence
from functools import partial
from pathlib import Path
from types import FrameType
from urllib.parse import urlsplit

_CLOUD_IMAGES = "https://cloud-images.ubuntu.com/releases/{release}/release"
_IMAGE_NAME = "ubuntu-{release}-server-cloudimg-amd64.img"
_DEFAULT_VMS_FOLDER = Path.home() / ".cache" / "sc2scala-vm-tests"
_SSH_PORT = 2222
_VM_USER = "ubuntu"
_BOOT_SECONDS = 300
_DOWNLOAD_CHUNK_BYTES = 1024 * 1024


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--release", required=True, help="Ubuntu's release, as 24.04 or 26.04")
    parser.add_argument("--wheel", type=Path, required=True, help="the Scala wheel the script installs")
    parser.add_argument("--server-url", required=True, help="the server's public_url, on 127.0.0.1")
    parser.add_argument("--setup-code", help="a setup code from the server's Add a runner page; left out, none runs")
    parser.add_argument("--vms-folder", type=Path, default=_DEFAULT_VMS_FOLDER, help="where images and disks are kept")
    parser.add_argument("--memory-gib", type=int, default=16)
    parser.add_argument("--processors", type=int, default=6)
    parser.add_argument("--disk-gb", type=int, default=80)
    args = parser.parse_args()
    sys.stdout.reconfigure(line_buffering=True)  # type: ignore[union-attr]
    server = urlsplit(args.server_url)
    if server.hostname != "127.0.0.1" or server.port is None:
        parser.error("--server-url is a server on 127.0.0.1, with its port, as http://127.0.0.1:8123")
    for program in ("qemu-system-x86_64", "qemu-img", "ssh", "ssh-keygen", "scp"):
        if shutil.which(program) is None:
            parser.error(f"{program} is missing")
    signal.signal(signal.SIGTERM, _stop_as_on_ctrl_c)
    vm_folder = args.vms_folder / f"ubuntu-{args.release}-{time.strftime('%Y%m%d-%H%M%S')}"
    vm_folder.mkdir(parents=True)
    base_image = cloud_image(args.release, args.vms_folder)
    disk = vm_folder / "disk.qcow2"
    overlay = ["-f", "qcow2", "-b", str(base_image), "-F", "qcow2"]
    run(["qemu-img", "create", "-q", *overlay, str(disk), f"{args.disk_gb}G"])
    key = vm_folder / "ssh_key"
    run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)])
    seed_url, seed_server = serve_cloud_init(vm_folder, args.release, key.with_suffix(".pub").read_text().strip())
    with subprocess.Popen(qemu_command(args, disk, seed_url, vm_folder / "console.log")) as vm:
        try:
            print(f"Booting Ubuntu {args.release} in {vm_folder}; its console goes to console.log")
            ssh = SshToVm(key)
            ssh.wait_for_boot()
            seed_server.shutdown()
            print(f"Ubuntu {args.release} is up. Reach it by:\n  {' '.join(ssh.command())}")
            tunnel_options = ["-N", "-R", f"{server.port}:127.0.0.1:{server.port}"]
            with subprocess.Popen(ssh.command(ssh_options=tunnel_options)) as tunnel:
                ssh.copy_in(args.wheel)
                if args.setup_code:
                    setup = setup_command(args.server_url, args.wheel.name, args.setup_code)
                    print(f"Running, in the VM: {setup}\n")
                    finished = subprocess.run(ssh.command(setup), check=False)
                    print(f"\nThe setup script ended with {finished.returncode}")
                print("The VM and its tunnel keep running until Ctrl-C")
                vm.wait()
                tunnel.terminate()
        except KeyboardInterrupt:
            print("\nStopping the VM")
        finally:
            vm.terminate()


def cloud_image(release: str, vms_folder: Path) -> Path:
    """Ubuntu's cloud image of `release`, downloaded once into `vms_folder` and checked against its SHA256SUMS."""
    name = _IMAGE_NAME.format(release=release)
    image = vms_folder / name
    if image.exists():
        return image
    folder_url = _CLOUD_IMAGES.format(release=release)
    with urllib.request.urlopen(f"{folder_url}/SHA256SUMS") as sums:
        expected = next(line.split()[0] for line in sums.read().decode().splitlines() if line.endswith(f"*{name}"))
    partial_image = image.with_name(name + ".part")
    digest = hashlib.sha256()
    print(f"Downloading {folder_url}/{name}")
    with urllib.request.urlopen(f"{folder_url}/{name}") as download, partial_image.open("wb") as written:
        while chunk := download.read(_DOWNLOAD_CHUNK_BYTES):
            digest.update(chunk)
            written.write(chunk)
    if digest.hexdigest() != expected:
        partial_image.unlink()
        sys.exit(f"{name} does not match Ubuntu's SHA256SUMS")
    partial_image.rename(image)
    return image


def serve_cloud_init(vm_folder: Path, release: str, public_key: str) -> tuple[str, http.server.ThreadingHTTPServer]:
    """Serves cloud-init's first-boot settings from `vm_folder`, on a free port of 127.0.0.1, which the VM reaches as
    10.0.2.2: the user `ubuntu`, with sudo and the key, and the host name. Returns their address in the VM."""
    seed = vm_folder / "cloud-init"
    seed.mkdir()
    host_name = f"scala-try-{release.replace('.', '')}"
    (seed / "meta-data").write_text(f"instance-id: {vm_folder.name}\nlocal-hostname: {host_name}\n", encoding="utf-8")
    (seed / "user-data").write_text(
        "#cloud-config\n"
        f"hostname: {host_name}\n"
        "users:\n"
        f"  - name: {_VM_USER}\n"
        "    sudo: ALL=(ALL) NOPASSWD:ALL\n"
        "    shell: /bin/bash\n"
        f"    ssh_authorized_keys: [{public_key}]\n",
        encoding="utf-8",
    )
    (seed / "vendor-data").write_text("", encoding="utf-8")
    handler = partial(http.server.SimpleHTTPRequestHandler, directory=str(seed))
    seed_server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=seed_server.serve_forever, daemon=True).start()
    return f"http://10.0.2.2:{seed_server.server_address[1]}/", seed_server


def qemu_command(args: argparse.Namespace, disk: Path, seed_url: str, console_log: Path) -> list[str]:
    return [
        "qemu-system-x86_64",
        "-enable-kvm",
        "-cpu",
        "host",
        "-m",
        f"{args.memory_gib}G",
        "-smp",
        str(args.processors),
        "-drive",
        f"file={disk},if=virtio",
        "-netdev",
        f"user,id=net0,hostfwd=tcp:127.0.0.1:{_SSH_PORT}-:22",
        "-device",
        "virtio-net-pci,netdev=net0",
        "-smbios",
        f"type=1,serial=ds=nocloud;s={seed_url}",
        "-display",
        "none",
        "-serial",
        f"file:{console_log}",
    ]


def setup_command(server_url: str, wheel_name: str, setup_code: str) -> str:
    """The command the server's page gives, with the wheel the script installs until Scala is on PyPI."""
    package = f"/home/{_VM_USER}/{wheel_name}"
    return f"curl -fsSL {server_url}/runner/setup.sh | sudo SC2SCALA_PACKAGE={package} bash -s -- {setup_code}"


class SshToVm:
    """SSH and SCP to the VM's user, by the VM's own key, on the forwarded port."""

    def __init__(self, key: Path) -> None:
        self._options = [
            "-i",
            str(key),
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "LogLevel=ERROR",
            "-o",
            "ServerAliveInterval=30",
        ]

    def command(self, *remote: str, ssh_options: Sequence[str] = ()) -> list[str]:
        """SSH to the VM running `remote`, or a shell if none, with `ssh_options` besides the key's."""
        return ["ssh", *self._options, *ssh_options, "-p", str(_SSH_PORT), f"{_VM_USER}@127.0.0.1", *remote]

    def copy_in(self, local_file: Path) -> None:
        run(["scp", *self._options, "-P", str(_SSH_PORT), str(local_file), f"{_VM_USER}@127.0.0.1:"])

    def wait_for_boot(self) -> None:
        """Waits until the VM answers by SSH and cloud-init is done; exits if it does not within 5 minutes."""
        deadline = time.monotonic() + _BOOT_SECONDS
        while time.monotonic() < deadline:
            probe = subprocess.run(
                self.command("true", ssh_options=["-o", "ConnectTimeout=5"]), capture_output=True, check=False
            )
            if probe.returncode == 0:
                run(self.command("cloud-init", "status", "--wait"), quiet=True)
                return
            time.sleep(5)
        sys.exit(f"The VM did not answer by SSH within {_BOOT_SECONDS} seconds")


def _stop_as_on_ctrl_c(signal_number: int, frame: FrameType | None) -> None:
    """Stops on SIGTERM as on Ctrl-C, so that the VM stops too, rather than outliving the tool."""
    raise KeyboardInterrupt


def run(command: list[str], *, quiet: bool = False) -> None:
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL if quiet else None)


if __name__ == "__main__":
    main()
