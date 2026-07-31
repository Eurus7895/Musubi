import { useMemo, useState } from 'react'

const STEPS = ['basics', 'stages', 'handoffs', 'validate']
const STEP_LABELS = { basics: 'Basics', stages: 'Stages', handoffs: 'Handoffs', validate: 'Validate' }

export default function Pipeline({ vals }) {
  const builder = vals.pipelineBuilder
  const actions = builder.actions
  const draft = builder.draft || { stages: [] }
  const activeStep = STEPS.includes(builder.step) ? builder.step : 'basics'
  const [query, setQuery] = useState('')
  const library = useMemo(() => {
    const needle = query.trim().toLowerCase()
    const matches = (item) => !needle || `${item.id || ''} ${item.name || ''} ${item.displayLabel || ''} ${item.agent || ''}`.toLowerCase().includes(needle)
    return {
      presets: (builder.library?.presets || []).filter(matches),
      agents: (builder.library?.agents || []).filter(matches),
      spawnRoles: builder.library?.spawnRoles || [],
    }
  }, [builder.library, query])
  const clientErrors = []
  if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(draft.name || '')) clientErrors.push('Use a lowercase safe pipeline name.')
  if ((draft.stages || []).length < 2) clientErrors.push('Add at least two primary stages.')
  const hasErrors = clientErrors.length > 0 || (builder.findings || []).some((finding) => finding.severity === 'error')

  return (
    <main className="pipeline-studio">
      <header className="pipeline-studio__header">
        <div>
          <div className="pipeline-studio__eyebrow">Recipe workspace</div>
          <div className="pipeline-studio__title-row">
            <h1>Pipeline Studio</h1>
            <span className="pipeline-studio__name">{draft.name || 'untitled-pipeline'}</span>
            {builder.dirty && <span className="pipeline-studio__dirty">Unsaved</span>}
          </div>
          <p>Build a governed sequential recipe. Execution happens in Orchestrator.</p>
        </div>
        <div className="pipeline-studio__actions">
          <OpenRecipe saved={builder.saved || []} loading={builder.loading} onLoad={actions.onLoad} />
          <button className="ui-button" onClick={actions.onClone} disabled={builder.loading || !draft.name}>Clone</button>
          {/* Repository-owned recipes carry a musubi-tier tag and the backend
              refuses to delete them; say so on the button rather than after
              the click. Confirm first either way — this removes a directory. */}
          <button
            className="ui-button ui-button--danger"
            onClick={() => { if (confirmRemoval(builder.savedRecipe?.name)) actions.onDelete(builder.savedRecipe.name) }}
            disabled={builder.loading || !builder.deletable}
            title={builder.savedRecipe?.name && !builder.deletable
              ? 'Repository-owned recipe — delete it in git, not here'
              : 'Remove this pipeline from .github/pipelines'}
          >
            Remove
          </button>
          <button className="ui-button" onClick={actions.onNew}>＋ New Pipeline</button>
          <button className="ui-button ui-button--primary" onClick={actions.onSave} disabled={builder.loading || hasErrors}>Save Pipeline</button>
        </div>
      </header>

      <nav className="pipeline-steps" aria-label="Pipeline builder steps">
        {STEPS.map((step, index) => (
          <button key={step} className={step === activeStep ? 'pipeline-step is-active' : 'pipeline-step'} onClick={() => actions.onSelectStep(step)}>
            <span>{String(index + 1).padStart(2, '0')}</span>{STEP_LABELS[step]}
          </button>
        ))}
      </nav>

      <section className="pipeline-studio__body">
        {activeStep === 'basics' && <Basics draft={draft} onUpdateRecipe={actions.onUpdateRecipe} />}
        {activeStep === 'stages' && (
          <Stages
            builder={builder} draft={draft} library={library} query={query} setQuery={setQuery}
            onAddStage={actions.onAddStage} onMoveStage={actions.onMoveStage}
            onRemoveStage={actions.onRemoveStage} onSelectStage={actions.onSelectStage}
            onUpdateStage={actions.onUpdateStage}
          />
        )}
        {activeStep === 'handoffs' && (
          <Handoffs
            draft={draft} agents={library.spawnRoles} onAddSpawn={actions.onAddSpawn}
            onRemoveSpawn={actions.onRemoveSpawn}
          />
        )}
        {activeStep === 'validate' && (
          <Validate
            draft={draft} findings={builder.findings || []} clientErrors={clientErrors}
            saveResult={builder.saveResult} loading={builder.loading} onValidate={actions.onValidate}
            onSave={actions.onSave} hasErrors={hasErrors}
          />
        )}
      </section>

      {builder.pendingTransition && (
        <div className="pipeline-confirm" role="dialog" aria-modal="true" aria-label="Unsaved pipeline changes">
          <div className="pipeline-confirm__card">
            <div className="pipeline-studio__eyebrow">Unsaved changes</div>
            <h2>Discard this draft?</h2>
            <p>The current recipe has local edits that have not been saved.</p>
            <div className="pipeline-confirm__actions">
              <button className="ui-button" onClick={actions.onCancelTransition}>Keep editing</button>
              <button className="ui-button ui-button--danger" onClick={actions.onConfirmTransition}>Discard changes</button>
            </div>
          </div>
        </div>
      )}
    </main>
  )
}

