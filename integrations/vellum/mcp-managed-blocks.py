# HERMES_VELLUM_CONTEXT_V1_BEGIN

def _hermes_vellum_context_lookup(query: str, max_results: int = 8):
    import json
    import os
    import re
    import subprocess
    from pathlib import Path

    query = str(query or "").strip()[:1000]

    if not query:
        return {
            "found": False,
            "context": "",
            "match_count": 0,
            "reason": "empty_query",
        }

    try:
        limit = int(max_results)
    except Exception:
        limit = 8

    limit = max(1, min(limit, 10))

    home = Path.home()

    possible_cli = [
        home / ".local" / "bin" / "assistant",
        home / ".bun" / "bin" / "assistant",
    ]

    assistant = next(
        (
            str(p)
            for p in possible_cli
            if p.exists()
        ),
        None,
    )

    if assistant is None:
        return {
            "found": False,
            "context": "",
            "match_count": 0,
            "reason": "vellum_cli_missing",
        }

    env = os.environ.copy()

    env["VELLUM_WORKSPACE_DIR"] = str(
        home
        / ".local"
        / "share"
        / "vellum"
        / "assistants"
        / "vellum-core"
        / ".vellum"
        / "workspace"
    )

    def load(surface):
        try:
            p = subprocess.run(
                [
                    assistant,
                    "memory",
                    surface,
                    "list",
                    "--json",
                ],
                text=True,
                capture_output=True,
                timeout=20,
                env=env,
                check=False,
            )
        except Exception:
            return None

        if p.returncode != 0:
            return None

        raw = p.stdout.strip()

        if not raw:
            return None

        try:
            return json.loads(raw)
        except Exception:
            return None


    semantic_keys = {
        "content",
        "text",
        "summary",
        "title",
        "name",
        "value",
        "fact",
        "memory",
        "statement",
        "description",
        "details",
        "body",
        "narrative",
        "note",
    }

    forbidden_key_parts = {
        "embedding",
        "vector",
        "checksum",
        "hash",
        "created_at",
        "updated_at",
        "timestamp",
        "token",
        "credential",
        "secret",
    }

    procedural_phrases = (
        "usage:",
        "options:",
        "--help",
        "assistant memory",
        "assistant tools",
        "tool invocation",
        "registered tool",
        "mcp server",
        "plugin route",
        "command to run",
        "cli command",
        "executiontarget",
        "risklevel",
    )


    def scalar_text(value):
        if value is None:
            return ""

        if isinstance(value, str):
            return value

        if isinstance(value, (int, float, bool)):
            return str(value)

        if isinstance(value, list):
            pieces = []

            for item in value:
                if isinstance(item, (str, int, float, bool)):
                    pieces.append(str(item))

            return " ".join(pieces)

        return ""


    def record_text(record):
        if not isinstance(record, dict):
            return ""

        pieces = []

        for key, value in record.items():
            key_low = str(key).lower()

            if any(
                bad in key_low
                for bad in forbidden_key_parts
            ):
                continue

            if key_low in semantic_keys:
                value_text = scalar_text(value).strip()

                if value_text:
                    pieces.append(value_text)

        return re.sub(
            r"\s+",
            " ",
            " ".join(pieces),
        ).strip()


    def collect_records(value, source, output, depth=0):
        if depth > 6:
            return

        if isinstance(value, dict):
            text = record_text(value)

            if len(text) >= 15:
                low = text.lower()

                procedural_score = sum(
                    phrase in low
                    for phrase in procedural_phrases
                )

                if procedural_score < 2:
                    output.append(
                        (source, text)
                    )

            for child in value.values():
                if isinstance(child, (dict, list)):
                    collect_records(
                        child,
                        source,
                        output,
                        depth + 1,
                    )

        elif isinstance(value, list):
            for child in value:
                collect_records(
                    child,
                    source,
                    output,
                    depth + 1,
                )


    stopwords = {
        "a", "about", "an", "and", "are",
        "as", "at", "based", "be", "by",
        "do", "for", "from", "i", "in",
        "is", "it", "kind", "know", "me",
        "my", "of", "on", "or", "should",
        "that", "the", "to", "user", "what",
        "when", "where", "which", "who",
        "why", "with", "you", "your",
    }

    query_low = query.lower()

    query_tokens = []

    for token in re.findall(
        r"[a-zA-Z0-9][a-zA-Z0-9_+.#-]*",
        query_low,
    ):
        if (
            len(token) >= 2
            and token not in stopwords
            and token not in query_tokens
        ):
            query_tokens.append(token)


    expansion = set()

    if any(
        word in query_low
        for word in (
            "internship",
            "internships",
            "career",
            "job",
            "jobs",
            "role",
            "roles",
        )
    ):
        expansion.update({
            "career",
            "internship",
            "devops",
            "cloud",
            "platform",
            "sre",
            "aws",
            "infrastructure",
            "project",
            "resume",
            "skills",
            "graduation",
            "cpt",
            "opt",
            "authorization",
            "engineering",
        })

    if any(
        word in query_low
        for word in (
            "authorization",
            "visa",
            "cpt",
            "opt",
            "work permit",
        )
    ):
        expansion.update({
            "f-1",
            "cpt",
            "opt",
            "stem",
            "authorization",
            "visa",
            "graduation",
        })

    if any(
        word in query_low
        for word in (
            "prefer",
            "preference",
            "preferences",
            "like",
            "want",
        )
    ):
        expansion.update({
            "prefer",
            "preference",
            "likes",
            "wants",
            "goal",
            "goals",
        })


    candidates = []

    items = load("items")

    if items is not None:
        collect_records(
            items,
            "memory_items",
            candidates,
        )


    def score(source, text):
        low = text.lower()
        points = 0
        direct_matches = 0

        for token in query_tokens:
            if token in low:
                points += 6
                direct_matches += 1

        expansion_matches = 0

        for token in expansion:
            if token in low:
                points += 2
                expansion_matches += 1

        if direct_matches >= 2:
            points += 8

        if expansion_matches >= 3:
            points += 6

        if source == "memory_items":
            points += 5

        if len(text) <= 900:
            points += 2

        low_proc = sum(
            phrase in low
            for phrase in procedural_phrases
        )

        points -= low_proc * 20

        return points


    ranked_items = [
        (
            score(source, text),
            source,
            text,
        )
        for source, text in candidates
    ]

    ranked_items = [
        row
        for row in ranked_items
        if row[0] > 0
    ]

    ranked_items.sort(
        key=lambda row: (
            -row[0],
            len(row[2]),
        )
    )


    # Only consult concept nodes when remembered facts
    # do not already provide enough useful evidence.
    if len(ranked_items) < 4:
        nodes = load("nodes")

        if nodes is not None:
            node_candidates = []

            collect_records(
                nodes,
                "memory_nodes",
                node_candidates,
            )

            for source, text in node_candidates:
                points = score(
                    source,
                    text,
                )

                if points > 0:
                    ranked_items.append(
                        (
                            points,
                            source,
                            text,
                        )
                    )

            ranked_items.sort(
                key=lambda row: (
                    -row[0],
                    len(row[2]),
                )
            )


    selected = []
    seen = set()
    total_chars = 0

    for points, source, text in ranked_items:
        normalized = re.sub(
            r"[^a-z0-9]+",
            " ",
            text.lower(),
        ).strip()

        fingerprint = normalized[:350]

        if (
            not fingerprint
            or fingerprint in seen
        ):
            continue

        seen.add(fingerprint)

        text = text[:1200]

        chunk = (
            "["
            + source
            + "] "
            + text
        )

        if total_chars + len(chunk) > 6000:
            break

        selected.append(chunk)
        total_chars += len(chunk)

        if len(selected) >= limit:
            break


    context = "\n".join(selected).strip()

    return {
        "found": bool(context),
        "context": context,
        "match_count": len(selected),
        "method": "vellum_memory_items_first_v2",
    }



