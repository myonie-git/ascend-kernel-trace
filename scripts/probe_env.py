#!/usr/bin/env python3
"""Read-only discovery. Help advertising a metric is not a platform support test."""

import argparse
import datetime
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess


SIMULATOR_FLAGS = (
    "--soc-version", "--kernel-name", "--launch-count", "--aic-metrics",
    "--dump", "--timeout", "--output",
)


def query(argv, timeout=10):
    try:
        process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, errors="replace", start_new_session=(os.name == "posix"))
        try:
            output, _ = process.communicate(timeout=timeout)
            return {"argv": argv, "returncode": process.returncode, "text": output[:131072]}
        except subprocess.TimeoutExpired:
            try:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except ProcessLookupError:
                pass
            output, _ = process.communicate()
            return {"argv": argv, "returncode": None, "text": output[:131072],
                    "error": f"Probe timed out after {timeout} seconds; partial output retained."}
    except OSError as error:
        return {"argv": argv, "returncode": None, "text": "", "error": str(error)}


def cann_info(root, soc):
    root = root or os.environ.get("ASCEND_HOME_PATH") or os.environ.get("ASCEND_TOOLKIT_HOME")
    result = {"root": str(Path(root).expanduser()) if root else None,
              "version_fields": [], "simulator_directories": [], "soc": soc}
    if not root:
        result["note"] = "CANN root unknown; source its environment or pass --cann-root."
        return result
    path = Path(root).expanduser()
    names = ("version.info", "version.cfg", "ascend_toolkit_install.info",
             f"{platform.machine()}-linux/ascend_toolkit_install.info")
    for name in names:
        candidate = path / name
        if candidate.is_file() and candidate.stat().st_size <= 1024 * 1024:
            for line in candidate.read_text(errors="replace").splitlines():
                key = line.split("=", 1)[0].strip().lower()
                if "=" in line and "version" in key:
                    result["version_fields"].append({"file": str(candidate), "value": line})
    if soc:
        for suffix in ("lib", "camodel"):
            candidate = path / "tools" / "simulator" / soc / suffix
            if candidate.is_dir():
                result["simulator_directories"].append(str(candidate))
    result["note"] = "Directory presence does not prove simulator/runtime compatibility."
    return result


def probe_environment(tool=None, cann_root=None, soc=None):
    candidates = [tool] if tool else ["msopprof", "msprof"]
    reports = []
    for candidate in candidates:
        executable = shutil.which(candidate)
        if not executable:
            continue
        executable = os.path.abspath(executable)
        prefix = [executable] + (["op"] if Path(executable).name == "msprof" else [])
        simulator_help = query(prefix + ["simulator", "--help"])
        # Keep discovery in simulator mode; default/board help may wait for a driver.
        version = query(prefix + ["simulator", "--version"])
        text = simulator_help["text"] if simulator_help["returncode"] == 0 else ""
        flags = {flag: flag in text for flag in SIMULATOR_FLAGS}
        reports.append({
            "prefix": prefix,
            "version": version,
            "simulator_help": simulator_help,
            "simulator_flags_advertised": flags,
            "simulator_metrics_advertised": {
                metric: metric.lower() in text.lower()
                for metric in ("PipeUtilization", "ResourceConflictRatio")
            },
            "hardware_platform_support": "unverified",
        })
        if (all(flags.values())
                and all(reports[-1]["simulator_metrics_advertised"].values())):
            break
    selected = next((report for report in reports
                     if all(report["simulator_flags_advertised"].values())
                     and all(report["simulator_metrics_advertised"].values())), None)
    return {
        "schema_version": 1,
        "observed_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "host": {"system": platform.system(), "architecture": platform.machine(),
                 "python": platform.python_version()},
        "cann": cann_info(cann_root, soc),
        "tools": reports,
        "selected_simulator_prefix": selected["prefix"] if selected else None,
        "device_access": "no explicit device query",
        "notes": ["Only version/help commands and selected CANN metadata were read.",
                  "SoC, installed models and hardware support are not inferred from host architecture.",
                  "Help text is a CLI capability hint, not successful collection evidence.",
                  "Only simulator-mode help/version is queried; tools may load their runtime libraries."],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tool", help="Path/name of a trusted msopprof or msprof executable")
    parser.add_argument("--cann-root", help="Configured CANN installation; no environment script is sourced")
    parser.add_argument("--soc", help="Explicit simulator target; no device is queried")
    args = parser.parse_args()
    print(json.dumps(probe_environment(args.tool, args.cann_root, args.soc), indent=2))


if __name__ == "__main__":
    main()
