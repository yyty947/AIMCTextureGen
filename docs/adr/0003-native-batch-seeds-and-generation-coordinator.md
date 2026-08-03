# ADR-0003: Native Batch Seeds and an In-Process Generation Coordinator

- Status: Accepted
- Date: 2026-08-03
- Decision owners: project maintainer and Phase 5 implementers

## Context

The original MVP design said that all four candidates receive independent,
persisted seeds while user-selected parallelism is 1, 2 or 4. Inspection of
the pinned ComfyUI `KSampler` contract shows a conflict between those two
claims: one native ComfyUI prompt accepts one seed. With latent
`batch_size=2/4`, that seed initializes one noise stream whose outputs are
distinguished by their positions in the batch.

Submitting two or four independent one-image prompts concurrently could retain
one seed per candidate, but that is not ComfyUI native batch behavior. It has
different memory, scheduling, cancellation and reproducibility semantics.

Phase 5 also needs progress, cancellation, partial-candidate preservation and
restart recovery. The product controls one local GPU and one managed ComfyUI
process, so an unrestricted multi-job queue would add resource contention
before the MVP has a reason to support it.

The already verified model profile version 1 requires at least one style
reference and has single-image workflows. Phase 5 now allows zero style
references and needs native batch 1/2/4. Mutating version 1 would invalidate
its hashes and real verification evidence.

## Decision

### Persist execution-batch seeds

Every new schema-3 job persists an execution plan:

- parallelism 1: four batches of one candidate and four base seeds;
- parallelism 2: two batches of two candidates and two base seeds;
- parallelism 4: one batch of four candidates and one base seed.

A candidate is identified by its stable candidate index plus batch index,
position in batch and batch base seed. Seeds are generated automatically when
the job is created and are read-only afterward. Retry preserves the original
batch plan and seeds. A new job receives new seeds.

The product does not promise that candidate N is identical when parallelism
changes.

### Use one application-owned coordinator

FastAPI owns one long-lived `GenerationCoordinator`. It permits exactly one
nonterminal generation job across the application and serially submits that
job's native batches to the managed ComfyUI.

The coordinator is an orchestration component, not a database. Durable project
JSON, candidate state and atomically published artifacts remain the recovery
source. HTTP performs commands; WebSocket only publishes committed snapshots.

The coordinator may automatically start an already installed and verified
managed ComfyUI. It never downloads, repairs or changes generation parameters
as part of job execution.

Cancellation persists intent, interrupts the one active ComfyUI prompt and
waits for confirmation. If the managed process cannot confirm interruption,
the application may stop only its own identity-verified child process before
releasing the single-job slot.

### Add profile version 2

The verified `sdxl-mapchip-ipadapter` version 1 remains immutable. Version 2
adds:

- zero-to-eight style-reference capability;
- explicit style/no-style text2img and img2img workflow variants;
- native batch-size binding;
- ordered multi-output validation;
- a new manifest and workflow digests;
- a new real-GPU verification gate.

It reuses the version-1 model and custom-node artifacts by their verified
content hashes.

## Consequences

### Positive

- UI parallelism matches actual ComfyUI native batch behavior.
- Durable job records explain exactly how every candidate was produced.
- Cancellation can safely use ComfyUI's global interrupt because only one
  product job is active.
- Browser disconnection and FastAPI restart do not make memory-only state the
  recovery authority.
- Future multi-texture generation can queue targets inside one controlled job
  instead of introducing competing GPU jobs.
- The verified version-1 evidence remains valid.

### Costs and limitations

- Changing parallelism can change candidate pixels even when all other
  parameters match.
- A queued job occupies the only slot until continued, canceled or terminated.
- Native batch cancellation is batch-grained; partial, unvalidated output from
  an interrupted batch is not published.
- Retry needs explicit candidate lineage and raw-output completeness checks.
- Profile version 2 requires new workflow files, manifest support and real GPU
  measurements even though its large model files are unchanged.
- An in-process coordinator is appropriate for the local single-user MVP, but
  is not a distributed worker system.

## Rejected alternatives

### Keep four independent candidate seeds and call that native batching

Rejected because the pinned sampler does not accept a seed vector. Persisting
four labels while only one seed controls a batch would be misleading.

### Submit independent one-image prompts concurrently

Rejected because it preserves per-candidate seeds by changing the chosen
feature into concurrent prompts. Memory use, progress, output ordering and
cancellation would no longer represent batch sizes 2 and 4.

### Add a custom noise or sampler node

Rejected for the MVP because it adds a new maintained GPU node and verification
surface solely to preserve cross-parallel candidate identity that users do not
require.

### Run generation directly inside an HTTP request

Rejected because browser disconnects, restart recovery and cancellation would
be coupled to request lifetime.

### Add an external worker and queue

Rejected as disproportionate for one local user, one GPU and one managed
ComfyUI. The durable service boundary keeps that option open if a later product
actually needs it.

### Mutate model profile version 1

Rejected because old task bindings and real verification evidence refer to
immutable manifest and workflow hashes.
