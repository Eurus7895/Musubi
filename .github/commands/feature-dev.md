---
name: feature-dev
description: Run the full plan → design → code → review pipeline on the given request
action: pipeline
pipeline: feature-dev
---

# /feature-dev

Runs the `feature-dev` pipeline: a 4-agent sequence (planner, designer,
coder, reviewer) with the evaluator firewall enabled and a 3-attempt
correction loop.

## Usage

```
@harness /feature-dev <your feature request>
```

Example:

```
@harness /feature-dev add a /logout endpoint that revokes the session cookie
```

The rest of the input (everything after `/feature-dev`) becomes the
request passed to the planner.

## See also

- `.github/pipelines/feature-dev/pipeline.yaml` — pipeline definition
- `.github/pipelines/feature-dev/README.md` — stage + correction overview
- `/CLAUDE.md` — full design doc
