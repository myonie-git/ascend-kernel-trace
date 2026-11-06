# Hardware mode: capability check before collection

v0.1 does not automate hardware instruction capture or modify kernels to add markers.
An existing hardware JSON trace can still be inspected offline if its schema fits.
Use this reference to explain the options and plan an explicitly requested extension.

Keep four kinds of evidence separate:

| Evidence | What it describes |
| --- | --- |
| Operator timeline / op_summary | Recorded task boundaries and aggregate metrics |
| Hardware instruction sampling | Instruction observations on a supported chip/tool combination |
| Simulator instruction timeline | Model-generated execution and dependency timing |
| Inserted code markers | Selected instrumented intervals, with instrumentation overhead |

The official guide reviewed on 2026-11-06 lists `InstrTimeline` and `PipeTimeline`
hardware support for Ascend 950PR/950DT. Do not turn this dated list into an immutable
rule, or infer support from help advertising an option alone. Check the actual chip,
installed version and corresponding support documentation. If unknown, report unknown.

`TimelineDetail` can collect board performance while generating a **simulated**
instruction timeline. Do not label its fine-grained timeline as hardware sampling
merely because the run also collected board data.

For a supported hardware workflow, preserve the user's device and workload choice,
record the exact metric selection, and verify the native output. Sampling may cover
only part of the execution or some cores. A trace viewer alone cannot prove full
coverage. Do not reset devices, reserve all devices, kill unrelated jobs, or install
profiling components as an implicit part of diagnosing a trace.

If hardware instruction sampling is unavailable, present simulator analysis or
targeted instrumentation as distinct alternatives. Ask before changing the requested
measurement or editing source. Markers require reasoning about which pipeline the
clock reads actually delimit; they are not a replacement for complete instruction
tracing. Report the additional synchronization, code and measurement perturbation.

Source: [official msopprof board guide](https://github.com/Ascend/msopprof/blob/master/docs/zh/user_guide/msopprof_user_guide.md).
