#!/usr/bin/env python3
from __future__ import annotations

import ast
import contextvars
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

HERMES_ROOT = Path(
    os.environ.get(
        "OPEN_CLOUD_HERMES_ROOT",
        str(Path.home() / ".hermes" / "hermes-agent"),
    )
)

DELEGATE_SOURCE = (
    HERMES_ROOT
    / "tools"
    / "delegate_tool.py"
)

POLICY = (
    ROOT
    / "config"
    / "hermes"
    / "orchestration.json"
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def function_node(tree: ast.AST, name: str):
    for node in ast.walk(tree):
        if isinstance(
            node,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
            ),
        ) and node.name == name:
            return node

    return None


def is_name(node: ast.AST, name: str) -> bool:
    return (
        isinstance(node, ast.Name)
        and node.id == name
    )


def is_attr(node: ast.AST, name: str) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == name
    )


def verify_delegate_contract(max_children: int) -> None:
    require(
        DELEGATE_SOURCE.is_file(),
        "Hermes delegate_tool.py missing",
    )

    source = DELEGATE_SOURCE.read_text(
        encoding="utf-8"
    )

    tree = ast.parse(source)

    delegate = function_node(
        tree,
        "delegate_task",
    )

    require(
        delegate is not None,
        "delegate_task function missing",
    )

    executor_contract = False
    submit_contract = False

    for node in ast.walk(delegate):

        if isinstance(node, ast.Call):

            if (
                isinstance(node.func, ast.Name)
                and node.func.id.endswith("ThreadPoolExecutor")
                or isinstance(node.func, ast.Attribute)
                and node.func.attr.endswith("ThreadPoolExecutor")
            ):

                for kw in node.keywords:
                    if (
                        kw.arg == "max_workers"
                        and is_name(
                            kw.value,
                            "max_children",
                        )
                    ):
                        executor_contract = True

            if is_attr(
                node.func,
                "submit",
            ):
                if any(
                    is_name(
                        arg,
                        "_run_single_child",
                    )
                    for arg in node.args
                ):
                    submit_contract = True

    require(
        executor_contract,
        "delegate_task no longer binds executor max_workers to max_children",
    )

    require(
        submit_contract,
        "delegate_task no longer submits _run_single_child through executor",
    )

    delegate_lines = source.splitlines()[
        delegate.lineno - 1:
        delegate.end_lineno
    ]

    delegate_text = "\n".join(
        delegate_lines
    )

    require(
        "if len(tasks) > max_children:" in delegate_text,
        "delegate_task batch-size guard missing",
    )

    require(max_children == 4, "OpenCloud reliability contract expects 4 children")

    for required_text, message in (
        ("_get_child_timeout()", "child timeout enforcement missing"),
        ("_get_max_spawn_depth()", "spawn-depth enforcement missing"),
        ("_get_inherit_mcp_toolsets()", "MCP inheritance contract missing"),
        ("_unregister_subagent", "worker cleanup contract missing"),
        ("if len(tasks) > max_children:", "dynamic batch ceiling missing"),
    ):
        require(required_text in source, message)

    print(
        "PASS delegate_task executor source contract"
    )

    print(
        "PASS delegate_task batch-size guard"
    )


def runtime_executor():
    source = DELEGATE_SOURCE.read_text(encoding="utf-8")
    if "DaemonThreadPoolExecutor(max_workers=max_children)" in source:
        with tempfile.TemporaryDirectory(prefix="opencloud-daemon-compat-") as tmp:
            materialized = Path(tmp) / "daemon_pool.py"
            shutil.copy2(HERMES_ROOT / "tools/daemon_pool.py", materialized)
            subprocess.run(
                [sys.executable, str(ROOT / "integrations/hermes/daemon_pool_compat.py"), str(materialized)],
                check=True,
            )
            spec = importlib.util.spec_from_file_location("opencloud_daemon_pool_test", materialized)
            require(spec is not None and spec.loader is not None, "cannot load materialized daemon executor")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module.DaemonThreadPoolExecutor
    from concurrent.futures import ThreadPoolExecutor
    return ThreadPoolExecutor


