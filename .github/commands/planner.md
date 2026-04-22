---
name: planner
description: Run the planner agent (creates or resumes a session)
action: step
agent: planner
---

# /planner

Runs only the planner agent. If no active session, a new one is
created with the given request. Useful to review the plan before
running the rest of the pipeline.

## Usage

```
@harness /planner <your feature request>
```
