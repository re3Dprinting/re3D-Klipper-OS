#!/usr/bin/env python3
"""Build-format validation and installation for manually selected USB updates."""

import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import shutil
import subprocess
import sys
import sysconfig
import tarfile
import tempfile


FORMAT = 1
USB = Path("/media/usb0")
BUNDLE_NAME = "re3d-os-update.tar"
AIRGAP = (Path("/etc/re3d-airgap/enabled"), Path("/run/re3d-airgap/enabled"))
UI = Path("/opt/mconfig/www")
LOCK = Path("/run/lock/re3d-os-update.lock")
PAYLOADS = ("repo.tar.gz", "software.tar.gz", "packages.tar")
SOFTWARE = (
    "klipper", "klippy-env", "moonraker", "moonraker-env", "mainsail",
    "KlipperScreen", "klipperscreen-env", "beacon_klipper",
    "moonraker-timelapse", "virtual_keyboard",
)
MAX_BYTES = 16 * 1024**3
RESERVE = 512 * 1024**2


def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, **kwargs)


def compatibility():
    release = {}
    for line in Path("/etc/os-release").read_text().splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            release[key] = value.strip('"')
    return {
        "distribution": release["ID"],
        "release": release["VERSION_ID"],
        "architecture": run("dpkg", "--print-architecture", capture_output=True).stdout.strip(),
        "python_abi": sysconfig.get_config_var("SOABI"),
    }


def require_allowed():
    if any(path.exists() for path in AIRGAP):
        raise ValueError("Updates are disabled in air-gapped mode. Disable it and reboot first.")


def usb_bundle():
    require_allowed()
    if not os.path.ismount(USB.resolve()):
        raise ValueError("USB drive is not mounted at /media/usb0.")
    bundle = USB / BUNDLE_NAME
    if bundle.is_symlink() or not bundle.is_file():
        raise ValueError(f"Copy {BUNDLE_NAME} to the root of the USB drive first.")
    return bundle


def digest(stream):
    result = hashlib.sha256()
    while chunk := stream.read(1024 * 1024):
        result.update(chunk)
    return result.hexdigest()


def read_manifest(archive, target):
    members = []
    for member in archive:
        members.append(member)
        if len(members) > 4:
            raise ValueError("Invalid bundle contents.")
    if (len(members) != 4 or {member.name for member in members} != {"manifest.json", *PAYLOADS}
            or any(not member.isfile() for member in members)):
        raise ValueError("Invalid bundle contents.")
    header = archive.getmember("manifest.json")
    if header.size > 65536:
        raise ValueError("Bundle manifest is too large.")
    try:
        manifest = json.load(archive.extractfile(header))
    except (ValueError, RecursionError) as error:
        raise ValueError("Invalid bundle manifest JSON.") from error
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise ValueError("Unsupported USB update format.")
    if manifest.get("compatibility") != target:
        raise ValueError("Bundle is incompatible with this OS release, architecture or Python ABI.")
    if not re.fullmatch(r"[0-9a-f]{40,64}", str(manifest.get("commit", ""))):
        raise ValueError("Invalid bundle commit.")
    payloads = manifest.get("payloads")
    if not isinstance(payloads, dict) or set(payloads) != set(PAYLOADS):
        raise ValueError("Invalid bundle payload manifest.")
    for name in PAYLOADS:
        entry = payloads[name]
        if (not isinstance(entry, dict)
                or any(type(entry.get(key)) is not int or not 0 < entry[key] <= MAX_BYTES
                       for key in ("size", "unpacked_size"))
                or not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("sha256", "")))
                or archive.getmember(name).size != entry["size"]):
            raise ValueError(f"Invalid payload metadata: {name}")
    return manifest


def verify_bundle(bundle, target):
    with tarfile.open(bundle, "r:") as archive:
        manifest = read_manifest(archive, target)
        for name in PAYLOADS:
            with archive.extractfile(name) as stream:
                if digest(stream) != manifest["payloads"][name]["sha256"]:
                    raise ValueError(f"Checksum mismatch: {name}. Download the bundle again.")
    return manifest