# HERMES_CODE_REPAIR_TOOL_V1_BEGIN

@mcp.tool()
def repair_code(task: str, target: str = "hermes"):
    """
    Repair a real code defect using the restricted local OpenCode mechanic.

    Use this only when Hermes needs an actual Hermes source-code repair.

    New feature development uses a separate workflow and is not handled here.

    Allowed target:
      - hermes

    Do NOT use this for ordinary questions, personal-memory retrieval,
    research, writing, or model selection.

    The repair harness snapshots the target first, blocks secret/external
    access, validates code after edits, and rolls back if OpenCode fails.
    """
    import subprocess
    from pathlib import Path

    task = str(task or "").strip()
    target = str(target or "hermes").strip().lower()

    if target != "hermes":
        return {
            "ok": False,
            "status": "target_denied",
        }

    if not task:
        return {
            "ok": False,
            "status": "empty_task",
        }

    if len(task) > 6000:
        return {
            "ok": False,
            "status": "task_too_large",
        }

    helper = Path("__OPEN_CLOUD_HOME__/.local/bin/hermes-code-repair")

    if not helper.exists():
        return {
            "ok": False,
            "status": "repair_harness_missing",
        }

    try:
        proc = subprocess.run(
            [
                str(helper),
                "--task",
                task,
            ],
            text=True,
            capture_output=True,
            timeout=960,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "status": "timeout",
        }
    except Exception:
        return {
            "ok": False,
            "status": "launch_failed",
        }

    lines = [
        line.strip()
        for line in proc.stdout.splitlines()
        if line.strip()
    ]

    safe = [
        line
        for line in lines
        if line.startswith(
            (
                "REPAIR_STATUS:",
                "REPAIR_TARGET:",
                "RESTART_REQUIRED:",
                "RESTART_SCHEDULED:",
                "PYTHON_FILES_VALIDATED:",
            )
        )
    ]

    return {
        "ok": proc.returncode == 0,
        "status": (
            "completed"
            if proc.returncode == 0
            else "failed"
        ),
        "summary": safe[-12:],
    }

# HERMES_CODE_REPAIR_TOOL_V1_END

@mcp.tool()
def get_user_context(query: str, max_results: int = 8):
    """
    Retrieve relevant PERSONAL CONTEXT ABOUT THE USER from Vellum memory.

    Use this automatically for identity, preferences, career, internships,
    education, work authorization, projects, goals, history, and prior
    personal decisions.

    IMPORTANT: for personal-memory lookup, use THIS tool instead of
    start_vellum_task. Do not start a Vellum conversational task merely
    to retrieve facts about the user.

    The lookup is read-only and does not require an LLM provider.
    """
    return _hermes_vellum_context_lookup(
        query,
        max_results,
    )

# HERMES_VELLUM_CONTEXT_V1_END
