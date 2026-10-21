from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import ci_security as security
from tools import update_security_tools as updater


BASE = "a" * 40
COMMIT = "b" * 40
TREE = "c" * 40


class UpdateFixture(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name)
        (self.repo / "tools").mkdir()
        self.source = (
            '# Preserve unrelated source.\nBETTERLEAKS_VERSION = "1.0.0"\n'
            'BETTERLEAKS_HASHES = {"arm64": "' + "1" * 64 + '", "x64": "' + "2" * 64 + '"}\n'
            'MINGIT_VERSION = "2.1.0.windows.1"\n'
            'MINGIT_HASHES = {"arm64": "' + "3" * 64 + '", "x64": "' + "4" * 64 + '"}\n'
            'def unchanged():\n    return "keep me"\n'
        )
        (self.repo / updater.SOURCE_PATH).write_text(self.source, encoding="utf-8")
        (self.repo / updater.REQUIREMENTS_PATH).write_text("# Security tools\npip-audit==1.0.0\n", encoding="utf-8")
        self.current = updater.current_pins(self.repo)
        self.updates = {
            "BETTERLEAKS": {"version": "1.1.0", "hashes": {"arm64": "5" * 64, "x64": "6" * 64}},
            "MINGIT": {"version": "2.2.0.windows.1", "hashes": {"arm64": "7" * 64, "x64": "8" * 64}},
            "PIP_AUDIT": {"version": "1.1.0"},
        }

    def release(self, tool: str, pin: dict) -> dict:
        return {
            "tag_name": "v" + pin["version"], "draft": False, "prerelease": False,
            "assets": [
                {
                    "name": updater.asset_name(tool, pin["version"], architecture),
                    "digest": "sha256:" + digest,
                    "browser_download_url": updater.asset_url(tool, pin["version"], architecture),
                }
                for architecture, digest in pin["hashes"].items()
            ],
        }

    def discovery(self, pins: dict, mutate=None) -> dict:
        releases = {
            f"repos/{upstream}/releases/latest": self.release(tool, pins[tool])
            for tool, upstream in updater.NATIVE_TOOLS.items()
        }
        package = {"info": {"name": "pip-audit", "version": pins["PIP_AUDIT"]["version"]},
                   "urls": [{"packagetype": "bdist_wheel", "yanked": False}]}
        if mutate:
            mutate(releases, package)
        with patch.object(updater, "github", side_effect=lambda path: releases[path]), patch(
            "urllib.request.urlopen", return_value=io.BytesIO(json.dumps(package).encode("utf-8"))
        ):
            return updater.discover(self.repo)


