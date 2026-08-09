#!/usr/bin/env python3
import argparse
import getpass
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
from pathlib import Path

CONFIG = Path(os.environ.get("OPEN_CLOUD_CONFIG", str(Path.home() / ".opencloud/config.env")))
STATE = Path(os.environ.get("OPEN_CLOUD_CHANNELS_STATE", str(Path.home() / ".opencloud/channels.json")))

CHANNELS = ["telegram", "discord", "browser", "cli", "imessage", "advanced"]


def read_env():
    result = {}
    if not CONFIG.exists():
        return result
    for raw in CONFIG.read_text().splitlines():
        if not raw or raw.lstrip().startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        result[key.strip()] = value
    return result


def update_env(updates):
    for value in updates.values():
        if "\n" in str(value) or "\r" in str(value):
            raise SystemExit("ERROR: invalid newline in configuration value")

    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(CONFIG.parent, 0o700)

    lines = CONFIG.read_text().splitlines() if CONFIG.exists() else []
    output = []
    seen = set()

    for line in lines:
        if "=" in line and not line.lstrip().startswith("#"):
            key = line.split("=", 1)[0].strip()
            if key in updates:
                output.append(key + "=" + str(updates[key]))
                seen.add(key)
                continue
        output.append(line)

    for key, value in updates.items():
        if key not in seen:
            output.append(key + "=" + str(value))

    fd, tmp = tempfile.mkstemp(prefix="config.env.", dir=str(CONFIG.parent))
    os.close(fd)
    tp = Path(tmp)
    tp.write_text("\n".join(output).rstrip() + "\n")
    os.chmod(tp, 0o600)
    os.replace(tp, CONFIG)


def load_state():
    if not STATE.exists():
        return None
    data = json.loads(STATE.read_text())
    if not isinstance(data, dict):
        raise SystemExit("ERROR: invalid channels state")
    return data


def save_state(selected, deferred=False):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(STATE.parent, 0o700)
    data = {
        "version": 1,
        "selected": sorted(set(selected)),
        "deferred": bool(deferred),
    }
    fd, tmp = tempfile.mkstemp(prefix="channels.", dir=str(STATE.parent))
    os.close(fd)
    tp = Path(tmp)
    tp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    os.chmod(tp, 0o600)
    os.replace(tp, STATE)


def normalize(text):
    aliases = {
        "1": "telegram",
        "2": "discord",
        "3": "browser",
        "4": "cli",
        "5": "imessage",
        "6": "advanced",
        "telegram": "telegram",
        "discord": "discord",
        "browser": "browser",
        "web": "browser",
        "cli": "cli",
        "imessage": "imessage",
        "apple": "imessage",
        "advanced": "advanced",
    }
    selected = []
    for raw in re.split(r"[, ]+", text.strip().lower()):
        if not raw:
            continue
        if raw == "7" or raw == "later":
            return [], True
        if raw not in aliases:
            raise SystemExit("ERROR: unknown channel selection: " + raw)
        selected.append(aliases[raw])
    return sorted(set(selected)), False


def telegram_ready(env):
    token = env.get("TELEGRAM_BOT_TOKEN", "").strip()
    users = env.get("TELEGRAM_ALLOWED_USERS", "").replace(" ", "")
    token_ok = bool(re.fullmatch(r"\d+:[A-Za-z0-9_-]{30,}", token))
    users_ok = bool(users) and all(x.isdigit() for x in users.split(",") if x)
    return token_ok and users_ok


def discord_ready(env):
    return bool(env.get("DISCORD_BOT_TOKEN", "").strip())


def browser_ready(env):
    enabled = env.get("API_SERVER_ENABLED", "").lower() == "true"
    host = env.get("API_SERVER_HOST", "")
    key = env.get("API_SERVER_KEY", "")
    port = env.get("API_SERVER_PORT", "")
    safe_host = host in ("127.0.0.1", "localhost", "::1")
    return enabled and safe_host and bool(key) and port.isdigit()


