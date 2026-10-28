"""Create the explicitly selected source repository using Git's saved login.

The credential remains in memory, is never printed, and is not stored here.
This helper does not commit, push, create releases, or change account settings.
"""
import argparse
import json
import os
import re
import subprocess
import urllib.error
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("repository", help="Explicit owner/name")
    parser.add_argument("--create-public", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", args.repository):
        raise SystemExit("Invalid repository name")
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")
    credential = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n",
                                text=True, capture_output=True, env=env, timeout=30)
    if credential.returncode:
        raise SystemExit("Git credential lookup failed; no repository was created.")
    values = dict(line.split("=", 1) for line in credential.stdout.splitlines() if "=" in line)
    token = values.get("password")
    if not token:
        raise SystemExit("No saved GitHub login; no repository was created.")

    def request(path, body=None):
        req = urllib.request.Request("https://api.github.com" + path,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "Astrspire-source-publication",
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.load(response)

    try:
        account = request("/user")
        owner, name = args.repository.split("/", 1)
        if account.get("login", "").lower() != owner.lower():
            raise SystemExit("Saved GitHub login does not own the requested repository.")
        try:
            repository = request("/repos/" + args.repository)
        except urllib.error.HTTPError as exc:
            if exc.code != 404 or not args.create_public:
                raise
            repository = request("/user/repos", {"name": name, "private": False,
                "auto_init": False, "description": "STC ISP, serial debugging and telemetry workbench (PySide6 / PyQtGraph)"})
        if repository.get("full_name", "").lower() != args.repository.lower() or repository.get("private"):
            raise SystemExit("Repository identity or requested public visibility does not match.")
        print(json.dumps({"repository": repository["full_name"], "url": repository["html_url"],
                          "private": repository["private"], "default_branch": repository["default_branch"]}))
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"GitHub API returned HTTP {exc.code}; credential contents were not logged.") from None
    except urllib.error.URLError:
        raise SystemExit("GitHub network request failed; credential contents were not logged.") from None


if __name__ == "__main__":
    main()
