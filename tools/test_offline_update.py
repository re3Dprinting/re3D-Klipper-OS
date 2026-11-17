import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "src/modules/fullpageos/filesystem/opt/mconfig"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


offline = load("offline", ASSETS / "offline_update.py")
builder = load("builder", ROOT / "tools/build_offline_update.py")
TARGET = {"distribution": "debian", "release": "12", "architecture": "armhf",
          "python_abi": "cpython-311-arm-linux-gnueabihf"}


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.repo = self.root / "source"
        (self.repo / "tools").mkdir(parents=True)
        (self.repo / "tools/re3d-os-update.sh").write_text("#!/bin/bash\nexit 0\n")
        for args in (("init",), ("add", "."), ("-c", "user.name=Test",
                     "-c", "user.email=test@example.invalid", "commit", "-m", "fixture")):
            subprocess.run(["git", "-C", str(self.repo), *args], check=True,
                           capture_output=True)
        self.image = self.root / "image"
        for name in offline.SOFTWARE:
            path = self.image / "home/pi" / name
            path.mkdir(parents=True)
            (path / "built-file").write_text(f"built {name}\n")
        private = self.image / "home/pi/printer_data/config"
        private.mkdir(parents=True)
        (private / "printer.cfg").write_text("printer settings must not be bundled")
        self.packages = self.root / "debs"
        self.packages.mkdir()
        (self.packages / "test_1_armhf.deb").write_bytes(b"fixture package")
        self.bundle = self.root / offline.BUNDLE_NAME
        with patch.object(builder, "ROOT", self.repo):
            builder.build(self.image, self.packages, TARGET, self.bundle)

    def rewrite(self, change):
        with tarfile.open(self.bundle, "r:") as archive:
            entries = {item.name: archive.extractfile(item).read() for item in archive}
        manifest = json.loads(entries["manifest.json"])
        change(entries, manifest)
        entries["manifest.json"] = json.dumps(manifest).encode()
        with tarfile.open(self.bundle, "w") as archive:
            for name, data in entries.items():
                member = tarfile.TarInfo(name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))

    def stage(self):
        destination = self.root / "stage"
        destination.mkdir()
        return offline.stage_bundle(self.bundle, destination, TARGET), destination

    def test_build_verify_and_stage_round_trip_excludes_printer_data(self):
        manifest, stage = self.stage()
        self.assertEqual(len(manifest["commit"]), 40)
        self.assertTrue((stage / "validated.json").is_file())
        self.assertTrue((stage / "repo/tools/re3d-os-update.sh").is_file())
        self.assertFalse((stage / "repo/.git").exists())
        self.assertFalse((stage / "software/printer_data").exists())
        for name in offline.SOFTWARE:
            self.assertEqual((stage / "software" / name / "built-file").read_text(),
                             f"built {name}\n")
        self.assertEqual((stage / "packages/test_1_armhf.deb").read_bytes(), b"fixture package")
        self.assertEqual(self.bundle.with_suffix(".tar.sha256").read_text().split()[0],
                         hashlib.sha256(self.bundle.read_bytes()).hexdigest())

    def test_corruption_and_incompatible_targets_are_rejected(self):
        for field in TARGET:
            with self.subTest(field=field):
                target = {**TARGET, field: "different"}
                self.assertRaisesRegex(ValueError, "incompatible",
                                       offline.verify_bundle, self.bundle, target)
        self.rewrite(lambda entries, _: entries.__setitem__(
            "software.tar.gz", b"x" + entries["software.tar.gz"][1:]))
        self.assertRaisesRegex(ValueError, "Checksum mismatch",
                               offline.verify_bundle, self.bundle, TARGET)

    def test_duplicate_outer_members_and_unrecognized_payloads_are_rejected(self):
        with tarfile.open(self.bundle, "a") as archive:
            member = tarfile.TarInfo("manifest.json")
            archive.addfile(member, io.BytesIO())
        self.assertRaisesRegex(ValueError, "contents",
                               offline.verify_bundle, self.bundle, TARGET)

    def test_insufficient_space_never_marks_a_bundle_validated(self):
        stage = self.root / "stage"
        stage.mkdir()
        with patch.object(offline.shutil, "disk_usage", return_value=Mock(free=0)):
            self.assertRaisesRegex(ValueError, "free storage",
                                   offline.stage_bundle, self.bundle, stage, TARGET)
        self.assertFalse((stage / "validated.json").exists())

    def test_missing_software_is_not_treated_as_a_successful_bundle(self):
        shutil.rmtree(self.image / "home/pi/moonraker")
        with patch.object(builder, "ROOT", self.repo):
            self.assertRaisesRegex(ValueError, "Missing built software",
                                   builder.build, self.image, self.packages, TARGET,
                                   self.root / "missing.tar")


