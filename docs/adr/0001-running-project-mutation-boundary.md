# ADR-0001: Running Project Mutation Boundary

- Status: Accepted
- Date: 2026-07-29
- Decision owner: User-confirmed Option A

## Context

AIMCTextureGen stores portable JSON and image files as project truth. The
application must not publish unvalidated bytes or expose a partially written
file, and concurrent writers inside the running application must not race a
schema migration.

Windows `SetFileInformationByHandle` can atomically publish the validated
temporary file by its open handle. It does not provide a non-deprecated
compare-and-swap operation that replaces a path only when the destination still
has an expected file identity. An external process using POSIX rename semantics
can therefore replace the destination in the final interval between an
observable identity check and the application's rename call.

A Windows Transactional NTFS experiment closed that interval, but TxF is
deprecated, may be unavailable in future Windows versions, and would add an
NTFS transaction requirement to a product that otherwise supports ordinary
local Windows storage.

## Decision

- One running AIMCTextureGen application process owns its configured project
  root. Running multiple application instances against the same project root is
  unsupported.
- All AIMCTextureGen project writes go through the repository or service
  boundary. Schema migration writers are serialized by the application.
- File publication uses a same-directory, exclusively created temporary file:
  write, flush, `fsync`, bounded readback validation, and atomic rename. On
  Windows the validated source handle remains open and does not share delete
  access through publication, preventing substitution of the temporary path.
- Schema migration rechecks the observed `project.json` identity and exact
  bytes during validation. An observable mismatch preserves the newer file and
  returns `PROJECT_MANIFEST_CONFLICT`.
- Manual or external mutation of project internals while the application is
  running is unsupported. The application does not promise hostile
  compare-and-swap protection against a forced external POSIX rename in the
  final OS-call window. A later read may report such mutation as corruption,
  an unsafe path, or a conflict.
- AIMCTextureGen does not use TxF and does not require NTFS transactions.

## Consequences

The supported application workflow retains validated, atomic publication,
ordinary Windows path and reparse-point safeguards, Unicode paths, and
deterministic in-process writer serialization without a deprecated filesystem
dependency. Tests cover concurrent application opens and externally changed
files when the change is observable before publication; they do not claim
hostile external CAS.
