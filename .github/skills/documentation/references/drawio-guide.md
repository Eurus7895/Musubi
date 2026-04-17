# Draw.io Guide — Documentation Skill Reference

Use this reference when producing or editing `.drawio` XML files.

---

## What Draw.io Is

Draw.io (also app.diagrams.net) produces diagrams stored as XML files with a
`.drawio` extension. The files are committed to the repo and opened in the
Draw.io desktop app or at https://app.diagrams.net.

**When to use Draw.io over PlantUML:**
- Free-form spatial layout matters (network topology, physical architecture)
- Stakeholders will edit the diagram directly
- The diagram does not map cleanly to a relationship type (sequence, class, etc.)

---

## File Format

A minimal `.drawio` file:

```xml
<mxfile>
  <diagram name="Architecture" id="arch-001">
    <mxGraphModel dx="1422" dy="762" grid="1" gridSize="10" guides="1"
                  tooltips="1" connect="1" arrows="1" fold="1"
                  page="1" pageScale="1" pageWidth="1169" pageHeight="827"
                  math="0" shadow="0">
      <root>
        <mxCell id="0" />
        <mxCell id="1" parent="0" />
        <!-- shapes go here, parent="1" -->
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>
```

Every cell has a unique `id`. All shapes use `parent="1"` (the root layer).
Edges use `source` and `target` referencing shape IDs.

---

## Common Shapes

### Rectangle (process / component)

```xml
<mxCell id="box1" value="MCP Server" style="rounded=1;whiteSpace=wrap;html=1;"
        vertex="1" parent="1">
  <mxGeometry x="100" y="100" width="120" height="60" as="geometry" />
</mxCell>
```

### Cylinder (database)

```xml
<mxCell id="db1" value="SQLite" style="shape=cylinder;whiteSpace=wrap;html=1;"
        vertex="1" parent="1">
  <mxGeometry x="300" y="100" width="80" height="60" as="geometry" />
</mxCell>
```

### Arrow (edge / connection)

```xml
<mxCell id="e1" value="" style="edgeStyle=none;"
        edge="1" source="box1" target="db1" parent="1">
  <mxGeometry relative="1" as="geometry" />
</mxCell>
```

### Labeled arrow

```xml
<mxCell id="e2" value="write_stage()" style="edgeStyle=none;"
        edge="1" source="box1" target="db1" parent="1">
  <mxGeometry relative="1" as="geometry" />
</mxCell>
```

### Swimlane (group / container)

```xml
<mxCell id="sw1" value="Harness Layer" style="swimlane;"
        vertex="1" parent="1">
  <mxGeometry x="50" y="50" width="400" height="300" as="geometry" />
</mxCell>
<!-- children use parent="sw1" -->
<mxCell id="c1" value="state.py" style="rounded=1;"
        vertex="1" parent="sw1">
  <mxGeometry x="20" y="60" width="100" height="40" as="geometry" />
</mxCell>
```

---

## Style Reference

| Property | Example | Effect |
|----------|---------|--------|
| `fillColor` | `fillColor=#dae8fc` | Background fill |
| `strokeColor` | `strokeColor=#6c8ebf` | Border color |
| `fontStyle` | `fontStyle=1` | 1=bold, 2=italic, 4=underline |
| `fontSize` | `fontSize=14` | Font size in pt |
| `dashed` | `dashed=1` | Dashed border |
| `rounded` | `rounded=1` | Rounded corners |
| `shape` | `shape=cylinder` | Shape type |

Common shape types: `mxgraph.aws4.*`, `mxgraph.azure.*`, `cylinder`,
`parallelogram`, `hexagon`, `ellipse`, `rhombus`.

---

## Harness Architecture Diagram — Starter Template

```xml
<mxfile>
  <diagram name="CopilotHarness Architecture">
    <mxGraphModel>
      <root>
        <mxCell id="0" /><mxCell id="1" parent="0" />

        <mxCell id="copilot" value="GitHub Copilot (LLM)"
                style="rounded=1;fillColor=#fff2cc;strokeColor=#d6b656;"
                vertex="1" parent="1">
          <mxGeometry x="400" y="40" width="160" height="60" as="geometry" />
        </mxCell>

        <mxCell id="mcp" value="server.py (MCP stdio)"
                style="rounded=1;fillColor=#dae8fc;strokeColor=#6c8ebf;"
                vertex="1" parent="1">
          <mxGeometry x="400" y="160" width="160" height="60" as="geometry" />
        </mxCell>

        <mxCell id="state" value="state.py"
                style="rounded=1;" vertex="1" parent="1">
          <mxGeometry x="200" y="280" width="100" height="50" as="geometry" />
        </mxCell>

        <mxCell id="verifier" value="verifier.py"
                style="rounded=1;" vertex="1" parent="1">
          <mxGeometry x="340" y="280" width="100" height="50" as="geometry" />
        </mxCell>

        <mxCell id="executor" value="executor.py"
                style="rounded=1;" vertex="1" parent="1">
          <mxGeometry x="480" y="280" width="100" height="50" as="geometry" />
        </mxCell>

        <mxCell id="db" value="SQLite"
                style="shape=cylinder;fillColor=#f8cecc;strokeColor=#b85450;"
                vertex="1" parent="1">
          <mxGeometry x="240" y="400" width="80" height="60" as="geometry" />
        </mxCell>

        <mxCell id="e1" value="MCP tool calls" style="edgeStyle=none;"
                edge="1" source="copilot" target="mcp" parent="1">
          <mxGeometry relative="1" as="geometry" />
        </mxCell>
        <mxCell id="e2" value="" style="edgeStyle=none;"
                edge="1" source="mcp" target="state" parent="1">
          <mxGeometry relative="1" as="geometry" />
        </mxCell>
        <mxCell id="e3" value="" style="edgeStyle=none;"
                edge="1" source="mcp" target="verifier" parent="1">
          <mxGeometry relative="1" as="geometry" />
        </mxCell>
        <mxCell id="e4" value="" style="edgeStyle=none;"
                edge="1" source="mcp" target="executor" parent="1">
          <mxGeometry relative="1" as="geometry" />
        </mxCell>
        <mxCell id="e5" value="" style="edgeStyle=none;"
                edge="1" source="state" target="db" parent="1">
          <mxGeometry relative="1" as="geometry" />
        </mxCell>
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>
```

