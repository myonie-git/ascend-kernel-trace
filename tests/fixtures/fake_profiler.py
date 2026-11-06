#!/usr/bin/env python3
"""Test double only: never executes CANN or the supplied application."""

import json
import os
from pathlib import Path
import shutil
import sys
import time

args = sys.argv[1:]
if args and args[0] == "op":
    args.pop(0)
mode = os.environ.get("FAKE_PROFILER_MODE", "complete")
if "--version" in args:
    print("TEST DOUBLE, not an Ascend installation")
elif "--help" in args:
    if mode == "unsupported":
        print("--output only")
    else:
        print("--soc-version --kernel-name --launch-count --aic-metrics --dump --timeout --output")
        print("PipeUtilization ResourceConflictRatio InstrTimeline")
else:
    if os.environ.get("FAKE_RUN_MARKER"):
        Path(os.environ["FAKE_RUN_MARKER"]).write_text("executed")
    if mode == "hang":
        time.sleep(30)
    output = Path(next(arg.split("=", 1)[1] for arg in args if arg.startswith("--output=")))
    output.mkdir(parents=True)
    (output / "received_argv.json").write_text(json.dumps(args))
    if mode == "failed":
        print("[ERROR] intentional test failure")
        sys.exit(7)
    if mode != "empty":
        folder = output / "OPPROF_test" / "simulator" / "core0.veccore0"
        folder.mkdir(parents=True)
        target = folder / "trace.json"
        if mode == "malformed":
            target.write_text('{"traceEvents": [')
        else:
            fixture = "operator.json" if mode == "operator" else "instruction.json"
            shutil.copyfile(Path(__file__).with_name(fixture), target)
    name = "different_kernel" if mode == "wrong-kernel" else "sample_kernel_0"
    print(f"[INFO] <ProfInit> Start profiling on kernel: {name}")
    if mode == "tool-timeout":
        print("[INFO] The timeout has reached and the application will be forcibly killed.")
    if mode != "missing-completion":
        print("[INFO] Profiling data parse finished.")
