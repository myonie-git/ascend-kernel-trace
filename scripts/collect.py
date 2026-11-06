#!/usr/bin/env python3
"""Plan by default; --execute runs a bounded simulator capture in a fresh directory."""

import argparse
import datetime
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import signal
import struct
import subprocess
import sys

from inspect_trace import discover_traces, inspect_trace
from probe_env import probe_environment


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def elf_architecture(path):
    with Path(path).open("rb") as handle:
        header = handle.read(20)
    if len(header) < 20 or header[:4] != b"\x7fELF" or header[5] not in (1, 2):
        return None
    machine = struct.unpack("<H" if header[5] == 1 else ">H", header[18:20])[0]
    return {183: "aarch64", 62: "x86_64", 40: "arm", 243: "riscv"}.get(machine)


def fresh_output(value):
    path = Path(value).expanduser()
    if ".." in path.parts:
        raise ValueError("Use an output path without '..' components.")
    path = path.absolute()
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("Use an output path without symlink components.")
    if path.exists():
        raise ValueError(f"Output already exists; choose a fresh directory: {path}")
    if not path.parent.is_dir():
        raise ValueError(f"Create the output parent directory first: {path.parent}")
    return path


def make_plan(args):
    if not re.fullmatch(r"[A-Za-z0-9_]+", args.soc):
        raise ValueError("--soc must be a simulator model name, not a path.")
    if not 1 <= args.timeout_minutes <= 2880 or not 1 <= args.launch_count <= 5000:
        raise ValueError("Timeout must be 1..2880 minutes; launch count must be 1..5000.")
    if args.wall_timeout_seconds <= 0:
        raise ValueError("Wall timeout must be positive.")
    output = fresh_output(args.output)
    workdir = Path(args.workdir).resolve()
    if not workdir.is_dir():
        raise ValueError(f"Working directory does not exist: {workdir}")
    app = list(args.application)
    if app and app[0] == "--":
        app.pop(0)
    if not app:
        raise ValueError("Provide the trusted application after --.")
    executable = (str((workdir / app[0]).resolve()) if "/" in app[0]
                  else shutil.which(app[0]))
    if not executable or not Path(executable).is_file() or not os.access(executable, os.X_OK):
        raise ValueError(f"Application is not executable: {app[0]}")
    app[0] = os.path.abspath(executable)
    arch = elf_architecture(app[0])
    if arch and arch != platform.machine():
        raise ValueError(f"Launcher architecture {arch} differs from host {platform.machine()}.")
    environment = probe_environment(args.tool, args.cann_root, args.soc)
    prefix = environment["selected_simulator_prefix"]
    if not prefix:
        raise ValueError("No compatible simulator CLI found. Run probe_env.py and inspect version/help output.")
    selected = next(tool for tool in environment["tools"] if tool["prefix"] == prefix)
    if not all(selected["simulator_metrics_advertised"].values()):
        raise ValueError("Simulator help does not advertise both required instruction/sync metrics; do not guess flags.")
    artifacts = []
    for path in dict.fromkeys([app[0]] + args.artifact):
        file = Path(path).resolve()
        artifacts.append({"path": str(file), "sha256": sha256(file)})
    argv = prefix + ["simulator", f"--soc-version={args.soc}",
                     f"--kernel-name={args.kernel_name}", f"--launch-count={args.launch_count}",
                     "--aic-metrics=PipeUtilization,ResourceConflictRatio", "--dump=on",
                     f"--timeout={args.timeout_minutes}", f"--output={output / 'profile'}"] + app
    return {
        "schema_version": 1, "status": "planned", "source": "simulator",
        "command_argv": argv, "command_display": shlex.join(argv),
        "working_directory": str(workdir), "output": str(output),
        "kernel_filter": args.kernel_name, "workload_note": args.note,
        "application_elf_architecture": arch, "artifacts": artifacts,
        "environment": environment, "wall_timeout_seconds": args.wall_timeout_seconds,
        "notes": ["No NPU is selected/reserved by this script; the trusted application still executes host code.",
                  "CLI discovery does not prove that this SoC model is installed or supported.",
                  "Hashes cover explicit artifacts only, not all dynamically loaded code or inputs.",
                  "The manifest records command arguments, not the full environment; avoid secrets in arguments."],
    }


def stop_own_process_group(process):
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGINT)
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
    except ProcessLookupError:
        process.wait()