class ArchiveSafetyTests(unittest.TestCase):
    def archive(self, entries):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as archive:
            for name, kind, value in entries:
                member = tarfile.TarInfo(name)
                member.type = kind
                if kind == tarfile.REGTYPE:
                    member.size = len(value)
                    archive.addfile(member, io.BytesIO(value))
                else:
                    member.linkname = value
                    archive.addfile(member)
        stream.seek(0)
        return tarfile.open(fileobj=stream, mode="r:")

    def test_traversal_links_devices_hardlinks_and_duplicate_paths_are_rejected(self):
        cases = [
            [("../outside", tarfile.REGTYPE, b"x")],
            [("/outside", tarfile.REGTYPE, b"x")],
            [("klipper/outside", tarfile.SYMTYPE, "/etc/shadow")],
            [("klipper", tarfile.SYMTYPE, "/home/pi/klipper")],
            [("klipper/device", tarfile.CHRTYPE, "")],
            [("klipper/link", tarfile.LNKTYPE, "klipper/file")],
            [("klipper/link", tarfile.SYMTYPE, "/usr/lib"),
             ("klipper/link/injected", tarfile.REGTYPE, b"x")],
            [("klipper/file", tarfile.REGTYPE, b"x"),
             ("klipper/file", tarfile.REGTYPE, b"x")],
        ]
        for entries in cases:
            with self.subTest(entries=entries), self.archive(entries) as archive:
                size = sum(len(value) for _, kind, value in entries if kind == tarfile.REGTYPE)
                self.assertRaises(ValueError, offline.payload_members, archive, "software", size)

    def test_real_venv_and_plugin_links_are_allowed_but_package_links_are_not(self):
        entries = [
            ("klippy-env/bin/python", tarfile.SYMTYPE, "/usr/bin/python3"),
            ("moonraker-env/lib/python3.11/site-packages/PIL", tarfile.SYMTYPE,
             "/usr/lib/python3/dist-packages/PIL"),
            ("klipper/klippy/extras/beacon.py", tarfile.SYMTYPE,
             "/home/pi/beacon_klipper/beacon.py"),
        ]
        with self.archive(entries) as archive:
            self.assertEqual(len(offline.payload_members(archive, "software", 0)), 3)
        with self.archive([("fake.deb", tarfile.SYMTYPE, "/usr/lib")]) as archive:
            self.assertRaises(ValueError, offline.payload_members, archive, "packages", 0)

    def test_compressed_payload_size_is_checked_before_extraction(self):
        with self.archive([("klipper/file", tarfile.REGTYPE, b"expanded")]) as archive:
            self.assertRaisesRegex(ValueError, "declared unpacked size",
                                   offline.payload_members, archive, "software", 1)


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.stage = Path(self.directory.name)
        (self.stage / "packages").mkdir()
        (self.stage / "packages/fixture.deb").touch()
        self.run = patch.object(offline, "run", return_value=Mock(
            stdout="Package: fixture\nVersion: 1\n")).start()
        self.process = patch.object(offline.subprocess, "run",
                                    return_value=Mock(returncode=1)).start()
        self.logged = patch.object(offline, "logged_command").start()
        self.allowed = patch.object(offline, "require_allowed").start()
        patch.object(offline, "state").start()
        patch.object(offline, "log").start()
        patch.object(offline, "prevent_package_service_starts",
                     return_value=contextlib.nullcontext()).start()
        self.addCleanup(patch.stopall)

    def test_simulation_failure_never_stops_services_or_deploys_files(self):
        self.logged.side_effect = subprocess.CalledProcessError(100, "apt-get")
        self.assertRaises(subprocess.CalledProcessError, offline.install,
                          self.stage, {"commit": "a" * 40})
        self.assertEqual(self.run.call_count, 1)
        self.assertIn("--simulate", self.logged.call_args.args[0])

    def test_newer_installed_packages_are_rejected_before_installation(self):
        self.process.side_effect = [Mock(returncode=0, stdout="2"),
                                    Mock(returncode=0)]
        self.assertRaisesRegex(ValueError, "newer than the bundle", offline.install,
                               self.stage, {"commit": "a" * 40})
        self.logged.assert_not_called()
        self.assertFalse(any(call.args[:2] == ("systemctl", "stop")
                             for call in self.run.call_args_list))

    def test_package_database_errors_do_not_look_like_missing_packages(self):
        self.process.return_value = Mock(returncode=2, args=["dpkg-query"], stderr="broken")
        self.assertRaises(subprocess.CalledProcessError, offline.install,
                          self.stage, {"commit": "a" * 40})
        self.logged.assert_not_called()

    def test_success_installs_dependencies_before_software_and_requests_reflash_on_change(self):
        events = []
        home = self.stage / "live-home"
        (home / "klipper/.git").mkdir(parents=True)
        flag = self.stage / "reflash"

        def path(value):
            if str(value).startswith("/home/pi"):
                return home / str(value).removeprefix("/home/pi").lstrip("/")
            if str(value) == "/etc/firstboot-splash":
                return flag
            return Path(value)

        def run(*args, **kwargs):
            events.append(args)
            if args[0] == "dpkg-deb":
                return Mock(stdout="Package: fixture\nVersion: 1\n")
            return Mock(stdout="old" if str(home / "klipper") in args else "new")

        self.run.side_effect = run
        self.logged.side_effect = lambda args: events.append(tuple(args))
        with patch.object(offline, "Path", side_effect=path):
            offline.install(self.stage, {"commit": "a" * 40})
        install = next(index for index, event in enumerate(events)
                       if event[0] == "apt-get" and "--simulate" not in event)
        deploy = next(index for index, event in enumerate(events) if event[0] == "rsync")
        self.assertLess(events.index(("systemctl", "stop", "klipper.service",
                                      "moonraker.service")), install)
        self.assertLess(install, deploy)
        self.assertTrue(flag.exists())
        self.assertEqual(events[-1], ("bash", str(self.stage / "repo/tools/re3d-os-update.sh"),
                                      "--offline", str(self.stage)))
        self.assertFalse(any(event[0] in ("curl", "wget", "pip") for event in events))

    def test_package_install_failure_never_copies_software_or_requests_reboot(self):
        self.logged.side_effect = [None, subprocess.CalledProcessError(100, "apt-get")]
        with patch.object(offline, "Path") as path:
            path.return_value.exists.return_value = False
            path.return_value.is_symlink.return_value = False
            (path.return_value / "klipper").is_symlink.return_value = False
            (path.return_value / "klipper").exists.return_value = False
            self.assertRaises(subprocess.CalledProcessError, offline.install,
                              self.stage, {"commit": "a" * 40})
        self.assertFalse(any(call.args[0] in ("rsync", "bash")
                             for call in self.run.call_args_list))

    def test_package_install_uses_private_empty_sources_and_indexes_without_removals(self):
        command = offline.apt_command([self.stage / "packages/fixture.deb"])
        self.assertIn("--no-remove", command)
        options = dict(argument.split("=", 1) for argument in command if "=" in argument)
        sources = Path(options["Dir::Etc::sourcelist"])
        self.assertTrue(sources.is_relative_to(self.stage))
        self.assertEqual(sources.read_text(), "")
        for key in ("Dir::Etc::sourceparts", "Dir::State::lists", "Dir::Cache::archives"):
            directory = Path(options[key])
            self.assertTrue(directory.is_relative_to(self.stage))
            self.assertTrue(directory.is_dir())
        self.assertEqual(list(Path(options["Dir::Etc::sourceparts"]).iterdir()), [])
        self.assertEqual(list(Path(options["Dir::State::lists"]).rglob("*")),
                         [Path(options["Dir::State::lists"]) / "partial"])
        self.assertEqual(options["Dir::Cache::pkgcache"], "")
        self.assertEqual(options["Dir::Cache::srcpkgcache"], "")
        self.assertNotIn("--allow-downgrades", command)


