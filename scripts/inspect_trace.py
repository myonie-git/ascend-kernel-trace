#!/usr/bin/env python3
"""Inspect Chrome trace JSON without CANN; provenance and granularity are separate."""

import argparse
import collections
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
import sys


PIPE_NAMES = re.compile(r"^(?:SCALAR(?:LDST)?|VECTOR|VEC|CUBE|MTE[123]|FIX(?:P|PIPE)?|FLOWCTRL)(?:_|$)", re.I)
INSTRUCTION_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
SOURCES = ("unknown", "simulator", "hardware-instruction", "hardware-operator", "instrumented")


def trace_number(value):
    if isinstance(value, bool) or not isinstance(value, (float, int, str)):
        return None
    try:
        number = Decimal(str(value))
        return number if number.is_finite() else None
    except InvalidOperation:
        return None


def union_duration(intervals):
    total = 0
    last_start = last_end = None
    for start, end in sorted(intervals):
        if last_end is None or start > last_end:
            if last_end is not None:
                total += last_end - last_start
            last_start, last_end = start, end
        else:
            last_end = max(last_end, end)
    return total + (last_end - last_start if last_end is not None else 0)


def discover_traces(path):
    path = Path(path)
    if path.is_file():
        return [path]
    if not path.is_dir():
        raise ValueError(f"Not a file or directory: {path}")
    candidates = sorted(path.rglob("trace.json"))
    # Avoid counting a combined trace and its per-core copies twice.
    per_core_parents = {file.parent.parent for file in candidates
                        if file.parent.name.startswith("core")}
    return [file for file in candidates if file.parent not in per_core_parents]


def inspect_trace(path, source="unknown", max_bytes=256 * 1024 * 1024):
    path = Path(path)
    if path.stat().st_size > max_bytes:
        raise ValueError(f"Trace exceeds --max-bytes: {path}")
    document = json.loads(path.read_text())
    if isinstance(document, list):
        events, display_unit = document, None
    elif isinstance(document, dict) and isinstance(document.get("traceEvents"), list):
        events, display_unit = document["traceEvents"], document.get("displayTimeUnit")
    else:
        raise ValueError("Expected a Chrome trace event list or an object with traceEvents")
    processes, threads = {}, {}
    for event in events:
        if not isinstance(event, dict) or event.get("ph") != "M":
            continue
        args = event.get("args") or {}
        if not isinstance(args, dict):
            continue
        if event.get("name") == "process_name":
            processes[event.get("pid")] = str(args.get("name", ""))
        elif event.get("name") == "thread_name":
            threads[(event.get("pid"), event.get("tid"))] = str(args.get("name", ""))
    counts, instructions, operators = collections.Counter(), [], []
    invalid = unsupported = 0
    lanes = {}
    complete = []
    intervals_all = []
    for event in events:
        if not isinstance(event, dict):
            invalid += 1
            continue
        if event.get("ph") in ("B", "E"):
            unsupported += 1
        if event.get("ph") != "X":
            continue
        ts, dur = trace_number(event.get("ts")), trace_number(event.get("dur"))
        if ts is None or dur is None or dur < 0:
            invalid += 1
            continue
        complete.append(event)
        intervals_all.append((ts, ts + dur))
        name = str(event.get("name", ""))
        counts[name] += 1
        pid, tid = event.get("pid"), event.get("tid")
        process = processes.get(pid, "")
        thread = threads.get((pid, tid), "")
        args = event.get("args") or {}
        if not isinstance(args, dict):
            args = {}
        pipe_lane = bool(PIPE_NAMES.match(process) or PIPE_NAMES.match(thread))
        # An opcode-like name alone can also be an operator name. Require PC metadata.
        pc = args.get("pc_addr", args.get("pc"))
        valid_pc = ((isinstance(pc, int) and not isinstance(pc, bool) and pc >= 0)
                    or (isinstance(pc, str) and re.fullmatch(r"(?:0x[0-9a-fA-F]+|[0-9]+)", pc)))
        if pipe_lane and valid_pc and INSTRUCTION_NAME.fullmatch(name):
            instructions.append(event)
        category = str(event.get("cat", "")).lower()
        task_type = str(args.get("Task Type", args.get("task_type", ""))).upper()
        if not pipe_lane and (any(key in category for key in ("kernel", "operator", "cpu_op"))
                              or task_type in ("AI_CORE", "AI_VECTOR_CORE", "AI_CPU", "AICPU")):
            operators.append(event)
        lane = lanes.setdefault((pid, tid), {"pid": pid, "tid": tid,
                               "process": process, "thread": thread, "intervals": []})
        lane["intervals"].append((ts, ts + dur))
    granularity = ("instruction" if instructions else
                   "operator" if operators else "undetermined")
    if instructions and operators:
        granularity = "mixed"
    warnings = []
    if source == "unknown":
        warnings.append("Timing provenance is unknown; a filename or opcode does not establish hardware versus simulator origin.")
    if not instructions:
        warnings.append("No instruction events confirmed by both pipeline metadata and instruction PC; do not invent an internal timeline.")
    if invalid:
        warnings.append("Malformed events were excluded; the result is incomplete.")
    if unsupported:
        warnings.append("B/E events are present but not paired in v0.1; inspect them in a native viewer.")
    if source == "hardware-operator" and instructions:
        warnings.append("Declared operator provenance conflicts with instruction evidence; verify collection metadata.")
    if source == "hardware-instruction" and not instructions:
        warnings.append("Declared hardware instruction source was not confirmed by event granularity.")
    lane_results = []
    for lane in lanes.values():
        intervals = lane.pop("intervals")
        lane_results.append({**lane, "event_count": len(intervals),
                             "interval_union_us": float(union_duration(intervals))})
    return {
        "path": str(path), "declared_source": source, "granularity": granularity,
        "timestamp_unit": "us (Chrome trace format)", "display_time_unit": display_unit,
        "complete_event_count": len(complete), "instruction_event_count": len(instructions),
        "operator_event_count": len(operators), "invalid_event_count": invalid,
        "unsupported_begin_end_event_count": unsupported,
        "event_span_us": float(max(end for _, end in intervals_all)
                               - min(start for start, _ in intervals_all)) if complete else None,
        "instruction_counts": dict(collections.Counter(e["name"] for e in instructions)),
        "most_common_events": dict(counts.most_common(20)), "lanes": lane_results,
        "samples": [{key: event[key] for key in ("name", "ts", "dur", "pid", "tid", "args")
                     if key in event} for event in instructions[:3]],
        "warnings": warnings,
        "limits": ["Standalone JSON cannot prove collection completeness or timing provenance.",
                   "Lane interval unions describe recorded events, not calibrated utilization; lanes overlap.",
                   "Event span is not necessarily the profiler's kernel duration boundary."],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--source", choices=SOURCES, default="unknown")
    parser.add_argument("--max-bytes", type=int, default=256 * 1024 * 1024)
    args = parser.parse_args()
    try:
        paths = list(dict.fromkeys(file for path in args.paths for file in discover_traces(path)))
        if not paths:
            raise ValueError("No trace.json found; provide the exact file path for other filenames.")
        reports = [inspect_trace(path, args.source, args.max_bytes) for path in paths]
        print(json.dumps({"schema_version": 1, "traces": reports}, indent=2))
        return 0
    except (OSError, ValueError, TypeError) as error:
        print(f"Trace inspection failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