class PinUpdateTests(UpdateFixture):
    def test_updates_are_exact_reviewable_changes_and_do_not_modify_checkout(self) -> None:
        files = updater.render_updates(self.repo, self.updates)
        self.assertEqual(set(files), {updater.SOURCE_PATH, updater.REQUIREMENTS_PATH})
        self.assertIn('BETTERLEAKS_VERSION = "1.1.0"', files[updater.SOURCE_PATH])
        self.assertIn('MINGIT_VERSION = "2.2.0.windows.1"', files[updater.SOURCE_PATH])
        self.assertIn('return "keep me"', files[updater.SOURCE_PATH])
        self.assertEqual(files[updater.REQUIREMENTS_PATH], "# Security tools\npip-audit==1.1.0\n")
        self.assertEqual((self.repo / updater.SOURCE_PATH).read_text(encoding="utf-8"), self.source)
        for name, content in files.items():
            (self.repo / name).write_text(content, encoding="utf-8")
        self.assertEqual(updater.current_pins(self.repo), self.updates)
        self.assertEqual(updater.render_updates(self.repo, self.updates), {})

    def test_auditor_only_update_does_not_change_scanner_source(self) -> None:
        files = updater.render_updates(self.repo, {"PIP_AUDIT": self.updates["PIP_AUDIT"]})
        self.assertEqual(set(files), {updater.REQUIREMENTS_PATH})
        self.assertEqual(security.pip_audit_version(files[updater.REQUIREMENTS_PATH]), "1.1.0")

    def test_downgrades_mutated_releases_and_invalid_plans_fail_closed(self) -> None:
        cases = [
            {"OTHER": {"version": "1.2.3"}},
            {"PIP_AUDIT": {"version": "0.9.0"}},
            {"PIP_AUDIT": {"version": "1.1.0rc1"}},
            {"PIP_AUDIT": {"version": "1.1.0", "extra": "unexpected"}},
            {"BETTERLEAKS": {"version": "1.1.0", "hashes": {"x64": "5" * 64}}},
            {"BETTERLEAKS": {"version": "1.0.0", "hashes": self.updates["BETTERLEAKS"]["hashes"]}},
            {"MINGIT": {"version": "2.2.0.windows.1", "hashes": {"arm64": "bad", "x64": "5" * 64}}},
            {"PIP_AUDIT": {"version": '1.1.0"; run_untrusted_code()'}},
            [],
        ]
        for updates in cases:
            with self.subTest(updates=updates), self.assertRaises(security.SecurityError):
                updater.render_updates(self.repo, updates)
        self.assertEqual((self.repo / updater.SOURCE_PATH).read_text(encoding="utf-8"), self.source)

    def test_ambiguous_source_pin_is_not_rewritten(self) -> None:
        (self.repo / updater.SOURCE_PATH).write_text(
            self.source + '\nBETTERLEAKS_VERSION = "1.0.0"\n', encoding="utf-8",
        )
        with self.assertRaisesRegex(security.SecurityError, "exactly one"):
            updater.render_updates(self.repo, self.updates)

    def test_discovery_verifies_every_changed_native_archive_without_installing(self) -> None:
        with patch.object(security, "download_release") as download:
            self.assertEqual(self.discovery(self.updates), self.updates)
        self.assertEqual(download.call_count, 4)
        for tool in updater.NATIVE_TOOLS:
            for architecture, digest in self.updates[tool]["hashes"].items():
                download.assert_any_call(updater.asset_url(tool, self.updates[tool]["version"], architecture), digest, tool)
        self.assertEqual(updater.current_pins(self.repo), self.current)

    def test_current_releases_are_idempotent_and_do_not_download_binaries(self) -> None:
        with patch.object(security, "download_release") as download:
            self.assertEqual(self.discovery(self.current), {})
        download.assert_not_called()

    def test_bad_release_metadata_and_yanked_wheels_never_create_partial_updates(self) -> None:
        path = "repos/betterleaks/betterleaks/releases/latest"
        mutations = [
            lambda releases, package: releases[path].update(prerelease=True),
            lambda releases, package: releases[path].update(draft=True),
            lambda releases, package: releases[path].update(tag_name="v1.1.0-rc1"),
            lambda releases, package: releases[path]["assets"].pop(),
            lambda releases, package: releases[path]["assets"].append(releases[path]["assets"][0]),
            lambda releases, package: releases[path]["assets"][0].update(digest=None),
            lambda releases, package: releases[path]["assets"][0].update(browser_download_url="https://example.invalid/tool"),
            lambda releases, package: package["urls"][0].update(yanked=True),
            lambda releases, package: package["info"].update(version="1.1.0rc1"),
            lambda releases, package: package["info"].update(version="0.9.0"),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate), patch.object(security, "download_release") as download:
                with self.assertRaises(security.SecurityError):
                    self.discovery(self.updates, mutate)
            download.assert_not_called()
        self.assertEqual(updater.current_pins(self.repo), self.current)

    def test_download_verification_failure_leaves_all_pins_unchanged(self) -> None:
        with patch.object(security, "download_release", side_effect=security.SecurityError("checksum mismatch")):
            with self.assertRaisesRegex(security.SecurityError, "checksum mismatch"):
                self.discovery(self.updates)
        self.assertEqual(updater.current_pins(self.repo), self.current)

    def test_github_errors_and_malformed_json_are_visible_failures(self) -> None:
        for result in (subprocess.CompletedProcess([], 1, "", "permission denied"),
                       subprocess.CompletedProcess([], 0, "not json", ""),
                       subprocess.CompletedProcess([], 0, "null", "")):
            with self.subTest(result=result), patch.object(security, "run", return_value=result):
                with self.assertRaises(security.SecurityError):
                    updater.github("repos/fixture/example/releases/latest")