def classify_result(returncode, interrupted, log, traces, inspection_errors, kernel_filter):
    names = re.findall(r"Start profiling on kernel:\s*([^\s]+)", log)
    patterns = kernel_filter.split("|")
    matched = any(fnmatch.fnmatchcase(name, pattern) or name.startswith(pattern)
                  for name in names for pattern in patterns)
    complete_marker = any(marker in log for marker in
                          ("Profiling data parse finished", "Op profiling finish"))
    tool_timeout = ("timeout has reached" in log.lower()
                    or "application will be forcibly killed" in log.lower())
    instruction_data = bool(traces) and all(
        item["granularity"] in ("instruction", "mixed")
        and item["invalid_event_count"] == 0
        and item["unsupported_begin_end_event_count"] == 0 for item in traces)
    if interrupted or tool_timeout:
        status = "partial"
    elif returncode != 0 or "[ERROR]" in log:
        status = "failed"
    elif not inspection_errors and instruction_data and complete_marker and matched:
        status = "complete"
    else:
        status = "unverified"
    return {"status": status, "returncode": returncode, "interrupted": interrupted,
            "tool_timeout_detected": tool_timeout, "parse_completion_marker": complete_marker,
            "observed_kernel_names": names, "target_match_confirmed": matched,
            "traces": traces, "inspection_errors": inspection_errors,
            "scope": "Collection completeness, not application numerical correctness or hardware timing accuracy."}


def execute(plan):
    if os.name != "posix" or platform.system() != "Linux":
        raise ValueError("v0.1 collection requires Linux; offline inspection is platform independent.")
    output = fresh_output(plan["output"])
    output.mkdir(mode=0o700)
    manifest = output / "manifest.json"
    plan["status"] = "running"
    plan["started_at_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    manifest.write_text(json.dumps(plan, indent=2) + "\n")
    interrupted = False
    returncode = None
    launch_error = None
    log_path = output / "capture.log"
    with log_path.open("wb") as log_file:
        try:
            process = subprocess.Popen(plan["command_argv"], cwd=plan["working_directory"],
                                       stdout=log_file, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                returncode = process.wait(timeout=plan["wall_timeout_seconds"])
            except (subprocess.TimeoutExpired, KeyboardInterrupt):
                interrupted = True
                stop_own_process_group(process)
                returncode = process.returncode
        except OSError as error:
            launch_error = str(error)
    traces, errors = [], [launch_error] if launch_error else []
    profile = output / "profile"
    for file in discover_traces(profile) if profile.is_dir() else []:
        try:
            report = inspect_trace(file, "simulator")
            report["sha256"] = sha256(file)
            traces.append(report)
        except (OSError, ValueError, TypeError) as error:
            errors.append(f"{file}: {error}")
    result = classify_result(returncode, interrupted, log_path.read_text(errors="replace"),
                             traces, errors, plan["kernel_filter"])
    plan.update(result)
    plan["finished_at_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    manifest.write_text(json.dumps(plan, indent=2) + "\n")
    print(json.dumps({"status": plan["status"], "manifest": str(manifest),
                      "trace_count": len(traces)}, indent=2))
    return {"complete": 0, "partial": 2, "failed": 1, "unverified": 3}[plan["status"]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("simulator",), default="simulator")
    parser.add_argument("--soc", required=True)
    parser.add_argument("--kernel-name", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--tool")
    parser.add_argument("--cann-root")
    parser.add_argument("--workdir", default=os.getcwd())
    parser.add_argument("--launch-count", type=int, default=1)
    parser.add_argument("--timeout-minutes", type=int, default=2)
    parser.add_argument("--wall-timeout-seconds", type=float, default=300)
    parser.add_argument("--artifact", action="append", default=[], help="Additional binary/input to hash; not copied")
    parser.add_argument("--note", default="", help="Input dimensions, dtype, core count or other workload context")
    run = parser.add_mutually_exclusive_group()
    run.add_argument("--execute", action="store_true")
    run.add_argument("--dry-run", action="store_true", help="Default; query help and print the plan without running the app")
    parser.add_argument("application", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        plan = make_plan(args)
        if not args.execute:
            print(json.dumps(plan, indent=2))
            return 0
        return execute(plan)
    except (OSError, ValueError) as error:
        print(f"Collection not completed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