def payload_members(archive, kind, expected_size):
    members = []
    paths = {}
    total = 0
    for member in archive:
        members.append(member)
        if len(members) > 250000:
            raise ValueError("Payload has too many archive entries.")
        path = PurePosixPath(member.name)
        if (path.is_absolute() or ".." in path.parts or not path.parts
                or path.as_posix() != member.name.rstrip("/") or path in paths):
            raise ValueError(f"Unsafe or duplicate archive path: {member.name}")
        if not (member.isfile() or member.isdir() or member.issym()):
            raise ValueError(f"Unsupported archive entry: {member.name}")
        if kind == "packages":
            allowed = len(path.parts) == 1 and member.isfile() and path.suffix == ".deb"
        elif kind == "software":
            allowed = path.parts[0] in SOFTWARE and (len(path.parts) > 1 or member.isdir())
        else:
            allowed = path.parts[0] not in (".git",)
        if not allowed:
            raise ValueError(f"Unexpected {kind} path: {member.name}")
        if member.issym():
            resolved = posixpath.normpath(posixpath.join(
                "/home/pi" if kind == "software" else "/repo",
                str(path.parent), member.linkname,
            ))
            roots = ([f"/home/pi/{name}" for name in SOFTWARE]
                     + ["/usr/bin", "/usr/lib"] if kind == "software" else ["/repo"])
            if not any(resolved == root or resolved.startswith(root + "/") for root in roots):
                raise ValueError(f"Unsafe archive symlink: {member.name}")
        total += member.size
        if total > expected_size:
            raise ValueError("Payload exceeds its declared unpacked size.")
        paths[path] = member
    if total != expected_size:
        raise ValueError("Incorrect unpacked payload size.")
    for path in paths:
        if any(parent in paths and not paths[parent].isdir() for parent in path.parents):
            raise ValueError(f"Archive entry has a non-directory parent: {path}")
    return members


def unpack_payload(source, destination, kind, expected_size):
    with tarfile.open(source, "r:*") as archive:
        members = payload_members(archive, kind, expected_size)
        destination.mkdir()
        for member in members:
            path = destination / member.name
            path.parent.mkdir(parents=True, exist_ok=True)
            if member.isdir():
                path.mkdir(exist_ok=True)
            elif member.issym():
                path.symlink_to(member.linkname)
            else:
                with archive.extractfile(member) as incoming, path.open("xb") as outgoing:
                    shutil.copyfileobj(incoming, outgoing)
                path.chmod(member.mode & 0o777)
                os.utime(path, (member.mtime, member.mtime))


def stage_bundle(bundle, stage, target):
    snapshot = stage / BUNDLE_NAME
    shutil.copyfile(bundle, snapshot)
    manifest = verify_bundle(snapshot, target)
    required = sum(entry["unpacked_size"] for entry in manifest["payloads"].values())
    if shutil.disk_usage(stage).free < snapshot.stat().st_size + required * 2 + RESERVE:
        raise ValueError("Not enough free storage to stage and install this USB update.")
    with tarfile.open(snapshot, "r:") as archive:
        for name, kind in zip(PAYLOADS, ("repo", "software", "packages")):
            payload = stage / name
            with archive.extractfile(name) as incoming, payload.open("xb") as outgoing:
                shutil.copyfileobj(incoming, outgoing)
            unpack_payload(payload, stage / kind, kind,
                           manifest["payloads"][name]["unpacked_size"])
            payload.unlink()
    snapshot.unlink()
    for name in SOFTWARE:
        if not (stage / "software" / name).is_dir():
            raise ValueError(f"Bundle is missing required software: {name}")
    if not (stage / "repo/tools/re3d-os-update.sh").is_file():
        raise ValueError("Bundle is missing the OS deployment script.")
    if not list((stage / "packages").glob("*.deb")):
        raise ValueError("Bundle has no system dependencies.")
    (stage / "validated.json").write_text(json.dumps(manifest))
    return manifest


def log(message):
    print(message, flush=True)
    with (UI / "update_log.txt").open("a") as output:
        output.write(message + "\n")


def state(value, progress=None):
    (UI / "update.txt").write_text(value + "\n")
    if progress is not None:
        (UI / "update_progress.txt").write_text(str(progress) + "\n")


def apt_command(packages):
    return [
        "apt-get", "--assume-yes", "--no-download", "--no-remove",
        "-o", "Dir::Etc::sourcelist=/dev/null", "-o", "Dir::Etc::sourceparts=-",
        "-o", "Dpkg::Options::=--force-confold", "install", *map(str, packages),
    ]


def logged_command(args):
    with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True) as process:
        for line in process.stdout:
            log(line.rstrip())
        if process.wait():
            raise subprocess.CalledProcessError(process.returncode, args)


@contextlib.contextmanager
def prevent_package_service_starts(stage):
    policy = Path("/usr/sbin/policy-rc.d")
    backup = stage / "policy-rc.d"
    existed = policy.exists() or policy.is_symlink()
    if existed:
        shutil.copy2(policy, backup, follow_symlinks=False)
        policy.unlink()
    try:
        policy.write_text("#!/bin/sh\nexit 101\n")
        policy.chmod(0o755)
        yield
    finally:
        policy.unlink(missing_ok=True)
        if existed:
            shutil.copy2(backup, policy, follow_symlinks=False)


