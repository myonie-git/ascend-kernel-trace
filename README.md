# Ascend Kernel Trace — v0.1

An Agent Skills package for collecting simulator instruction timelines and inspecting
existing Ascend Chrome traces. It keeps operator spans, hardware sampling, simulator
events and inserted markers distinct.

## Use as a skill

Copy this directory into your coding agent's local skills directory, following that
client's installation instructions. `SKILL.md` is the portable entry point;
`agents/openai.yaml` is optional Codex UI metadata, not a runtime dependency.
Example requests:

- “Inspect this trace and tell me whether it contains instruction-level events.”
- “Plan a simulator capture for this existing kernel launcher, then run it.”
- “Compare these two traces; separate observations from explanations.”

## Use the scripts directly

Python 3.9+ and the standard library are sufficient for inspection and offline tests.
CANN is required only for actual collection; use an existing configured environment.
Collector subprocess tests run on Linux and are skipped elsewhere.

```bash
python3 scripts/inspect_trace.py tests/fixtures/instruction.json
python3 scripts/probe_env.py
python3 -m unittest discover -s tests -v
```

The fixture is deliberately synthetic and is not a hardware/simulator measurement.
For actual collection, follow [the simulator recipe](references/simulator.md).
The collector defaults to dry-run and accepts the user's application argv after `--`.
There is no built-in remote login, machine configuration, Docker image, installation
path, model shape, kernel name, or device reservation policy.

## Boundaries

- Automated capture in v0.1 is simulator-only. Hardware options are documented, not
  silently substituted or automatically exercised.
- Existing traces can be inspected without CANN or an NPU. Launcher architecture
  must match its execution environment; ARM64 is not a general skill requirement.
- JSON classification is conservative and supports complete (`X`) events. Unknown
  formats and unpaired `B`/`E` events require a native viewer or parser extension.
- The inspector loads a JSON file in memory and defaults to a 256 MiB input limit;
  `--max-bytes` can raise it when sufficient memory is available.
- Collection success does not validate application output or hardware performance.
- The test double exercises control flow, timeouts and failure handling. It is not
  evidence that a particular CANN/platform combination works.

## Contents

`scripts/` contains the probe, collector and inspector; `references/` contains
mode-specific guidance; `tests/fixtures/` contains small synthetic examples and a
fake profiler. No model data, proprietary binaries or captured raw traces are bundled.

## Validation

v0.1 passed 20 offline tests, real instruction/operator trace inspection, and two CPU
simulator captures on CANN 9.1.0 / msopprof 26.1.0 (Ascend910_9382). Hardware instruction
capture and other SoC/tool combinations remain unverified.

## Repository synchronization

GitHub and GitCode synchronize branches and tags in both directions through the
`Sync GitHub and GitCode` GitHub Actions workflow. GitHub pushes trigger a run;
a five-minute schedule also checks for GitCode updates. Scheduled runs may be delayed,
and manual runs are available in Actions.

Only fast-forward branch updates and new refs are copied. Divergent histories or
different objects for the same tag stop the entire preflight before any push; merge
the histories manually before retrying. The workflow never force-pushes or deletes
refs. A one-sided deletion can be recreated, so pause synchronization and delete the
ref on both repositories when retiring it.

GitCode credentials are stored in the `GITCODE_TOKEN` repository secret. Keep Actions
workflow changes on GitHub. Updates sent back using `GITHUB_TOKEN` do not trigger
another push workflow. Each remote push is atomic, but the two repositories are not
one transaction; a failed run can be retried after resolving any concurrent changes.

## License

Licensed under the [Apache License 2.0](LICENSE).