class CommandLoggingTests(unittest.TestCase):
    def test_failed_apt_command_keeps_the_actual_error_visible_without_dumping_all_paths(self):
        process = Mock(stdout=io.StringIO("E: actual package error\n"), returncode=100)
        process.wait.return_value = 100
        command = ["apt-get", *[f"/stage/packages/package-{number}.deb" for number in range(2000)]]
        with patch.object(offline.subprocess, "Popen",
                          return_value=contextlib.nullcontext(process)) as popen, \
                patch.object(offline, "log") as log:
            with self.assertRaises(subprocess.CalledProcessError) as caught:
                offline.logged_command(command)
        log.assert_called_once_with("E: actual package error")
        self.assertEqual(caught.exception.cmd, "apt-get")
        self.assertLess(len(str(caught.exception)), 100)
        self.assertEqual(popen.call_args.kwargs["env"]["DEBIAN_FRONTEND"], "noninteractive")


@unittest.skipUnless(os.environ.get("RE3D_APT_INTEGRATION") == "1",
                     "Run explicitly in a disposable Debian container")
class AptIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual(sys.platform, "linux")
        self.assertEqual(os.geteuid(), 0)
        self.assertTrue(Path("/.dockerenv").is_file(), "Requires a disposable Docker container")
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.stage = Path(self.directory.name)
        self.packages = self.stage / "packages"
        self.packages.mkdir()

    def package(self, name, dependency=""):
        source = self.stage / name
        control = source / "DEBIAN/control"
        control.parent.mkdir(parents=True)
        control.write_text(
            f"Package: {name}\nVersion: 1\nArchitecture: all\n"
            "Maintainer: Test <test@example.invalid>\nInstalled-Size: 1\n"
            + (f"Depends: {dependency}\n" if dependency else "")
            + "Description: Offline APT installation regression fixture\n",
        )
        marker = source / f"usr/share/{name}/marker"
        marker.parent.mkdir(parents=True)
        marker.write_text("bundled content\n")
        archive = self.packages / f"{name}_1_all.deb"
        subprocess.run(["dpkg-deb", "--build", str(source), str(archive)],
                       check=True, capture_output=True, text=True)
        return archive, Path("/") / marker.relative_to(source)

    def test_real_apt_installs_local_packages_and_their_bundled_dependency_without_network(self):
        dependency, dependency_marker = self.package("re3d-offline-fixture-dep")
        package, marker = self.package("re3d-offline-fixture", "re3d-offline-fixture-dep (= 1)")
        command = offline.apt_command([package, dependency])
        environment = {**os.environ, "DEBIAN_FRONTEND": "noninteractive"}
        simulation = subprocess.run([command[0], "--simulate", *command[1:]], text=True,
                                    capture_output=True, env=environment)
        self.assertEqual(simulation.returncode, 0, simulation.stdout + simulation.stderr)
        self.assertFalse(marker.exists())
        result = subprocess.run(command, text=True, capture_output=True, env=environment)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(marker.read_text(), "bundled content\n")
        self.assertEqual(dependency_marker.read_text(), "bundled content\n")
        self.assertNotIn("Unable to read", result.stderr)

    def test_missing_dependency_fails_without_installing_or_fetching_from_external_sources(self):
        package, marker = self.package("re3d-offline-missing-fixture", "re3d-unbundled-dependency")
        result = subprocess.run(offline.apt_command([package]), text=True, capture_output=True,
                                env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"})
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(marker.exists())
        self.assertIn("re3d-unbundled-dependency", result.stdout + result.stderr)
        self.assertNotIn("Failed to fetch", result.stderr)


class EntryPointTests(unittest.TestCase):
    @unittest.skipIf(sys.platform == "win32", "Requires Linux flock; also runs in image-build CI")
    def test_shared_lock_rejects_concurrent_updates_and_is_released(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(offline, "LOCK", Path(directory) / "update.lock"):
            with offline.update_lock():
                with self.assertRaisesRegex(ValueError, "already running"):
                    with offline.update_lock():
                        self.fail("Concurrent update acquired the lock")
            with offline.update_lock():
                pass

    def test_both_persistent_and_applied_airgap_markers_block_usb(self):
        with tempfile.TemporaryDirectory() as directory:
            markers = [Path(directory) / "enabled", Path(directory) / "applied"]
            with patch.object(offline, "AIRGAP", markers), patch.object(offline, "run") as run, \
                    patch.object(offline, "update_lock", side_effect=contextlib.nullcontext), \
                    patch.object(offline, "state"), patch.object(offline, "log"):
                for marker in markers:
                    marker.touch()
                    self.assertRaisesRegex(ValueError, "air-gapped", offline.start)
                    self.assertRaisesRegex(ValueError, "air-gapped", offline.apply)
                    self.assertRaisesRegex(ValueError, "air-gapped", offline.usb_bundle)
                    marker.unlink()
                run.assert_not_called()

    def test_usb_directory_without_a_real_mount_is_not_accepted(self):
        with patch.object(offline, "require_allowed"), \
                patch.object(offline.os.path, "ismount", return_value=False):
            self.assertRaisesRegex(ValueError, "not mounted", offline.usb_bundle)

    def test_start_uses_a_detached_system_service_and_network_namespace(self):
        with patch.object(offline, "require_allowed"), patch.object(offline, "usb_bundle"), \
                patch.object(offline, "update_lock", return_value=contextlib.nullcontext()), \
                patch.object(offline, "run") as run:
            offline.start()
        args = run.call_args.args
        self.assertEqual(args[0], "systemd-run")
        self.assertIn("--unit=re3d-usb-update", args)
        self.assertIn("--net", args)
        self.assertEqual(args[-1], "apply")

    def test_namespace_failure_is_reported_before_accepting_a_background_update(self):
        with patch.object(offline, "require_allowed"), patch.object(offline, "usb_bundle"), \
                patch.object(offline, "update_lock", return_value=contextlib.nullcontext()), \
                patch.object(offline, "run", side_effect=subprocess.CalledProcessError(
                    1, "unshare")) as run:
            self.assertRaises(subprocess.CalledProcessError, offline.start)
        run.assert_called_once_with("/usr/bin/unshare", "--net", "--", "/bin/true")

    def test_failed_validation_sets_error_without_running_install_or_reboot(self):
        with patch.object(offline, "require_allowed"), \
                patch.object(offline, "update_lock", return_value=contextlib.nullcontext()), \
                patch.object(offline, "state") as state, patch.object(offline, "log"), \
                patch.object(offline, "usb_bundle", side_effect=ValueError("invalid bundle")), \
                patch.object(offline, "install") as install:
            self.assertRaisesRegex(ValueError, "invalid bundle", offline.apply)
        state.assert_any_call("error")
        install.assert_not_called()

@unittest.skipUnless(sys.platform == "linux" and os.geteuid() == 0,
                     "Requires Linux root; exercised separately in image-build CI")
class BedCyclingPermissionTests(unittest.TestCase):
    def setUp(self):
        import pwd
        import grp

        self.user = pwd.getpwnam("nobody")
        self.group = grp.getgrgid(self.user.pw_gid)
        self.directory = tempfile.TemporaryDirectory(prefix="re3d-bed-cycling-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.root.chmod(0o755)
        self.stage = self.root / "private-stage"
        self.stage.mkdir(mode=0o700)
        self.source = self.stage / "repo/Bed Cycling"
        self.source.mkdir(parents=True)
        self.destination = self.root / "gcodes/Bed Cycling"
        self.destination.parent.mkdir(mode=0o755)
        os.chown(self.destination.parent, self.user.pw_uid, self.user.pw_gid)
        source = (ROOT / "tools/re3d-os-update.sh").read_text()
        self.script = (source.split("# ---------- Bundled bed cycling: add missing files only ----------\n", 1)[1]
                       .split("# ---------- 1) Configurator:", 1)[0])
        self.script = self.script.replace("sudo -u pi", f"runuser -u {self.user.pw_name} --").replace(
            "--chown=pi:pi", f"--chown={self.user.pw_name}:{self.group.gr_name}")

    def deploy(self, script=None):
        result = subprocess.run(
            ["bash", "-c", 'set -euo pipefail\nlog() { echo "$*"; }\nLOG_TAG=test\n'
             + (self.script if script is None else script)],
            text=True, capture_output=True,
            env={**os.environ, "SRC_BED_CYCLING": str(self.source),
                 "DST_BED_CYCLING": str(self.destination)},
        )
        return result

    def test_root_only_usb_source_adds_missing_files_and_preserves_existing_gcode(self):
        (self.source / "existing.gcode").write_text("bundle's older content\n")
        (self.source / "missing file.gcode").write_text("new bundled content\n")
        (self.source / "notes.txt").write_text("not G-code\n")
        self.destination.mkdir()
        os.chown(self.destination, self.user.pw_uid, self.user.pw_gid)
        existing = self.destination / "existing.gcode"
        existing.write_text("operator's customized content\n")
        os.chown(existing, self.user.pw_uid, self.user.pw_gid)
        existing.chmod(0o600)
        unreadable = subprocess.run(
            ["runuser", "-u", self.user.pw_name, "--", "cat",
             str(self.source / "missing file.gcode")], capture_output=True,
        )
        self.assertNotEqual(unreadable.returncode, 0)
        for _ in range(2):
            result = self.deploy()
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(existing.read_text(), "operator's customized content\n")
            self.assertEqual(existing.stat().st_mode & 0o777, 0o600)
            added = self.destination / "missing file.gcode"
            self.assertEqual(added.read_text(), "new bundled content\n")
            self.assertEqual((added.stat().st_uid, added.stat().st_gid),
                             (self.user.pw_uid, self.user.pw_gid))
        self.assertFalse((self.destination / "notes.txt").exists())
        self.assertEqual(self.stage.stat().st_mode & 0o777, 0o700)

    def test_missing_destination_is_created_for_the_printer_user(self):
        (self.source / "new.gcode").write_text("new bundled content\n")
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.destination.stat().st_uid, self.user.pw_uid)
        self.assertEqual((self.destination / "new.gcode").read_text(), "new bundled content\n")


class ShellFlowTests(unittest.TestCase):
    def bash(self, source, directory=None):
        git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
        bash = str(git_bash) if git_bash.exists() else shutil.which("bash")
        if not bash:
            self.skipTest("Bash is required for updater flow tests")
        return subprocess.run([bash, "-c", source], cwd=directory,
                              text=True, capture_output=True)

    def test_offline_deployment_never_enters_online_software_installers(self):
        source = (ROOT / "tools/re3d-os-update.sh").read_text(encoding="utf-8")
        sections = (source[source.index("# ---------- 6)"):source.index("# ---------- 9.5)")],
                    source[source.index("# ---------- 9.6)"):
                           source.index("# Deploy KlipperScreen config")])
        prefix = """set -euo pipefail
LOG_TAG=test
log() { echo "$*"; }
set_progress() { :; }
apt-get() { echo network-installer >&2; exit 91; }
git() { echo network-installer >&2; exit 91; }
curl() { echo network-installer >&2; exit 91; }
wget() { echo network-installer >&2; exit 91; }
sudo() { echo network-installer >&2; exit 91; }
"""
        for section in sections:
            result = self.bash(prefix + "OFFLINE=1\n" + section)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("network-installer", result.stderr)
            online = self.bash(prefix + "OFFLINE=0\n" + section)
            self.assertEqual(online.returncode, 91, online.stdout + online.stderr)

    def test_dependency_download_includes_local_debs_and_the_full_apt_dependency_closure(self):
        source = (ROOT / "tools/build-offline-update.sh").read_text()
        prepare = source.split("<<'PREPARE'\n", 1)[1].split("for venv in ", 1)[0]
        prepare = prepare.replace("/var/lib/re3d-offline-packages", "./local").replace(
            "/var/tmp/re3d-offline-packages", "./cache")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "local").mkdir()
            (root / "local/local-tool.deb").write_bytes(b"fixture")
            result = self.bash("""
apt-get() { printf '%s\\n' "$@" >> apt-calls; }
dpkg-deb() {
  if [ "$3" = Package ]; then echo local-tool; else echo 1; fi
}
dpkg-query() {
  if [ "$#" -eq 3 ]; then
    echo 1
  else
    printf 'installed local-tool\\ninstalled core:armhf\\nconfig-files removed\\n'
  fi
}
""" + prepare, directory)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            arguments = (root / "apt-calls").read_text().splitlines()
            self.assertIn("Dir::State::status=/dev/null", arguments)
            self.assertIn("--download-only", arguments)
            self.assertIn("core:armhf", arguments)
            self.assertIn("./cache/local-tool.deb", arguments)
            self.assertNotIn("local-tool", arguments)
            self.assertNotIn("removed", arguments)


if __name__ == "__main__":
    unittest.main()