function Basics({ draft, onUpdateRecipe }) {
  const correction = draft.correction || {}
  return (
    <div className="builder-panel builder-panel--narrow" data-step="basics">
      <PanelHeading title="Recipe identity" copy="These fields belong to pipeline.yaml and are validated before save." />
      <div className="builder-form-grid">
        <Field label="Pipeline name" hint="lowercase, digits and hyphens">
          <input value={draft.name || ''} onChange={(event) => onUpdateRecipe({ name: event.target.value })} placeholder="feature-dev" />
        </Field>
        <Field label="Version"><input value={draft.version || ''} onChange={(event) => onUpdateRecipe({ version: event.target.value })} placeholder="1" /></Field>
        <Field label="Description" wide><textarea value={draft.description || ''} onChange={(event) => onUpdateRecipe({ description: event.target.value })} rows={3} /></Field>
        <Field label="Baseline checks" hint="one deterministic command per line" wide>
          <textarea value={(draft.baselineChecks || []).join('\n')} onChange={(event) => onUpdateRecipe({ baselineChecks: event.target.value.split('\n').map((line) => line.trim()).filter(Boolean) })} rows={4} placeholder="npm test" />
        </Field>
        <Field label="Correction attempts" hint="0 disables correction">
          <input type="number" min="0" value={correction.max_retries || 0} onChange={(event) => onUpdateRecipe({ correction: { ...correction, max_retries: Number(event.target.value) } })} />
        </Field>
      </div>
    </div>
  )
}

function confirmRemoval(name) {
  return !!name && window.confirm(
    `Remove pipeline "${name}"? Its directory under .github/pipelines is deleted. Audit history is unaffected.`,
  )
}

// `loadPipelineRecipe` shipped with the first version of the Studio and nothing
// ever rendered it, so a recipe could be saved but never reopened — which is
// what made the shipped presets look read-only. A select is enough: the list is
// short, and switching already routes through the unsaved-changes guard.
function OpenRecipe({ saved, loading, onLoad }) {
  const open = saved.find((entry) => entry.open)
  return (
    <label className="pipeline-open">
      <span>Open</span>
      <select
        value={open?.name || ''}
        disabled={loading || !saved.length}
        onChange={(event) => { if (event.target.value) onLoad(event.target.value) }}
      >
        <option value="">{saved.length ? 'Select a recipe…' : 'No saved recipes'}</option>
        {saved.map((entry) => (
          <option key={entry.name} value={entry.name}>
            {entry.name}{entry.protected ? ' · repository' : ''}
          </option>
        ))}
      </select>
    </label>
  )
}

