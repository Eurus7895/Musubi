// musubi-tier: substrate
//
// On-demand task launcher. Opening the GUI never starts an agent; pressing
// Run spawns exactly one governed `agent "<task>"` process (the standalone
// driver). Output below is a bounded runtime overlay — orchestration state
// stays in the audit DB, rendered by the Orchestrator/Audit views.
import { cssToObj } from '../lib/css.js'
import Box from '../lib/Box.jsx'

const mono = "'IBM Plex Mono',monospace"

export default function TaskLauncher({ vals }) {
  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, overflow: 'auto' }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 16, padding: '22px 26px 14px', flexWrap: 'wrap' }}>
        <div style={{ flex: 1, minWidth: 200 }}>
          <div style={{ fontSize: 18, fontWeight: 600 }}>Run task</div>
          <div style={{ fontSize: 12, color: '#6a6a72', marginTop: 4, fontFamily: mono }}>{vals.taskStatusText}</div>
        </div>
        <div style={{ display: 'flex', gap: 9, flexShrink: 0, alignItems: 'center' }}>
          <select
            value={vals.taskProfile}
            onChange={(e) => vals.setTaskProfile(e.target.value)}
            disabled={vals.taskRunning}
            style={{ fontFamily: mono, fontSize: 12, padding: '9px 12px', borderRadius: 9, background: '#19212f', border: '1px solid rgba(255,255,255,0.1)', color: '#e9e9ea', cursor: vals.taskRunning ? 'default' : 'pointer' }}
          >
            {vals.taskProfiles.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
          <button onClick={vals.onRunTask} disabled={vals.taskRunDisabled} style={cssToObj(vals.taskRunStyle)}>{vals.taskRunLabel}</button>
        </div>
      </div>

      <div style={{ padding: '0 26px 14px' }}>
        <textarea
          value={vals.taskDraft}
          onChange={vals.onTaskDraft}
          disabled={vals.taskRunning}
          placeholder='What should the agent do? e.g. "add a health endpoint and tests"'
          rows={4}
          style={{ width: '100%', boxSizing: 'border-box', resize: 'vertical', fontFamily: mono, fontSize: 12.5, lineHeight: 1.5, color: '#e9e9ea', background: '#0f1620', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 12, padding: '12px 14px', outline: 'none', opacity: vals.taskRunning ? 0.6 : 1 }}
        />
        <div style={{ fontSize: 10.5, color: '#6a6a72', marginTop: 6, fontFamily: mono }}>
          spawns: agent "&lt;task&gt;" --tool-surface agent · governed by policy, budget &amp; audit · nothing runs until you press Run
        </div>
      </div>

      <div style={{ flex: 1, minHeight: 0, padding: '0 26px 26px', display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.09em', color: '#6a6a72' }}>Process output</div>
          <div style={{ flex: 1 }} />
          <Box as="button" onClick={vals.onClearTaskOutput} css="font-family:'IBM Plex Mono',monospace;font-size:11px;padding:6px 12px;border-radius:8px;cursor:pointer;background:#19212f;border:1px solid rgba(255,255,255,0.1);color:#9b9ba2" hover="color:#e9e9ea">clear output</Box>
        </div>

        {vals.taskError && (
          <div style={{ fontFamily: mono, fontSize: 11.5, color: '#e86a5f', background: 'rgba(232,106,95,0.08)', border: '1px solid rgba(232,106,95,0.25)', borderRadius: 10, padding: '10px 13px', whiteSpace: 'pre-wrap' }}>{vals.taskError}</div>
        )}

        <div style={{ flex: 1, minHeight: 120, background: '#0b0f16', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 12, padding: '12px 14px', overflow: 'auto' }}>
          {vals.taskStdout
            ? <pre style={{ margin: 0, fontFamily: mono, fontSize: 11.5, lineHeight: 1.5, color: '#cfcfd4', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{vals.taskStdout}</pre>
            : <div style={{ fontFamily: mono, fontSize: 11.5, color: '#4a4a52' }}>stdout — last 64 KiB</div>}
        </div>

        <div style={{ minHeight: 60, maxHeight: 180, background: '#0b0f16', border: '1px solid rgba(232,106,95,0.18)', borderRadius: 12, padding: '12px 14px', overflow: 'auto', flexShrink: 0 }}>
          {vals.taskStderr
            ? <pre style={{ margin: 0, fontFamily: mono, fontSize: 11.5, lineHeight: 1.5, color: '#d8a49e', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{vals.taskStderr}</pre>
            : <div style={{ fontFamily: mono, fontSize: 11.5, color: '#4a4a52' }}>stderr — last 64 KiB</div>}
        </div>
      </div>
    </div>
  )
}
