"""Reject identity and live-cloud metadata anywhere in reachable Git history."""

from __future__ import annotations

import re
import subprocess

EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
LIVE_CLOUD_RUN_URL = re.compile(r"https://[a-z0-9.-]+\.run\.app\b")
LOCAL_HOME = re.compile(r"(?:/" + r"Users/[^/\s]+|[A-Za-z]:\\" + r"Users\\[^\\\s]+)")
LITERAL_GCP_PROJECT = re.compile(
    r"gcloud config set project\s+[\"']?(?!\$|<)[a-z][a-z0-9-]{4,28}[a-z0-9]"
)
LITERAL_ARTIFACT_PROJECT = re.compile(r"\.pkg\.dev/[a-z][a-z0-9-]{4,28}[a-z0-9]/")


def _reachable_patch_text() -> str:
    completed = subprocess.run(
        [
            "git",
            "log",
            "--all",
            "--format=",
            "--patch",
            "--no-ext-diff",
            "--no-textconv",
        ],
        capture_output=True,
        check=True,
    )
    return completed.stdout.decode("utf-8", errors="replace")


def main() -> int:
    history = _reachable_patch_text()
    violations: set[str] = set()

    for match in EMAIL.finditer(history):
        if match.group(1).lower() != "example.com":
            violations.add("email address")
    for match in LIVE_CLOUD_RUN_URL.finditer(history):
        if ".example.run.app" not in match.group(0):
            violations.add("live Cloud Run URL")
    if LOCAL_HOME.search(history):
        violations.add("personal home path")
    if LITERAL_GCP_PROJECT.search(history):
        violations.add("literal GCP project assignment")
    if LITERAL_ARTIFACT_PROJECT.search(history):
        violations.add("literal Artifact Registry project path")

    if violations:
        labels = ", ".join(sorted(violations))
        print(f"repository hygiene check failed: {labels}")
        return 1
    print("repository hygiene check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