function Stages({ builder, draft, library, query, setQuery, onAddStage, onMoveStage, onRemoveStage, onSelectStage, onUpdateStage }) {
  const selectedIndex = builder.selectedStageIndex
  const selected = draft.stages?.[selectedIndex]
  const selectedPreset = (builder.library?.presets || []).find((item) => item.id === selected?.preset)
  const resolvedAgentName = selected?.agent || selectedPreset?.agent
  const contract = (builder.library?.agents || []).find((item) => item.name === resolvedAgentName)
  const readPayload = (event) => {
    try { return JSON.parse(event.dataTransfer.getData('application/x-musubi-stage')) } catch { return null }
  }
  return (
    <div className="stages-workspace" data-step="stages">
      <aside className="agent-library">
        <PanelHeading title="Preset / Agent Library" copy="Catalog-owned contracts are resolved read-only." />
        <input className="library-search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search presets and agents…" />
        <LibraryGroup title="Presets" items={library.presets} kind="preset" onAddStage={onAddStage} />
        <LibraryGroup title="Agents" items={library.agents} kind="agent" onAddStage={onAddStage} />
      </aside>
      <section className="stage-lane" onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); const payload = readPayload(event); if (payload) onAddStage(payload) }}>
        <PanelHeading title="Ordered primary stages" copy="Primary stages execute sequentially in this order." />
        {!draft.stages?.length && <div className="empty-drop">Drop a runnable preset or agent here</div>}
        {(draft.stages || []).map((stage, index) => (
          <article
            key={`${stage.preset || stage.agent}-${index}`}
            className={selectedIndex === index ? 'stage-card is-selected' : 'stage-card'}
            draggable onDragStart={(event) => event.dataTransfer.setData('application/x-musubi-index', String(index))}
            onDragOver={(event) => event.preventDefault()}
            onDrop={(event) => { event.preventDefault(); event.stopPropagation(); const raw = event.dataTransfer.getData('application/x-musubi-index'); if (!raw) return; const from = Number(raw); if (Number.isInteger(from)) onMoveStage(from, index) }}
            onClick={() => onSelectStage(index)}
          >
            <span className="stage-card__handle">⠿</span><span className="stage-card__index">{String(index + 1).padStart(2, '0')}</span>
            <div><strong>{stage.preset || stage.agent || 'unresolved'}</strong><small>{stage.stage || 'default stage name'}</small></div>
            <span className="stage-card__spawns">{stage.spawns?.length || 0} helpers</span>
            <button aria-label="Move stage earlier" disabled={index === 0} onClick={(event) => { event.stopPropagation(); onMoveStage(index, index - 1) }}>↑</button>
            <button aria-label="Move stage later" disabled={index === draft.stages.length - 1} onClick={(event) => { event.stopPropagation(); onMoveStage(index, index + 1) }}>↓</button>
            <button className="danger-icon" aria-label="Remove stage" onClick={(event) => { event.stopPropagation(); onRemoveStage(index) }}>×</button>
          </article>
        ))}
      </section>
      <aside className="stage-inspector">
        <PanelHeading title="Stage inspector" copy={selected ? `Stage ${selectedIndex + 1}` : 'Select a stage to inspect'} />
        {!selected ? <div className="empty-inspector">No stage selected.</div> : <>
          <Field label="Stage override"><input value={selected.stage || ''} onChange={(event) => onUpdateStage(selectedIndex, { stage: event.target.value })} placeholder="catalog default" /></Field>
          <div className="contract-card">
            <h3>Resolved contract <span>read-only</span></h3>
            <ContractRow label="Agent" value={contract?.displayLabel || resolvedAgentName || 'unresolved'} />
            <ContractRow label="Role skill" value={contract?.roleSkill || 'none'} />
            <ContractRow label="Allowed tools" value={(contract?.allowedTools || []).join(', ') || 'none'} />
            <ContractRow label="Max turns" value={contract?.maxTurns ?? 'unresolved'} />
            <ContractRow label="Output budget" value={contract?.maxOutputTokens ?? 'profile default'} />
            <ContractRow label="Source" value={(contract?.sourcePaths || []).join(' · ') || 'catalog'} mono />
          </div>
        </>}
      </aside>
    </div>
  )
}

function LibraryGroup({ title, items, kind, onAddStage }) {
  return <div className="library-group"><h3>{title}</h3>{items.map((item) => {
    const blocked = item.blocked || item.runnable === false
    const payload = kind === 'preset' ? { kind, id: item.id } : { kind, agent: item.name }
    return <button
      key={item.id || item.name} className={blocked ? 'library-item is-blocked' : 'library-item'} disabled={blocked}
      draggable={!blocked} onDragStart={(event) => event.dataTransfer.setData('application/x-musubi-stage', JSON.stringify(payload))}
      onClick={() => !blocked && onAddStage(payload)} title={item.blockedReason || ''}
    ><span><strong>{item.id || item.displayLabel || item.name}</strong><small>{item.agent || item.name}</small></span><em>{blocked ? 'blocked' : '+'}</em></button>
  })}</div>
}

