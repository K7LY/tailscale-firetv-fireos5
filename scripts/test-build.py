#!/usr/bin/env python3
"""Run build.sh against local sources and fake build tools (no downloads)."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


BUILD_SCRIPT = Path(__file__).with_name("build.sh")
DEFAULT_REF = "1.98.8-t1241b225b-gbcbaf1889"


class BuildReplayTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "project"
        self.src = self.repo / ".build/tailscale-android-gbcbaf1889"
        self.tools = self.root / "tools"
        self.tools.mkdir()
        (self.repo / "scripts").mkdir(parents=True)
        shutil.copy2(BUILD_SCRIPT, self.repo / "scripts/build.sh")
        self.write(self.repo / "scripts/verify-apk.py", "import sys\nfrom pathlib import Path\nassert Path(sys.argv[-1]).is_file()\n")
        self.write(self.root / "jdk/bin/java", '#!/bin/sh\necho \'openjdk version "17.0.1"\' >&2\n', executable=True)
        sdk = self.root / "sdk"
        for directory in ("ndk/23.1.7779620", "platforms/android-34", "build-tools/34.0.0"):
            (sdk / directory).mkdir(parents=True)
        self.write(sdk / "cmdline-tools/latest/bin/sdkmanager", "#!/bin/sh\nexit 99\n", executable=True)
        self.real_git = shutil.which("git")
        self.write(self.tools / "git", '#!/bin/sh\nfor arg do\n  if [ "$arg" = fetch ]; then echo "unexpected network fetch" >&2; exit 99; fi\ndone\nexec "$TEST_REAL_GIT" "$@"\n', executable=True)
        self.write(self.tools / "curl", "#!/bin/sh\nexit 99\n", executable=True)
        self.write(self.tools / "go", '#!/bin/sh\ncase "$1" in\n  env) exit 0 ;;\n  version) echo "go version fixture" ;;\n  install) exit 0 ;;\n  *) exit 99 ;;\nesac\n', executable=True)
        self.write(self.tools / "gomobile", '#!/bin/sh\nprintf "fixture aar\\n" > android/libs/libtailscale.aar\n', executable=True)
        self.src.mkdir(parents=True)
        self.git("init", "-q")
        self.git("config", "user.name", "Build Test")
        self.git("config", "user.email", "build-test@example.invalid")
        self.write(self.src / "android/build.gradle", 'minSdkVersion 26\nversionName "1.98.8"\n')
        self.write(self.src / "android/gradle.properties", "org.gradle.jvmargs=-Xmx1g\n")
        self.write(self.src / "android/gradlew", '#!/bin/sh\nmkdir -p build/outputs/apk/debug\nprintf "fixture apk\\n" > build/outputs/apk/debug/app-debug.apk\n', executable=True)
        self.write(self.src / "libtailscale/source.go", "package libtailscale\n")
        self.write(self.src / "tailscale.version", 'VERSION_LONG="1.98.8"\n')
        self.write(self.src / "shared.txt", "original\n")
        self.git("add", ".")
        self.git("commit", "-qm", "fixture base")
        self.base = self.git("rev-parse", "HEAD").strip()
        self.env = dict(os.environ, PATH=str(self.tools) + os.pathsep + os.environ["PATH"],
                        JAVA_HOME=str(self.root / "jdk"), ANDROID_SDK_ROOT=str(sdk),
                        TEST_REAL_GIT=self.real_git, TS_REF=DEFAULT_REF, WORKDIR=".build",
                        MIN_SDK="22", NDK_VERSION="23.1.7779620",
                        GOMOBILE_TARGET="android/arm", SKIP_TESTS="1", GRADLE_HEAP="1g")

    @staticmethod
    def write(path, content, executable=False):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        if executable:
            path.chmod(0o755)

    def git(self, *args):
        return subprocess.run([self.real_git, "-C", str(self.src), *args],
                              check=True, capture_output=True, text=True).stdout

    def build(self, *, use_default_ref=False):
        env = self.env.copy()
        if use_default_ref:
            env.pop("TS_REF")
        return subprocess.run(["bash", str(self.repo / "scripts/build.sh")], env=env,
                              capture_output=True, text=True, timeout=30)

    def assert_build_succeeds(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        artifact = self.repo / "dist/tailscale-fireos5-1.98.8-minsdk22-armeabi-v7a.apk"
        self.assertEqual(artifact.read_text(), "fixture apk\n")

    def add_overlapping_patches(self, added_name="added.txt"):
        patches = self.repo / "patches/1.98.8"
        previous = self.base
        for number, value in enumerate(("first", "second"), 1):
            self.write(self.src / "shared.txt", value + "\n")
            self.write(self.src / added_name, value + "\n")
            self.git("add", ".")
            self.git("commit", "-qm", f"fixture patch {number}")
            current = self.git("rev-parse", "HEAD").strip()
            self.write(patches / f"{number:04d}-fixture.patch", self.git("diff", previous, current))
            previous = current
        self.git("reset", "--hard", self.base)

    def test_default_ref_builds_documented_release(self):
        self.assert_build_succeeds(self.build(use_default_ref=True))

    def test_empty_patch_directory_builds(self):
        (self.repo / "patches/1.98.8").mkdir(parents=True)
        self.assert_build_succeeds(self.build())

    def test_partially_applied_patch_stack_can_build(self):
        self.add_overlapping_patches()
        self.git("apply", str(self.repo / "patches/1.98.8/0001-fixture.patch"))
        self.assert_build_succeeds(self.build())
        self.assertEqual((self.src / "shared.txt").read_text(), "second\n")
        self.assertEqual((self.src / "added.txt").read_text(), "second\n")

    def test_edited_patch_added_file_is_preserved_on_conflict(self):
        self.add_overlapping_patches()
        self.assert_build_succeeds(self.build())
        self.write(self.src / "added.txt", "user change\n")
        result = self.build()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot safely reset checkout", result.stderr)
        self.assertIn("added.txt", result.stderr)
        self.assertEqual((self.src / "added.txt").read_text(), "user change\n")

    def test_tracked_edit_conflict_preserves_sources_before_reset(self):
        self.add_overlapping_patches()
        self.assert_build_succeeds(self.build())
        self.write(self.src / "shared.txt", "user tracked change\n")
        result = self.build()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.src / "shared.txt").read_text(), "user tracked change\n")
        self.assertEqual((self.src / "added.txt").read_text(), "second\n")
        self.assertIn("cannot safely reset checkout", result.stderr)
        self.assertIn("added.txt", result.stderr)

    def test_quoted_patch_paths_rebuild_and_report_conflicts(self):
        added_name = 'added "source"\t[1].txt'
        self.add_overlapping_patches(added_name=added_name)
        self.assert_build_succeeds(self.build())
        self.assert_build_succeeds(self.build())
        self.write(self.src / "shared.txt", "user tracked change\n")
        self.write(self.src / (added_name + ".notes"), "unrelated notes\n")
        result = self.build()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.src / "shared.txt").read_text(), "user tracked change\n")
        self.assertEqual((self.src / added_name).read_text(), "second\n")
        self.assertEqual((self.src / (added_name + ".notes")).read_text(), "unrelated notes\n")
        self.assertIn(added_name, result.stderr)

    def test_rebuild_reapplies_new_files_and_overlapping_changes(self):
        self.add_overlapping_patches()
        self.assert_build_succeeds(self.build())
        self.assertEqual((self.src / "shared.txt").read_text(), "second\n")
        self.assertEqual((self.src / "added.txt").read_text(), "second\n")
        self.write(self.src / "user-notes.txt", "keep this scratch file\n")
        for _ in range(2):
            self.assert_build_succeeds(self.build())
            self.assertEqual((self.src / "shared.txt").read_text(), "second\n")
            self.assertEqual((self.src / "added.txt").read_text(), "second\n")
            self.assertEqual((self.src / "user-notes.txt").read_text(), "keep this scratch file\n")


if __name__ == "__main__":
    unittest.main()