class PublicationTests(UpdateFixture):
    def setUp(self) -> None:
        super().setUp()
        environment = patch.dict(os.environ, {
            "GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": "fixture/example",
            "GITHUB_REF": "refs/heads/main", "GITHUB_EVENT_NAME": "schedule",
        })
        environment.start()
        self.addCleanup(environment.stop)
        git = patch.object(security, "git", side_effect=lambda repo, *args: "" if args[0] == "status" else BASE)
        git.start()
        self.addCleanup(git.stop)
        self.calls = []
        self.open_pulls = []
        self.open_files = []
        self.previous_pulls = []
        self.existing_commit = None
        self.branch = None
        self.fail_pr_creation = False
        self.default_sha = BASE

    def api(self, path: str, data: dict | None = None):
        self.calls.append((path, copy.deepcopy(data)))
        prefix = "repos/fixture/example"
        if path == prefix:
            return {"default_branch": "main"}
        if path == prefix + "/git/ref/heads/main":
            return {"object": {"sha": self.default_sha}}
        if "/pulls?state=open" in path:
            return self.open_pulls
        if "/pulls/2/files?" in path:
            return self.open_files
        if "/pulls?state=all" in path:
            return self.previous_pulls
        if path == prefix + "/git/commits/" + BASE:
            return {"tree": {"sha": "d" * 40}}
        if path == prefix + "/git/trees":
            return {"sha": TREE}
        if "/git/matching-refs/heads/" in path:
            self.branch = path.split("/heads/", 1)[1]
            return [{"ref": "refs/heads/" + self.branch, "object": {"sha": COMMIT}}] if self.existing_commit else []
        if path == prefix + "/git/commits/" + COMMIT:
            return self.existing_commit
        if path == prefix + "/git/commits":
            return {"sha": COMMIT}
        if path == prefix + "/git/refs":
            self.existing_commit = {"tree": {"sha": TREE}, "parents": [{"sha": BASE}]}
            return {"ref": data["ref"]}
        if path == prefix + "/pulls" and data:
            if self.fail_pr_creation:
                raise security.SecurityError("PR service unavailable")
            return {"html_url": "https://github.com/fixture/example/pull/1"}
        self.fail(f"Unexpected API operation: {path}, {data}")

    def publish(self):
        with patch.object(updater, "github", side_effect=self.api), contextlib.redirect_stdout(io.StringIO()):
            return updater.publish(self.repo, self.updates, BASE)

    def test_only_pins_are_published_to_a_new_branch_and_draft_pr(self) -> None:
        self.assertEqual(self.publish(), "https://github.com/fixture/example/pull/1")
        writes = [(path, data) for path, data in self.calls if data is not None]
        self.assertEqual([path.rsplit("/", 1)[-1] for path, _ in writes], ["trees", "commits", "refs", "pulls"])
        tree, commit, ref, pull = [data for _, data in writes]
        self.assertEqual({entry["path"] for entry in tree["tree"]}, {updater.SOURCE_PATH, updater.REQUIREMENTS_PATH})
        self.assertEqual(commit["parents"], [BASE])
        self.assertTrue(ref["ref"].startswith("refs/heads/" + updater.BRANCH_PREFIX))
        self.assertNotIn("force", ref)
        self.assertEqual(pull["base"], "main")
        self.assertIs(pull["draft"], True)
        self.assertIn("Approve the workflow runs", pull["body"])
        self.assertEqual(updater.current_pins(self.repo), self.current)

    def test_untrusted_events_and_non_default_branches_cannot_publish(self) -> None:
        for changes in ({"GITHUB_ACTIONS": "false"}, {"GITHUB_EVENT_NAME": "pull_request"},
                        {"GITHUB_REF": "refs/heads/contributor"}, {"GITHUB_REPOSITORY": "../invalid"}):
            self.calls.clear()
            with self.subTest(changes=changes), patch.dict(os.environ, changes):
                with self.assertRaises(security.SecurityError):
                    self.publish()
            self.assertFalse(any(data is not None for _, data in self.calls))

    def test_changed_default_branch_or_dirty_checkout_cannot_publish(self) -> None:
        self.default_sha = COMMIT
        with self.assertRaisesRegex(security.SecurityError, "changed after discovery"):
            self.publish()
        self.default_sha = BASE
        with patch.object(security, "git", return_value=" M contributor-file"):
            with self.assertRaisesRegex(security.SecurityError, "clean checkout"):
                self.publish()
        self.assertFalse(any(data is not None for _, data in self.calls))

    def test_open_upgrade_pr_and_previously_closed_proposal_are_left_alone(self) -> None:
        self.open_pulls = [{"head": {"ref": updater.BRANCH_PREFIX + "existing"}, "html_url": "existing PR"}]
        self.assertIsNone(self.publish())
        self.open_pulls = []
        self.previous_pulls = [{"html_url": "closed proposal"}]
        self.assertIsNone(self.publish())
        self.assertFalse(any(data is not None for _, data in self.calls))

    def test_failed_pr_creation_resumes_without_another_commit_or_branch_write(self) -> None:
        self.fail_pr_creation = True
        with self.assertRaisesRegex(security.SecurityError, "PR service unavailable"):
            self.publish()
        self.calls.clear()
        self.fail_pr_creation = False
        self.assertIsNotNone(self.publish())
        writes = [path.rsplit("/", 1)[-1] for path, data in self.calls if data is not None]
        self.assertEqual(writes, ["trees", "pulls"])

    def test_overlapping_dependabot_or_contributor_pr_is_not_duplicated(self) -> None:
        for branch in ("dependabot/pip/pip-audit-1.1.0", "contributor/update-scanner"):
            self.open_pulls = [{"number": 2, "head": {"ref": branch}, "html_url": "overlapping PR"}]
            self.open_files = [{"filename": updater.REQUIREMENTS_PATH}]
            with self.subTest(branch=branch):
                self.assertIsNone(self.publish())
        self.assertFalse(any(data is not None for _, data in self.calls))
        self.open_files = [{"filename": "README.md"}]
        self.assertIsNotNone(self.publish())

    def test_existing_branch_with_contributor_changes_is_never_overwritten(self) -> None:
        self.existing_commit = {"tree": {"sha": "e" * 40}, "parents": [{"sha": BASE}]}
        with self.assertRaisesRegex(security.SecurityError, "refusing to overwrite"):
            self.publish()
        self.assertEqual([path.rsplit("/", 1)[-1] for path, data in self.calls if data is not None], ["trees"])