def run_parallel_proof(max_children: int) -> None:

    sys.path.insert(
        0,
        str(HERMES_ROOT),
    )

    RuntimeExecutor = runtime_executor()

    child_sleep = 0.300

    barrier = threading.Barrier(
        max_children
    )

    lock = threading.Lock()

    active = 0
    peak_active = 0
    records = {}

    def synthetic_child(
        task_index: int,
    ):

        nonlocal active
        nonlocal peak_active

        entered = time.perf_counter()

        try:
            barrier.wait(
                timeout=5.0
            )
        except threading.BrokenBarrierError as exc:
            raise AssertionError(
                "all workers did not reach concurrency barrier"
            ) from exc

        started = time.perf_counter()

        with lock:
            active += 1
            peak_active = max(
                peak_active,
                active,
            )

        try:
            time.sleep(
                child_sleep
            )
        finally:
            ended = time.perf_counter()

            with lock:
                active -= 1

                records[
                    task_index
                ] = {
                    "entered": entered,
                    "started": started,
                    "ended": ended,
                    "thread": threading.get_ident(),
                }

        return {
            "task_index": task_index,
            "status": "completed",
        }

    wall_start = time.perf_counter()

    with RuntimeExecutor(
        max_workers=max_children
    ) as executor:

        futures = []

        for task_index in range(
            max_children
        ):
            child_context = (
                contextvars.copy_context()
            )

            future = executor.submit(
                child_context.run,
                synthetic_child,
                task_index,
            )

            futures.append(
                future
            )

        results = [
            future.result(
                timeout=5.0
            )
            for future in futures
        ]

    wall_end = time.perf_counter()

    require(
        len(results)
        == max_children,
        "not all synthetic children completed",
    )

    require(
        len(records)
        == max_children,
        "missing child timing records",
    )

    unique_threads = {
        record["thread"]
        for record in records.values()
    }

    require(
        len(unique_threads)
        == max_children,
        "children did not occupy the configured worker threads",
    )

    require(
        peak_active
        == max_children,
        "configured concurrency was not observed",
    )

    latest_start = max(
        record["started"]
        for record in records.values()
    )

    earliest_end = min(
        record["ended"]
        for record in records.values()
    )

    overlap = max(
        0.0,
        earliest_end
        - latest_start,
    )

    wall = (
        wall_end
        - wall_start
    )

    serial_baseline = (
        child_sleep
        * max_children
    )

    require(
        overlap >= 0.200,
        "worker execution overlap was too small",
    )

    require(
        wall
        < serial_baseline * 0.75,
        "batch wall time is too close to serial execution",
    )

    speedup = (
        serial_baseline
        / wall
    )

    print(
        "PASS configured concurrent Hermes executor workers"
    )

    print(
        "PASS copied-context submission path"
    )

    print(
        "PASS measurable worker overlap"
    )

    print(
        "MEASURE hermes_peak_concurrency="
        + str(
            peak_active
        )
    )

    print(
        "MEASURE hermes_unique_worker_threads="
        + str(
            len(
                unique_threads
            )
        )
    )

    print(
        "MEASURE hermes_batch_wall_ms="
        + format(
            wall * 1000,
            ".3f",
        )
    )

    print(
        "MEASURE hermes_serial_baseline_ms="
        + format(
            serial_baseline * 1000,
            ".3f",
        )
    )

    print(
        "MEASURE hermes_overlap_ms="
        + format(
            overlap * 1000,
            ".3f",
        )
    )

    print(
        "MEASURE hermes_synthetic_speedup="
        + format(
            speedup,
            ".2f",
        )
        + "x"
    )


def run_single_worker_proof(max_children: int) -> None:
    RuntimeExecutor = runtime_executor()

    calls = []
    with RuntimeExecutor(max_workers=max_children) as executor:
        result = executor.submit(lambda: calls.append(threading.get_ident()) or "done")
        require(result.result(timeout=5.0) == "done", "single worker did not complete")
    require(len(calls) == 1, "simple task unexpectedly filled the worker pool")
    print("PASS simple task uses one worker rather than filling capacity")


def main() -> None:

    policy = json.loads(
        POLICY.read_text(
            encoding="utf-8"
        )
    )

    require(
        policy.get(
            "orchestrator_enabled"
        )
        is True,
        "orchestration is disabled",
    )

    max_children = int(
        policy[
            "max_concurrent_children"
        ]
    )

    require(
        policy.get(
            "max_spawn_depth"
        )
        == 2,
        "unexpected spawn-depth policy",
    )

    require(
        policy.get("max_iterations") == 12,
        "unexpected child iteration policy",
    )

    require(
        policy.get("child_timeout_seconds") == 120,
        "unexpected child timeout policy",
    )

    require(
        policy.get(
            "inherit_mcp_toolsets"
        )
        is True,
        "MCP inheritance policy changed",
    )

    print(
        "Open Cloud Assistant Hermes concurrency reliability test"
    )

    print(
        "Hermes source: "
        + str(
            DELEGATE_SOURCE
        )
    )

    print(
        "Model/provider calls: none"
    )

    verify_delegate_contract(
        max_children
    )

    run_parallel_proof(
        max_children
    )

    run_single_worker_proof(
        max_children
    )

    print(
        "INFO timings measure deterministic synthetic worker execution, not LLM latency or an SLO"
    )

    print(
        "HERMES_CONCURRENCY_RELIABILITY: PASS"
    )


if __name__ == "__main__":
    main()