def install(stage, manifest):
    require_allowed()
    packages = sorted((stage / "packages").glob("*.deb"))
    command = apt_command(packages)
    # Refuse downgrades before stopping anything; mixing newer system libraries
    # with a build's older Python environments is not a supported migration.
    for package in packages:
        name, version = run("dpkg-deb", "-f", str(package), "Package", "Version",
                            capture_output=True).stdout.splitlines()
        name = name.removeprefix("Package: ")
        version = version.removeprefix("Version: ")
        installed = subprocess.run(
            ["dpkg-query", "-W", "-f=${Version}", name], text=True, capture_output=True,
        )
        if installed.returncode == 0:
            comparison = subprocess.run(["dpkg", "--compare-versions",
                                         installed.stdout, "gt", version])
            if comparison.returncode == 0:
                raise ValueError(f"Installed {name} is newer than the bundle; use a newer bundle.")
            if comparison.returncode != 1:
                raise subprocess.CalledProcessError(comparison.returncode, comparison.args)
        elif installed.returncode != 1:
            raise subprocess.CalledProcessError(installed.returncode, installed.args,
                                                stderr=installed.stderr)
    logged_command([*command[:1], "--simulate", *command[1:]])
    for name in SOFTWARE:
        destination = Path("/home/pi") / name
        if destination.is_symlink() or (destination.exists() and not destination.is_dir()):
            raise ValueError(f"Software destination is not a regular directory: {destination}")
    require_allowed()
    old_klipper = Path("/home/pi/klipper")
    old_revision = None
    if (old_klipper / ".git").is_dir():
        old_revision = run("git", "-c", f"safe.directory={old_klipper}",
                           "-C", str(old_klipper), "rev-parse", "HEAD",
                           capture_output=True).stdout.strip()
    new_revision = run("git", "-C", str(stage / "software/klipper"),
                       "rev-parse", "HEAD", capture_output=True).stdout.strip()
    state("running", 15)
    log("Stopping printer services. Do not remove power or the USB drive.")
    run("systemctl", "stop", "klipper.service", "moonraker.service")
    with prevent_package_service_starts(stage):
        logged_command(command)
    state("running", 30)
    for name in SOFTWARE:
        run("rsync", "-a", "--delete", "--chown=pi:pi",
            str(stage / "software" / name) + "/", f"/home/pi/{name}/")
    if old_revision != new_revision:
        Path("/etc/firstboot-splash").touch()
        log("Klipper changed; the Archimajor mainboard must be re-flashed after reboot.")
    log(f"Installing re:3D OS commit {manifest['commit']} from USB (no network).")
    run("bash", str(stage / "repo/tools/re3d-os-update.sh"), "--offline", str(stage))


@contextlib.contextmanager
def update_lock():
    import fcntl

    with LOCK.open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("Another OS update is already running.") from error
        yield


def apply():
    with update_lock():
        try:
            require_allowed()
            state("running", 1)
            log("Validating and staging USB update...")
            bundle = usb_bundle()
            # Reserve enough room for a private copy even before reading its manifest.
            if bundle.stat().st_size > MAX_BYTES or shutil.disk_usage("/var/tmp").free < (
                    bundle.stat().st_size * 2 + RESERVE):
                raise ValueError("Bundle is too large or there is not enough free storage.")
            with tempfile.TemporaryDirectory(prefix="re3d-usb-update-", dir="/var/tmp") as directory:
                stage = Path(directory)
                manifest = stage_bundle(bundle, stage, compatibility())
                install(stage, manifest)
        except (OSError, ValueError, tarfile.TarError, subprocess.SubprocessError) as error:
            state("error")
            log(f"USB update failed: {error}. No automatic reboot will be requested.")
            raise


def start():
    require_allowed()
    usb_bundle()
    with update_lock():
        pass
    run("/usr/bin/unshare", "--net", "--", "/bin/true")
    # The transient service survives CGI/nginx/package restarts. A network
    # namespace makes accidental network-dependent installers fail closed.
    run("systemd-run", "--unit=re3d-usb-update", "--collect",
        "--property=Type=exec", "--property=StandardOutput=append:/var/log/re3d-usb-update.log",
        "--property=StandardError=inherit",
        "/usr/bin/unshare", "--net", "--", "/usr/bin/python3",
        "/opt/mconfig/offline_update.py", "apply")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inspect", "start", "apply", "compatibility"))
    args = parser.parse_args()
    try:
        if args.action == "compatibility":
            print(json.dumps(compatibility()))
        elif args.action == "inspect":
            manifest = verify_bundle(usb_bundle(), compatibility())
            print(json.dumps({"commit": manifest["commit"],
                              "built_at": manifest.get("built_at", ""),
                              "compatibility": manifest["compatibility"]}))
        elif args.action == "start":
            start()
            print("USB update started. See /var/log/re3d-usb-update.log for persistent logs.")
        else:
            apply()
    except (OSError, ValueError, tarfile.TarError, subprocess.SubprocessError) as error:
        print(f"USB update error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