def ensure_browser(env):
    updates = {
        "API_SERVER_ENABLED": "true",
        "API_SERVER_HOST": env.get("API_SERVER_HOST") or "127.0.0.1",
        "API_SERVER_PORT": env.get("API_SERVER_PORT") or "8642",
        "API_SERVER_KEY": env.get("API_SERVER_KEY") or secrets.token_urlsafe(32),
    }
    if updates["API_SERVER_HOST"] not in ("127.0.0.1", "localhost", "::1"):
        updates["API_SERVER_HOST"] = "127.0.0.1"
    update_env(updates)


def configure():
    print("How do you want to talk to your assistant?")
    print()
    print("  1) Telegram              [recommended]")
    print("  2) Discord")
    print("  3) Browser / Open WebUI")
    print("  4) CLI only")
    print("  5) iMessage / Apple      [optional]")
    print("  6) Advanced channels")
    print("  7) Configure later")
    print()
    print("You may choose more than one, for example: 1,2,4")
    print()

    selected, deferred = normalize(input("Selection: "))

    if deferred:
        save_state([], True)
        print("Channel configuration deferred.")
        return 0

    env = read_env()

    if "telegram" in selected:
        token = env.get("TELEGRAM_BOT_TOKEN", "")
        while not re.fullmatch(r"\d+:[A-Za-z0-9_-]{30,}", token.strip()):
            entered = getpass.getpass("Telegram bot token: ").strip()
            if entered:
                token = entered
            if not token:
                print("Telegram requires a bot token.")

        users = env.get("TELEGRAM_ALLOWED_USERS", "").replace(" ", "")
        while not users or not all(x.isdigit() for x in users.split(",") if x):
            users = input("Allowed Telegram user IDs, comma separated: ").replace(" ", "")
            if not users:
                print("Telegram requires an explicit user allowlist.")

        update_env({
            "TELEGRAM_BOT_TOKEN": token.strip(),
            "TELEGRAM_ALLOWED_USERS": users,
        })
        env = read_env()

    if "discord" in selected:
        token = env.get("DISCORD_BOT_TOKEN", "").strip()
        while not token:
            token = getpass.getpass("Discord bot token: ").strip()
            if not token:
                print("Discord requires a bot token.")
        update_env({"DISCORD_BOT_TOKEN": token})
        env = read_env()

    if "browser" in selected:
        ensure_browser(env)
        env = read_env()
        print("Browser API prepared on localhost only.")
        print("The service stage will provide the actual protected runtime.")

    if "imessage" in selected:
        print()
        print("iMessage is optional and requires a compatible Apple or Photon setup.")
        project = input("Photon project ID, or Enter to configure later: ").strip()
        secret = ""
        if project:
            secret = getpass.getpass("Photon project secret: ").strip()
        updates = {}
        if project:
            updates["PHOTON_PROJECT_ID"] = project
        if secret:
            updates["PHOTON_PROJECT_SECRET"] = secret
        if updates:
            update_env(updates)

    save_state(selected, False)

    print()
    print("Channel selection saved.")
    print("Secrets remain local in " + str(CONFIG))
    print("Run: opencloud channels status")
    print("Run: opencloud doctor")

    if "advanced" in selected:
        if not shutil.which("hermes"):
            print("Hermes is unavailable; advanced gateway setup was skipped.")
            return 1
        print()
        print("Launching upstream Hermes gateway setup for advanced channels.")
        return subprocess.run(["hermes", "gateway", "setup"]).returncode

    return 0


def set_channels(value):
    selected, deferred = normalize(value)
    if "browser" in selected:
        ensure_browser(read_env())
    save_state(selected, deferred)
    print("CHANNEL_SELECTION: PASS")
    return 0


