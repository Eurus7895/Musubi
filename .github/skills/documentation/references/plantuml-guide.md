# PlantUML Guide — Documentation Skill Reference

Use this reference when writing PlantUML source or choosing which diagram type
fits a given relationship.

---

## Choosing a Diagram Type

| Type | Use when | Keyword |
|------|---------|---------|
| Sequence | A → B → C call flow, API interactions, agent pipeline | `@startuml` + `participant` |
| Component | System architecture, module dependencies | `@startuml` + `component` |
| Class | Data models, schemas, inheritance | `@startuml` + `class` |
| Activity | Flowcharts, decision trees, correction loop | `@startuml` + `start` |
| State | State machines (session status transitions) | `@startuml` + `[*]` |

---

## Sequence Diagram

```plantuml
@startuml
title Agent Pipeline — Happy Path

actor User
participant "Copilot\n(Planner)" as Planner
participant "MCP Server" as MCP
database "SQLite" as DB
participant "Copilot\n(Coder)" as Coder
participant "Copilot\n(Reviewer)" as Reviewer

User -> Planner : request
Planner -> MCP : musubi_new_session(request)
MCP -> DB : INSERT session
MCP --> Planner : session_id

Planner -> MCP : musubi_write_stage("plan", output)
MCP -> DB : INSERT stage_output

Coder -> MCP : musubi_read_stage("plan")
MCP -> DB : SELECT stage_output
MCP --> Coder : plan JSON
Coder -> MCP : musubi_write_stage("code", output)

Reviewer -> MCP : musubi_read_stage("code")
MCP --> Reviewer : code JSON
Reviewer -> MCP : musubi_write_stage("review", {status: "pass"})
MCP --> User : final output
@enduml
```

### Syntax reference

```plantuml
A -> B : label          " synchronous call
A --> B : label         " dashed return
A ->> B : label         " async call
activate A              " show activation bar
deactivate A
group optional [condition]
    A -> B
end
alt success
    A -> B
else failure
    A -> C
end
```

---

## Component Diagram

```plantuml
@startuml
title Musubi — Component Overview

package "GitHub Copilot (LLM)" {
    [Planner Agent]
    [Coder Agent]
    [Reviewer Agent]
}

package "Musubi (MCP Server)" {
    [server.py] as server
    [context_builder.py] as ctx
    [verifier.py] as ver
    [state.py] as state
    [executor.py] as exec
    [stage_gate.py] as gate
}

database "SQLite" as db

[Planner Agent] --> server : musubi_new_session
[Coder Agent] --> server : musubi_write_stage
[Reviewer Agent] --> server : musubi_write_stage

server --> ctx
server --> ver
server --> state
server --> exec
server --> loop
state --> db
@enduml
```

---

## Class Diagram

```plantuml
@startuml
title Session State — Data Model

class Session {
    +session_id: str
    +request: str
    +status: SessionStatus
    +created_at: str
}

class StageOutput {
    +id: int
    +session_id: str
    +stage: str
    +attempt: int
    +output: str
    +created_at: str
}

enum SessionStatus {
    active
    complete
    escalated
}

Session "1" *-- "0..*" StageOutput : contains
Session --> SessionStatus
@enduml
```

---

## Activity Diagram (Stage Attempt Loop)

```plantuml
@startuml
title Stage Attempt Loop

start
:Coder writes stage output;
:verifier.verify(output);
if (schema valid?) then (no)
    :return validation error to Coder;
    stop
endif

:Reviewer reviews code;
if (status?) then (pass)
    :executor runs lint + tests;
    if (execution pass?) then (yes)
        :return final output;
        stop
    else (no)
        :send exec errors as fix_instructions;
    endif
else (fail)
    :send fix_instructions to Coder;
endif

:attempt = attempt + 1;
if (attempt > 3?) then (yes)
    :escalate to user;
    stop
else (no)
    :Coder retries;
    -> Coder writes stage output;
endif
@enduml
```

---

## State Diagram (Session Stage Status)

```plantuml
@startuml
title Stage Status Transitions

[*] --> pending : stage created
pending --> in_progress : agent starts work
in_progress --> complete : musubi_write_stage succeeds
in_progress --> in_progress : retry (new attempt row)
in_progress --> escalated : max attempts reached
complete --> [*]
escalated --> [*]
@enduml
```

---

## Styling Tips

```plantuml
@startuml
skinparam backgroundColor #FEFEFE
skinparam participant {
    BackgroundColor #DAE8FC
    BorderColor #6C8EBF
}
skinparam sequence {
    ArrowColor #333333
    LifeLineBorderColor #666666
}
@enduml
```

- Use `skinparam` blocks at the top for consistent styling.
- Keep diagrams under 20 participants/components — split into sub-diagrams if larger.
- Use `note left/right of X` for annotations.
- Use `== Phase Name ==` separators in sequence diagrams to mark stages.
