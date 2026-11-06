# Interpret the data, not the filename

## Establish evidence

Collection command, environment and logs establish provenance. Event content
establishes granularity. A file can be a valid Chrome trace yet contain only operator
spans. Conversely, no source-line mapping does not imply no instruction events.

The v0.1 parser conservatively confirms an instruction when a complete event has an
opcode-like name, an instruction PC, and a recognized pipeline lane. It leaves other
schemas undetermined instead of guessing from a name such as `VCONV`. This can miss
valid traces from different tool versions: inspect them in the native viewer and
extend the parser with a documented fixture before claiming support.

Chrome complete-event `ts` and `dur` use microseconds. CANN numeric-string timestamps
are accepted; absolute timestamps are subtracted using decimal arithmetic before
converting intervals to floats, preserving short intervals on large timestamps.
`displayTimeUnit` controls display rather than rescaling those fields. This script is not a parser for unrelated
formats whose fields happen to have the same names. Negative/nonfinite durations
are invalid. Begin/end events and truncated JSON need separate treatment; v0.1 does
not reconstruct them.

## Attribute carefully

- Keep source, target workload, and measurement boundary explicit. Do not compare a
  single-core short simulation directly with a full multi-core hardware latency.
- The union of event intervals on a lane is a description of recorded activity,
  not a calibrated utilization percentage. Different lanes may overlap.
- A whole-file event span can include setup/drain events not counted by the tool's
  kernel-duration metric. Explain the boundary before comparing the numbers.
- Summed waits are not automatically critical-path stalls. Understand the tool's
  event semantics and the producer/consumer direction before assigning a cause.
- A large aggregate pipeline time does not identify the exact code segment that
  caused a regression. Correlate instruction PC/source, schedule and controlled runs.

For buffer/synchronization questions, map which pipeline writes each buffer, which
reads it, and when it can be overwritten. Input-read completion and output-DMA
completion can protect different lifetimes. Do not recommend deleting a wait solely
because another wait is nearby or because its own duration looks small.

Report a diagnosis as: observed change, supporting events/source, plausible mechanism,
and remaining uncertainty. Use matched unprofiled hardware runs for a performance
claim. A simulator is useful for schedule hypotheses; it is not cycle-identical proof.

## Present without inventing data

Prefer the native viewer for the complete timeline. Any derived plot must identify
its source file and units, selected cores/lanes/events, cropped range, reference time,
merged intervals, and annotations. Labels inferred from instruction order are analysis,
not additional profiler fields. Blank space after filtering does not prove core idle.

Never manufacture instruction positions from an operator's total duration. If only
a schematic is possible, label it as a schematic and keep it separate from measured
or simulated event plots. Preserve original files for independent inspection.