class WorkflowTests(unittest.TestCase):
    def test_paginated_pr_files_are_all_inspected(self) -> None:
        first = [{"filename": f"unrelated-{index}"} for index in range(100)]
        second = [{"filename": updater.REQUIREMENTS_PATH}]
        with patch.object(updater, "github", side_effect=[first, second]) as api:
            files = list(updater.github_pages("repos/fixture/example/pulls/1/files"))
        self.assertEqual(len(files), 101)
        self.assertEqual(files[-1], second[0])
        self.assertTrue(api.call_args.args[0].endswith("page=2"))

    def test_discovery_failure_does_not_publish_success_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            with patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}), patch(
                "sys.argv", ["update_security_tools.py", "check"]
            ), patch.object(updater, "discover", side_effect=security.SecurityError("metadata failure")), contextlib.redirect_stderr(io.StringIO()) as error:
                self.assertEqual(updater.main(), 1)
            self.assertFalse(output.exists())
            self.assertIn("metadata failure", error.getvalue())

    def test_discovery_outputs_a_noop_plan_for_current_tools(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            with patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}), patch(
                "sys.argv", ["update_security_tools.py", "check"]
            ), patch.object(updater, "discover", return_value={}), patch.object(
                security, "git", return_value=BASE
            ), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(updater.main(), 0)
            self.assertEqual(output.read_text(encoding="utf-8"), f"base={BASE}\nupdates={{}}\n")

    def test_workflow_limits_write_access_and_does_not_run_on_pr_events(self) -> None:
        workflow = (security.ROOT / ".github" / "workflows" / "security-tool-updates.yml").read_text(encoding="utf-8")
        discovery, publication = workflow.split("\n  publish:\n")
        self.assertIn('cron: "23 8 * * 1"', discovery)
        self.assertIn("workflow_dispatch:", discovery)
        self.assertNotIn("pull_request:", workflow)
        self.assertIn("permissions:\n  contents: read", discovery)
        self.assertNotIn("contents: write", discovery)
        self.assertIn("github.event.repository.default_branch", discovery)
        self.assertIn("needs: discover", publication)
        self.assertIn("contents: write\n      pull-requests: write", publication)
        self.assertIn("ref: ${{ needs.discover.outputs.base }}", publication)
        self.assertNotIn("secrets.", workflow)
        self.assertEqual(workflow.count("persist-credentials: false"), 2)
        self.assertNotIn("approve", publication)
        self.assertNotIn("--merge", publication)


if __name__ == "__main__":
    unittest.main()
