#!/usr/bin/env python3
"""Best-effort publication guard for the Git index; never print matching secret values.

Run after explicitly staging release files. This supplements review, not a guarantee
that arbitrary personal data has been found. Only indexed blobs are inspected.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_NAME = re.compile(
    r"(?i)(^|/)(?:config\.json|state(?:\.[^/]*)?\.json|[^/]*(?:storage|cookies?|sessions?)[^/]*\.json|"
    r"[^/]*\.(?:log(?:\.[0-9]+)?|bak|tmp|pem|pfx|p12|key)|\.env(?:\..*)?)$")
TOKEN_PATTERNS = [
    re.compile(rb"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----"),
    re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(rb"\bgithub_pat_[A-Za-z0-9_]{50,}\b"),
    re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
]


def git(*arguments):
    return subprocess.run(["git", "-C", str(ROOT), *arguments], check=True,
                          capture_output=True).stdout


def scan():
    paths = git("ls-files", "-z").decode("utf-8").split("\0")
    failures = []
    count = 0
    for path in filter(None, paths):
        count += 1
        fixture = path.startswith("tests/fixtures/")
        if PRIVATE_NAME.search(path) and not fixture:
            failures.append((path, "private runtime/credential filename"))
        data = git("show", ":" + path)
        if any(pattern.search(data) for pattern in TOKEN_PATTERNS):
            failures.append((path, "credential-shaped value"))
        if path.endswith(".json") and (path == "config.example.json" or path.startswith("examples/")):
            try:
                cfg = json.loads(data)
            except (ValueError, UnicodeError):
                failures.append((path, "invalid public JSON example"))
                continue
            def walk(value):
                if isinstance(value, dict):
                    for key, item in value.items():
                        if key in ("password", "smtp_auth_code") and item:
                            if not isinstance(item, str) or not item.startswith(("YOUR_", "TO_BE_")):
                                failures.append((path, "non-placeholder example credential"))
                        walk(item)
                elif isinstance(value, list):
                    for item in value:
                        walk(item)
            walk(cfg)
    for path, reason in failures:
        print("BLOCKED: %s (%s)" % (path, reason))
    if failures:
        print("Do not publish. Remove private files/values from the index and history before retrying.")
        return 1
    print("PASS: %d indexed files checked for private filenames and credential patterns." % count)
    print("Also manually review diffs, build archives and Git history; this is not a complete secret scanner.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(scan())
    except subprocess.CalledProcessError:
        print("Cannot inspect the Git index; no publication check completed.")
        raise SystemExit(1)
