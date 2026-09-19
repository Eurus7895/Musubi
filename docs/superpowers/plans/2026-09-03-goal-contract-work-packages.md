# Goal Contract and Work Package Control Loop

## Context

Baseline `dev@4d04637` already supplied Root-owned planning, role-order and
evidence gates, StageContract/StageGate, append-only pipeline attempts, token
budgeting, and deterministic file tools. It lacked an immutable definition of
done at goal level and an evidence-backed unit of adaptive execution.

## Implemented release scope

1. Shared canonical JSON and SHA-256 hashing, with StageContract adapted to the
   shared primitive without changing its public behavior.
2. Closed, versioned Goal Contract and Work Package validators with lineage,
   scope, expected delta, verifier, budget, dependency, and reversibility
   checks.
3. Append-only SQLite records for contract versions, criterion events, Work
   Package attempts, verification evidence, budget events, and rollback bytes.
4. `WorkPackageController` for freeze, replay, bounded attempt leases,
   deterministic predicate-to-criterion mapping, Gap Reports, completion,
   plateau detection, and same-hash retry enforcement.
5. Root runtime integration. `musubi_commit_plan` accepts the Goal
   Contract; Root then freezes a Work Package and spawns only its resolved
   brief. Worker rows carry goal, package, attempt, and contract hash IDs.
6. Required-criterion fail-closed finalization and explicit semantic verdicts.
7. File scope enforcement and byte-exact rollback journal for Musubi write,
   append, and edit tools. Automatic Work Packages cannot claim rollback over
   shell commands.
8. Replayable Goal → Work Package → Attempt → Evidence query for observability.

## Runtime policy

Direct Root execution has no legacy controller or migration switch. A Root may
answer conversational and read-only turns without opening Planning. Once it
calls `musubi_begin_plan`, it must freeze the plan and Goal Contract, execute
through bounded Work Packages, and satisfy the Gap Report before finalizing.
Pipeline metrics and control remain separate.

## Verification

- Contract/hash/version and retry tests.
- Mechanical versus semantic criterion tests.
- Scope, budget, plateau, persistence/replay, hierarchy, and rollback tests.
- Full Python compatibility suite and GUI tests.
- Static check that new substrate modules import no vendor/model packages.
