#!/usr/bin/env python3
"""Package a built image's software and downloaded dependencies, not printer data."""

import argparse
import datetime
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "offline_update", ROOT / "src/modules/fullpageos/filesystem/opt/mconfig/offline_update.py",
)
offline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(offline)


def pack_tree(archive, source, name):
    member = archive.gettarinfo(source, arcname=name)
    if member.islnk():
        member.type = tarfile.REGTYPE
        member.linkname = ""
        member.size = source.stat().st_size
    if member.isfile():
        with source.open("rb") as stream:
            archive.addfile(member, stream)
    else:
        archive.addfile(member)
    if source.is_dir() and not source.is_symlink():
        for child in sorted(source.iterdir()):
            pack_tree(archive, child, name + "/" + child.name)


def describe(path, kind):
    with tarfile.open(path, "r:*") as archive:
        size = sum(member.size for member in archive.getmembers())
        offline.payload_members(archive, kind, size)
    with path.open("rb") as stream:
        checksum = offline.digest(stream)
    return {"size": path.stat().st_size, "unpacked_size": size, "sha256": checksum}


def build(image_root, packages, target, output):
    with tempfile.TemporaryDirectory(prefix="re3d-bundle-", dir=output.parent) as directory:
        work = Path(directory)
        git = ["git", "-c", f"safe.directory={ROOT}", "-C", str(ROOT)]
        subprocess.run([*git, "archive", "--format=tar.gz",
                        "-o", str(work / "repo.tar.gz"), "HEAD"], check=True)
        with tarfile.open(work / "software.tar.gz", "w:gz", compresslevel=1) as archive:
            for name in offline.SOFTWARE:
                source = image_root / "home/pi" / name
                if not source.is_dir() or source.is_symlink():
                    raise ValueError(f"Missing built software: {name}")
                pack_tree(archive, source, name)
        debs = sorted(packages.glob("*.deb"))
        if not debs:
            raise ValueError("No offline system packages were downloaded.")
        with tarfile.open(work / "packages.tar", "w") as archive:
            for package in debs:
                pack_tree(archive, package, package.name)
        manifest = {
            "format": offline.FORMAT,
            "commit": subprocess.check_output(
                [*git, "rev-parse", "HEAD"], text=True).strip(),
            "built_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "compatibility": target,
            "payloads": {name: describe(work / name, kind) for name, kind in
                         zip(offline.PAYLOADS, ("repo", "software", "packages"))},
        }
        data = json.dumps(manifest, indent=2).encode()
        temporary = work / offline.BUNDLE_NAME
        with tarfile.open(temporary, "w") as archive:
            header = tarfile.TarInfo("manifest.json")
            header.size = len(data)
            archive.addfile(header, io.BytesIO(data))
            for name in offline.PAYLOADS:
                archive.add(work / name, arcname=name, recursive=False)
        offline.verify_bundle(temporary, target)
        temporary.replace(output)
    with output.open("rb") as stream:
        checksum = offline.digest(stream)
    output.with_suffix(output.suffix + ".sha256").write_text(
        f"{checksum}  {output.name}\n", encoding="ascii",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image_root", type=Path)
    parser.add_argument("packages", type=Path)
    parser.add_argument("compatibility", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    build(args.image_root, args.packages, json.loads(args.compatibility.read_text()), args.output)