---

## Tips

- IDs must be unique within the file. Use descriptive strings, not sequential numbers.
- Keep geometry on a 10px grid (`gridSize="10"`) for clean alignment.
- Use swimlanes to group related components visually.
- Export to SVG for embedding in Markdown documentation.

---

## Making a Diagram Fully Editable (Manual Layout)

Apply these rules when refactoring an existing `.drawio` file so it behaves as a
static manual layout — no auto-routing, no node resize cascades, no edge jumping.

### Why diagrams break editability

Draw.io's auto-layout features (`childLayout`, `resizeParent`, orthogonal edge routing)
recalculate positions whenever a node is moved or resized. This causes edges to jump,
containers to expand unexpectedly, and nodes to shift. The fix is to strip all
automatic layout behaviour and let geometry be the single source of truth.

### Rules to apply

**1. Remove auto-layout from `mxGraphModel`**

Strip these attributes if present on `<mxGraphModel>`:
- `autosize`
- `resizeParent`
- `childLayout`

Safe attributes to keep: `dx`, `dy`, `grid`, `gridSize`, `guides`, `tooltips`,
`connect`, `arrows`, `fold`, `page`, `pageScale`, `pageWidth`, `pageHeight`.

**2. Remove auto-layout from swimlanes and containers**

For any `<mxCell>` with `style="swimlane;..."`, remove from the style string:
- `childLayout=stackLayout`
- `resizeParent=1`
- `resizeParentMax=...`
- `collapsible=...` (optional — only remove if causing resize issues)

Keep: `swimlane`, `fillColor`, `strokeColor`, `fontStyle`, `fontSize`, and any
visual-only properties.

**3. Fix all edges**

Replace any routing style with `edgeStyle=none`:

```xml
<!-- before — auto-routing, jumps on node move -->
<mxCell style="edgeStyle=orthogonalEdgeStyle;orthogonalLoop=1;..." .../>

<!-- after — stable straight line -->
<mxCell style="edgeStyle=none;" .../>
```

Remove from edge styles: `orthogonalLoop=1`, `jettySize=auto`, `exitX`, `exitY`,
`exitDx`, `exitDy`, `entryX`, `entryY`, `entryDx`, `entryDy`.

Only add `<Array as="points">` waypoints if an edge must visually avoid overlapping
a node. Do not add waypoints pre-emptively.

**4. Preserve all IDs and connections**

- Never change `id` attributes on any `<mxCell>`.
- Never change `source` or `target` on edges unless the connection is logically wrong.
- Never change `parent` attributes.

**5. Preserve all geometry**

- Keep all `x`, `y`, `width`, `height` values unchanged.
- Do not add or remove `<mxGeometry>` children.
- `relative="1"` on edge geometry is correct — leave it.

**6. Do not touch non-layout styles**

Only modify style tokens related to layout (`edgeStyle`, `childLayout`,
`resizeParent`, routing attributes). Leave all visual styles (`fillColor`,
`strokeColor`, `rounded`, `shape`, `fontSize`, etc.) exactly as-is.

### Checklist before returning refactored XML

- [ ] No `childLayout=stackLayout` anywhere in the file
- [ ] No `resizeParent=1` anywhere in the file
- [ ] All edges use `edgeStyle=none`
- [ ] No `orthogonalLoop`, `jettySize`, `exitX/Y`, `entryX/Y` in any edge style
- [ ] All `id` attributes unchanged
- [ ] All `source`/`target` attributes unchanged
- [ ] All `mxGeometry` x/y/width/height unchanged
- [ ] Output is valid XML (well-formed, no unclosed tags)

### Example — before and after

```xml
<!-- BEFORE: auto-layout swimlane with orthogonal edges -->
<mxCell id="sw1" value="Layer"
        style="swimlane;childLayout=stackLayout;resizeParent=1;fillColor=#dae8fc;"
        vertex="1" parent="1">
  <mxGeometry x="50" y="50" width="300" height="200" as="geometry" />
</mxCell>
<mxCell id="e1" value=""
        style="edgeStyle=orthogonalEdgeStyle;orthogonalLoop=1;jettySize=auto;exitX=0.5;exitY=1;"
        edge="1" source="box1" target="box2" parent="1">
  <mxGeometry relative="1" as="geometry" />
</mxCell>

<!-- AFTER: manual layout, stable edges -->
<mxCell id="sw1" value="Layer"
        style="swimlane;fillColor=#dae8fc;"
        vertex="1" parent="1">
  <mxGeometry x="50" y="50" width="300" height="200" as="geometry" />
</mxCell>
<mxCell id="e1" value=""
        style="edgeStyle=none;"
        edge="1" source="box1" target="box2" parent="1">
  <mxGeometry relative="1" as="geometry" />
</mxCell>
```
