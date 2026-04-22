---
name: continue
description: Run the next pending agent in the active session
action: continue
---

# /continue

Resumes the active pipeline session and runs the next pending agent
(whichever stage is not yet complete). Used after a paused or
interrupted `@harness <task>` to proceed one step at a time.

## Usage

```
@harness /continue
```

If there is no active session, this returns an error directing you
to start one with `/feature-dev`.
