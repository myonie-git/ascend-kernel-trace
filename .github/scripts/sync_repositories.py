"""Bidirectional fast-forward sync; never force-push, delete refs, or merge commits."""

import argparse
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit


class SyncConflict(RuntimeError):
    pass


def git(repo, *args, check=True):
    env = os.environ.copy()
    for key in ("GIT_TRACE", "GIT_TRACE_CURL", "GIT_CURL_VERBOSE", "GIT_TRACE_PACKET"):
        env.pop(key, None)
    env["GIT_TERMINAL_PROMPT"] = "0"
    result = subprocess.run(["git", "-C", str(repo), *args], env=env, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90)
    if check and result.returncode:
        raise RuntimeError(result.stderr.strip() or f"git {args[0]} failed")
    return result


def credential(action):
    if action != "get":
        return
    request = dict(line.rstrip("\n").split("=", 1) for line in sys.stdin if "=" in line)
    targets = (
        ("SYNC_GITHUB_URL", "github.com", "x-access-token", "GH_SYNC_TOKEN"),
        ("SYNC_GITCODE_URL", "atomgit.com", os.environ.get("GITCODE_USERNAME", ""), "GITCODE_TOKEN"),
    )
    for url_variable, host, username, token_variable in targets:
        target = urlsplit(os.environ.get(url_variable, ""))
        if (target.scheme == "https" and target.netloc == host
                and request.get("protocol") == "https" and request.get("host") == host
                and request.get("path", "").rstrip("/") == target.path.lstrip("/").rstrip("/")):
            token = os.environ.get(token_variable, "")
            if username and token:
                print(f"username={username}\npassword={token}")
            return


def read_refs(repo, name):
    prefix = f"refs/sync/{name}/"
    output = git(repo, "for-each-ref", "--format=%(refname) %(objectname)", prefix).stdout
    return {"refs/" + ref[len(prefix):]: oid
            for ref, oid in (line.split() for line in output.splitlines())}


def ancestor(repo, older, newer):
    result = git(repo, "merge-base", "--is-ancestor", older, newer, check=False)
    if result.returncode not in (0, 1):
        raise RuntimeError(result.stderr.strip() or "Could not compare branch ancestry")
    return result.returncode == 0


def plan_updates(repo, github, gitcode):
    updates = {"github": {}, "gitcode": {}}
    conflicts = []
    for ref in sorted(github.keys() | gitcode.keys()):
        left, right = github.get(ref), gitcode.get(ref)
        if left == right:
            continue
        if left is None:
            updates["github"][ref] = right
        elif right is None:
            updates["gitcode"][ref] = left
        elif ref.startswith("refs/tags/"):
            conflicts.append(f"{ref}: different tag objects")
        elif ancestor(repo, left, right):
            updates["github"][ref] = right
        elif ancestor(repo, right, left):
            updates["gitcode"][ref] = left
        else:
            conflicts.append(f"{ref}: divergent histories")
    if conflicts:
        raise SyncConflict("No pushes performed. Resolve these conflicts first:\n" + "\n".join(conflicts))
    return updates


def synchronize(github_url, gitcode_url, execute=False):
    urls = {"github": github_url, "gitcode": gitcode_url}
    for url in urls.values():
        parsed = urlsplit(url)
        if parsed.username or parsed.password:
            raise ValueError("Credentials must not be embedded in remote URLs")
    with tempfile.TemporaryDirectory(prefix="repository-sync-") as directory:
        repo = Path(directory)
        git(repo, "init", "--bare", "--quiet")
        git(repo, "config", "--add", "credential.helper", "")
        helper = "!" + shlex.join([sys.executable, str(Path(__file__).resolve()), "--credential"])
        git(repo, "config", "--add", "credential.helper", helper)
        git(repo, "config", "credential.useHttpPath", "true")
        git(repo, "config", "http.followRedirects", "false")
        refs = {}
        for name, url in urls.items():
            git(repo, "fetch", "--no-tags", url,
                f"refs/heads/*:refs/sync/{name}/heads/*",
                f"refs/tags/*:refs/sync/{name}/tags/*")
            refs[name] = read_refs(repo, name)
        updates = plan_updates(repo, refs["github"], refs["gitcode"])
        for name, changes in updates.items():
            for ref, oid in changes.items():
                print(f"{name}: {ref} -> {oid}")
        if not any(updates.values()):
            print("Both repositories are already synchronized.")
        elif execute:
            for name, changes in updates.items():
                if changes:
                    # Ordinary atomic pushes reject concurrent non-fast-forward changes.
                    git(repo, "push", "--atomic", urls[name],
                        *(f"{oid}:{ref}" for ref, oid in changes.items()))
            print("Synchronization completed.")
        else:
            print("Dry run: no pushes performed.")
        return updates


def main():
    if len(sys.argv) > 2 and sys.argv[1] == "--credential":
        credential(sys.argv[2])
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--github-url", default=os.environ.get("SYNC_GITHUB_URL"))
    parser.add_argument("--gitcode-url", default=os.environ.get("SYNC_GITCODE_URL"))
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.github_url or not args.gitcode_url:
        parser.error("Both remote URLs are required")
    try:
        synchronize(args.github_url, args.gitcode_url, args.execute)
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
