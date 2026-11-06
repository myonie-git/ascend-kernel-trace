"""Use isolated local Git repositories; never contact the hosted repositories."""

import contextlib
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import sync_repositories as sync


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.github = self.root / "github.git"
        self.gitcode = self.root / "gitcode.git"
        self.work = self.root / "work"
        for repo in (self.github, self.gitcode, self.work):
            repo.mkdir()
            self.git(repo, "init", "--bare", "--quiet")
        self.git(self.work, "config", "user.name", "Sync Test")
        self.git(self.work, "config", "user.email", "sync-test@example.invalid")
        tree = self.git(self.work, "mktree", data="")
        self.base = self.git(self.work, "commit-tree", tree, "-m", "base")
        for repo in (self.github, self.gitcode):
            self.git(self.work, "push", str(repo), f"{self.base}:refs/heads/main")

    def git(self, repo, *args, data=None):
        return subprocess.run(["git", "-C", str(repo), *args], input=data, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              check=True).stdout.strip()

    def commit(self, message):
        tree = self.git(self.work, "rev-parse", f"{self.base}^{{tree}}")
        return self.git(self.work, "commit-tree", tree, "-p", self.base, "-m", message)

    def push(self, target, oid, ref="refs/heads/main"):
        self.git(self.work, "push", str(target), f"{oid}:{ref}")

    def refs(self, repo):
        return self.git(repo, "for-each-ref", "--format=%(refname) %(objectname)")

    def run_sync(self, execute=True):
        with contextlib.redirect_stdout(io.StringIO()):
            return sync.synchronize(str(self.github), str(self.gitcode), execute)

    def test_equal_is_noop(self):
        self.assertEqual(self.run_sync(), {"github": {}, "gitcode": {}})

    def test_fast_forward_each_direction(self):
        for source, target in ((self.github, self.gitcode), (self.gitcode, self.github)):
            self.base = self.git(source, "rev-parse", "refs/heads/main")
            oid = self.commit(str(source))
            self.push(source, oid)
            self.run_sync()
            self.assertEqual(self.git(target, "rev-parse", "refs/heads/main"), oid)

    def test_divergence_stops_all_updates(self):
        left, right = self.commit("left"), self.commit("right")
        self.push(self.github, left)
        self.push(self.gitcode, right)
        self.push(self.github, left, "refs/heads/unrelated-new-branch")
        before = (self.refs(self.github), self.refs(self.gitcode))
        with self.assertRaises(sync.SyncConflict):
            self.run_sync()
        self.assertEqual(before, (self.refs(self.github), self.refs(self.gitcode)))

    def test_different_tags_stop_before_any_push(self):
        self.push(self.github, self.base, "refs/tags/v1")
        self.push(self.gitcode, self.commit("other tag target"), "refs/tags/v1")
        before = (self.refs(self.github), self.refs(self.gitcode))
        with self.assertRaises(sync.SyncConflict):
            self.run_sync()
        self.assertEqual(before, (self.refs(self.github), self.refs(self.gitcode)))

    def test_new_refs_are_copied_both_ways_but_not_internal_refs(self):
        self.push(self.github, self.base, "refs/heads/from-github")
        self.push(self.gitcode, self.base, "refs/heads/from-gitcode")
        self.push(self.gitcode, self.base, "refs/tags/v1")
        self.push(self.github, self.base, "refs/pull/1/head")
        self.run_sync()
        for repo in (self.github, self.gitcode):
            for ref in ("refs/heads/from-github", "refs/heads/from-gitcode", "refs/tags/v1"):
                self.assertEqual(self.git(repo, "rev-parse", ref), self.base)
        self.assertNotIn("refs/pull/", self.refs(self.gitcode))

    def test_one_sided_deletion_is_recreated_not_propagated(self):
        self.git(self.gitcode, "update-ref", "-d", "refs/heads/main")
        self.run_sync()
        self.assertEqual(self.git(self.gitcode, "rev-parse", "refs/heads/main"), self.base)

    def test_dry_run_does_not_push(self):
        self.push(self.github, self.commit("advance"))
        before = self.refs(self.gitcode)
        self.assertTrue(self.run_sync(execute=False)["gitcode"])
        self.assertEqual(before, self.refs(self.gitcode))

    def test_credentials_are_limited_to_the_configured_repository(self):
        env = {"SYNC_GITHUB_URL": "https://github.com/example/project.git", "GH_SYNC_TOKEN": "fake-secret"}
        for path, allowed in (("example/project.git", True), ("example/other.git", False)):
            output = io.StringIO()
            request = io.StringIO(f"protocol=https\nhost=github.com\npath={path}\n\n")
            with mock.patch.dict(os.environ, env), mock.patch("sys.stdin", request), contextlib.redirect_stdout(output):
                sync.credential("get")
            self.assertEqual("fake-secret" in output.getvalue(), allowed)


if __name__ == "__main__":
    unittest.main()
