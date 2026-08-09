#!/usr/bin/env python3
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]

forbidden_names = {
    ".env",
    "auth.json",
    "credentials.json",
    "token.json",
}
forbidden_suffixes = {
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".sqlite",
    ".sqlite3",
    ".db",
}

patterns = {
    "private-key": re.compile(
        rb"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"
    ),
    "bearer-token": re.compile(
        rb"Bearer [A-Za-z0-9._~+/=-]{20,}"
    ),
    "provider-key": re.compile(
        rb"(?:sk-|nvapi-|AIza|github_pat_|gh[pousr]_)[A-Za-z0-9_-]{20,}"
    ),
    "jwt": re.compile(
        rb"eyJ[A-Za-z0-9_-]{10,}\."
        rb"eyJ[A-Za-z0-9_-]{10,}\."
        rb"[A-Za-z0-9_-]{10,}"
    ),
    "auth-code": re.compile(
        rb"auth_code=[A-Za-z0-9._~%+\-/]{8,}"
    ),
}

path_findings = []
content_findings = []

for path in ROOT.rglob("*"):
    if not path.is_file():
        continue
    if ".git" in path.parts:
        continue

    rel = path.relative_to(ROOT)

    if path.name in forbidden_names or path.suffix.lower() in forbidden_suffixes:
        # .env.example is explicitly safe.
        if str(rel) != ".env.example":
            path_findings.append(str(rel))

    try:
        data = path.read_bytes()
    except OSError:
        continue

    if b"\x00" in data[:8192]:
        continue

    for name, pattern in patterns.items():
        if pattern.search(data):
            content_findings.append((str(rel), name))

if path_findings or content_findings:
    print("PUBLIC_AUDIT: FAIL")

    if path_findings:
        print("Forbidden paths:")
        for item in sorted(path_findings):
            print(f"  {item}")

    if content_findings:
        print("Credential-shaped content:")
        for path, kind in sorted(content_findings):
            print(f"  {path} [{kind}]")

    raise SystemExit(1)

print("PASS: no forbidden secret/runtime paths")
print("PASS: no credential-shaped values")
print("PUBLIC_AUDIT: PASS")