def print_status():
    state = load_state()
    env = read_env()

    print("Open Cloud Assistant channels")

    if state is None:
        print("Selection: not configured")
        return 0

    if state.get("deferred"):
        print("Selection: configure later")
        return 0

    selected = state.get("selected", [])
    print("Selected: " + (", ".join(selected) if selected else "none"))

    if "telegram" in selected:
        print("Telegram: " + ("CONFIGURED" if telegram_ready(env) else "INCOMPLETE"))
    if "discord" in selected:
        print("Discord: " + ("CONFIGURED" if discord_ready(env) else "INCOMPLETE"))
    if "browser" in selected:
        print("Browser: " + ("LOCAL CONFIG READY" if browser_ready(env) else "INCOMPLETE"))
    if "cli" in selected:
        print("CLI: " + ("AVAILABLE" if shutil.which("hermes") else "MISSING"))
    if "imessage" in selected:
        print("iMessage: " + ("PHOTON AVAILABLE" if shutil.which("photon") else "OPTIONAL RUNTIME NOT READY"))
    if "advanced" in selected:
        print("Advanced: managed through Hermes gateway setup")

    return 0


def doctor_line(kind, name, detail):
    print((kind + "  %-24s %s") % (name, detail))


def doctor():
    state = load_state()
    env = read_env()
    failures = 0

    if state is None or state.get("deferred"):
        doctor_line("SKIP", "Telegram", "not selected")
        doctor_line("SKIP", "Discord", "not selected")
        doctor_line("SKIP", "Browser API", "not selected")
        doctor_line("SKIP", "CLI channel", "not explicitly selected")
        doctor_line("SKIP", "iMessage", "optional Apple integration")
        return 0

    selected = set(state.get("selected", []))

    if "telegram" in selected:
        if telegram_ready(env):
            doctor_line("PASS", "Telegram", "token and explicit allowlist configured")
        else:
            doctor_line("FAIL", "Telegram", "selected but credential or allowlist is incomplete")
            failures += 1
    else:
        doctor_line("SKIP", "Telegram", "not selected")

    if "discord" in selected:
        if discord_ready(env):
            doctor_line("PASS", "Discord", "bot credential configured")
        else:
            doctor_line("FAIL", "Discord", "selected but bot credential is missing")
            failures += 1
    else:
        doctor_line("SKIP", "Discord", "not selected")

    if "browser" in selected:
        if browser_ready(env):
            doctor_line("PASS", "Browser API", "protected localhost configuration prepared")
        else:
            doctor_line("FAIL", "Browser API", "selected but secure local configuration is incomplete")
            failures += 1
    else:
        doctor_line("SKIP", "Browser API", "not selected")

    if "cli" in selected:
        if shutil.which("hermes"):
            doctor_line("PASS", "CLI channel", "Hermes CLI available")
        else:
            doctor_line("FAIL", "CLI channel", "Hermes CLI missing")
            failures += 1
    else:
        doctor_line("SKIP", "CLI channel", "not explicitly selected")

    if "imessage" in selected:
        if shutil.which("photon"):
            doctor_line("PASS", "iMessage", "optional Photon runtime available")
        else:
            doctor_line("FAIL", "iMessage", "selected but optional Photon runtime is unavailable")
            failures += 1
    else:
        doctor_line("SKIP", "iMessage", "optional Apple integration")

    if "advanced" in selected:
        doctor_line("SKIP", "Advanced channels", "managed through Hermes gateway setup")

    return 1 if failures else 0


def clear():
    if STATE.exists():
        STATE.unlink()
    print("CHANNEL_SELECTION_CLEARED: PASS")
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="command")
    sub.add_parser("configure")
    sub.add_parser("status")
    sub.add_parser("doctor")
    sub.add_parser("clear")
    setter = sub.add_parser("set")
    setter.add_argument("channels")
    args = ap.parse_args()

    if args.command == "configure":
        raise SystemExit(configure())
    if args.command == "status":
        raise SystemExit(print_status())
    if args.command == "doctor":
        raise SystemExit(doctor())
    if args.command == "set":
        raise SystemExit(set_channels(args.channels))
    if args.command == "clear":
        raise SystemExit(clear())

    ap.print_help()


if __name__ == "__main__":
    main()
