---
name: ascend-kernel-trace
description: >-
  Collect and inspect Ascend kernel instruction timelines with CANN/MindStudio tools.
  Use for simulator trace collection, existing instruction or operator trace analysis,
  and DMA, vector/cube, synchronization, or pipeline-overlap questions.
  Distinguish instruction events from operator spans and simulated timing from hardware measurements.
license: Apache-2.0
metadata:
  version: "0.1.0"
---

# Ascend Kernel Trace

Scripts require Python 3.9+. Collection additionally needs Linux, a compatible
CANN/msopprof simulator environment, and a trusted working application. Offline
JSON inspection does not require CANN or an NPU.

Use raw evidence to answer a kernel-timeline question. This skill does not authorize
kernel edits, device resets, software installation, remote-machine selection, or
publication. Follow the user's chosen program and resource constraints.

## Select the relevant workflow

- **Existing trace:** use `scripts/inspect_trace.py`; no CANN probe or application run
  is needed. Read [interpretation.md](references/interpretation.md) before making
  timing or synchronization claims.
- **New simulator capture:** read [simulator.md](references/simulator.md), probe the
  configured environment, prepare a plan, then execute only within the user's
  authorization. v0.1 automates this mode.
- **Hardware instruction capture or code instrumentation:** read
  [hardware.md](references/hardware.md). v0.1 does not automate those modes; verify
  platform/version support and propose the appropriate next step. Never silently
  substitute simulation for a requested hardware measurement.

## Establish the inputs

Identify the target kernel, the existing application command, its working directory,
and the desired granularity. For comparisons, record shapes, dtypes, launch/core
counts, inputs, build options, and relevant binary hashes when available.
Ask only for missing information that blocks the selected workflow.

Do not infer the target SoC from the host CPU architecture. Do not require ARM64
for offline trace inspection: executable architecture is a property of the user's
application, not of this skill. A generic name such as `trace.json` does not establish
data origin, event granularity, or collection completeness.

## Probe and collect

Paths below are relative to this skill directory. Scripts use Python's standard
library and inherited environment; they do not source shell profiles or install tools.

```bash
python3 scripts/probe_env.py --soc TARGET_SOC
python3 scripts/collect.py --soc TARGET_SOC --kernel-name KERNEL \
  --workdir APPLICATION_DIRECTORY --output NEW_OUTPUT_DIRECTORY \
  -- ./application application_arguments
```

Replace the uppercase parameters with the user's values. The second command is a
dry run by default: it queries tool help, hashes explicit artifacts, and prints the
argv without running the application or creating the output directory. Add
`--execute` for an authorized run. `--artifact` records additional input/library
hashes; `--note` records workload details not present in argv.

The output directory must be fresh, have an existing parent, and contain no symlink
components. Preserve the user's application working directory. Do not use `eval`,
silently rebuild with `-g`, resize inputs, or insert timestamp probes. If those
changes are needed, explain them and obtain the appropriate authorization.

CLI help advertising an option is not proof that a platform supports it. Unsupported
options or missing dependencies should produce an actionable report, not repeated
trial flags, altered system configuration, or an unannounced fallback to an NPU.

## Verify and interpret

```bash
python3 scripts/inspect_trace.py PATH_TO_TRACE_OR_RESULT_DIRECTORY
```

By default provenance is unknown. Supply `--source simulator` or another supported
source only when backed by the collection command/logs; this label is a declaration,
not automatic proof. Check the collection manifest separately for completeness.

The inspector confirms instruction evidence using pipeline metadata and instruction
PCs, not opcode-like names alone. Unknown schemas remain undetermined. v0.1 handles
Chrome complete events (`ph: X`); it reports, but does not reconstruct, `B`/`E` spans.
Open those or unsupported formats in the native viewer rather than inventing events.

For synchronization diagnosis, inspect producer, consumer, buffer reuse, and event
direction in the relevant source. Adjacent waits need not be redundant. Report an
observed overlap or stall separately from its proposed explanation. Do not sum
overlapping lanes into kernel latency or convert simulator wait durations into
hardware wait durations. A smaller debug workload does not establish full-workload
performance. Performance comparisons still need controlled hardware measurements.

## Deliver

Return the capture status, source and granularity, environment/tool versions, exact
command, workload context, explicit artifact hashes, raw trace/log locations, and
the supported conclusion. Distinguish complete, partial, failed, and unverified
captures. Successful collection is not a numerical-correctness test.

Prefer original traces in MindStudio Insight. Optional plots must use actual event
timestamps and disclose filtering, time normalization, interval merging, and
annotations. Mark schematic diagrams separately. Missing events are not evidence
that a core was idle. Do not upload traces or include credentials/proprietary data
in a shared report without authorization.