function Handoffs({ draft, agents, onAddSpawn, onRemoveSpawn }) {
  const readRole = (event) => {
    try { return JSON.parse(event.dataTransfer.getData('application/x-musubi-spawn')).role } catch { return '' }
  }
  return (
    <div className="builder-panel" data-step="handoffs">
      <PanelHeading title="Handoffs and nested workers" copy="The primary backbone is sequential. Nested roles are allowlists, not guaranteed work." />
      <div className="handoff-layout">
        <aside className="spawn-library"><h3>Spawnable agents</h3>{agents.map((agent) => <button key={agent.name} draggable onDragStart={(event) => event.dataTransfer.setData('application/x-musubi-spawn', JSON.stringify({ role: agent.name }))}>{agent.displayLabel || agent.name}<span>drag</span></button>)}</aside>
        <div className="handoff-chain">
          {(draft.stages || []).map((stage, index) => <div className="handoff-stage" key={`${stage.preset || stage.agent}-${index}`}>
            <div className="handoff-stage__node"><span>{String(index + 1).padStart(2, '0')}</span><strong>{stage.preset || stage.agent}</strong>{index < draft.stages.length - 1 && <em>sequential handoff ↓</em>}</div>
            <div className="spawn-cluster" onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); const role = readRole(event); if (role) onAddSpawn(index, role) }}>
              <label>May spawn</label>
              {(stage.spawns || []).map((role) => <button key={role} onClick={() => onRemoveSpawn(index, role)}>{role} ×</button>)}
              {!stage.spawns?.length && <span>Drop an agent role</span>}
            </div>
          </div>)}
        </div>
      </div>
      <p className="parallel-note">Runs in parallel only when summoned in the same worker turn.</p>
    </div>
  )
}

function Validate({ draft, findings, clientErrors, saveResult, loading, onValidate, onSave, hasErrors }) {
  return (
    <div className="builder-panel" data-step="validate">
      <div className="validate-toolbar"><PanelHeading title="Validate recipe" copy="Backend validation is authoritative and fail-closed." /><div><button className="ui-button" onClick={onValidate} disabled={loading}>Validate</button><button className="ui-button ui-button--primary" onClick={onSave} disabled={loading || hasErrors}>Save Pipeline</button></div></div>
      <div className="validate-grid">
        <section><h3>Final recipe topology</h3><div className="final-topology">{(draft.stages || []).map((stage, index) => <div key={index}><span>{String(index + 1).padStart(2, '0')}</span><strong>{stage.preset || stage.agent || 'unresolved'}</strong><small>{stage.spawns?.length ? `may spawn ${stage.spawns.join(', ')}` : 'no nested workers'}</small></div>)}</div></section>
        <section><h3>Findings</h3>{!clientErrors.length && !findings.length ? <div className="finding finding--ok">No findings. Run backend validation before save.</div> : <>{clientErrors.map((message) => <div className="finding finding--error" key={message}>{message}</div>)}{findings.map((finding, index) => <div className={`finding finding--${finding.severity}`} key={`${finding.field}-${index}`}><strong>{finding.step || 'recipe'} · {finding.field || 'general'}</strong>{finding.message}</div>)}</>}</section>
      </div>
      <div className="yaml-target"><span>YAML target</span><code>.github/pipelines/{draft.name || '&lt;name&gt;'}/pipeline.yaml</code>{saveResult?.saved && <em>{saveResult.catalogRefreshed ? 'saved · catalog refreshed' : 'saved · catalog refresh failed'}</em>}</div>
    </div>
  )
}

function PanelHeading({ title, copy }) { return <div className="panel-heading"><h2>{title}</h2><p>{copy}</p></div> }
function Field({ label, hint, wide, children }) { return <label className={wide ? 'builder-field is-wide' : 'builder-field'}><span>{label}</span>{children}{hint && <small>{hint}</small>}</label> }
function ContractRow({ label, value, mono }) { return <div className="contract-row"><span>{label}</span><strong className={mono ? 'is-mono' : ''}>{value}</strong></div> }
