"""Initialize a pinned checkout without overwriting an agent's later work."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


def git(*args, cwd=None):
    return subprocess.check_output(
        ["git", *args], cwd=cwd, text=True, stderr=subprocess.PIPE,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"}, timeout=120,
    ).strip()


def prepare(repository, revision, destination):
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("revision must be a full lowercase Git commit SHA")
    if not repository or repository.startswith("-"):
        raise ValueError("repository must be a URL or path, not an option")
    destination = Path(destination).absolute()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink():
        raise ValueError("destination must not be a symlink")
    expected = {"repository": repository, "revision": revision}
    with open(destination.parent / (destination.name + ".lock"), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if destination.exists():
            marker = destination / ".git" / "bcenv-source.json"
            if not marker.is_file() or json.loads(marker.read_text()) != expected:
                raise ValueError("existing workspace has a different or unknown source; choose a new destination")
            if git("remote", "get-url", "origin", cwd=destination) != repository:
                raise ValueError("workspace origin differs from its recorded source")
            git("merge-base", "--is-ancestor", revision, "HEAD", cwd=destination)
            return "existing"  # Preserve commits and uncommitted agent edits.
        temporary = Path(tempfile.mkdtemp(prefix=".bcenv-checkout-", dir=destination.parent))
        try:
            git("init", "--quiet", str(temporary))
            git("remote", "add", "origin", repository, cwd=temporary)
            git("fetch", "--quiet", "--depth=1", "origin", revision, cwd=temporary)
            git("checkout", "--quiet", "--detach", "FETCH_HEAD", cwd=temporary)
            if git("rev-parse", "HEAD", cwd=temporary) != revision:
                raise ValueError("fetched revision does not match requested commit")
            (temporary / ".git" / "bcenv-source.json").write_text(json.dumps(expected) + "\n")
            temporary.rename(destination)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
        return "created"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--destination", default="/var/lib/bcenv/workspace")
    args = parser.parse_args()
    try:
        state = prepare(args.repository, args.revision, args.destination)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, f"workspace setup failed: {error}\n")
    print(json.dumps({"state": state, "revision": args.revision, "destination": args.destination}))


if __name__ == "__main__":
    main()
