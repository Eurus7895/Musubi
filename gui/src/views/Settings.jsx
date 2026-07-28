export default function Settings({ vals }) {
  return (
    <div style={{ flex: 1, minHeight: 0, overflow: 'auto', padding: '22px 26px 28px', background: '#0d1117' }}>
      <div style={{ maxWidth: 980, display: 'flex', flexDirection: 'column', gap: 18 }}>
        <header>
          <div style={{ fontSize: 20, fontWeight: 700, color: '#f2f2f3' }}>First run</div>
          <div style={{ fontSize: 12, color: '#7a7a82', marginTop: 5 }}>Core runtime, model profile, and audit database discovery.</div>
        </header>

        <section style={{ border: '1px solid rgba(84,199,154,0.25)', background: '#141b27', borderRadius: 8, padding: '16px', display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div>
            <div style={{ fontSize: 14, fontWeight: 650, color: '#f2f2f3' }}>Application workspace</div>
            <div style={{ fontSize: 12, color: '#8b929e', marginTop: 4, lineHeight: 1.5 }}>
              Choose the folder Musubi may inspect and update. The Console restarts so sessions, audit, pipelines, and agent commands all move to the same boundary.
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <div title={vals.workspaceRoot} style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', border: '1px solid rgba(255,255,255,0.1)', background: '#0d1117', borderRadius: 6, padding: '9px 11px', fontFamily: "'IBM Plex Mono',monospace", fontSize: 11, color: '#d4d4d8' }}>
              {vals.workspaceRoot || 'No workspace selected'}
            </div>
            <button type="button" disabled={vals.workspaceSwitchDisabled} onClick={vals.onChooseWorkspace} style={{ border: '1px solid rgba(84,199,154,0.45)', background: vals.workspaceSwitchDisabled ? '#202631' : 'rgba(84,199,154,0.14)', color: vals.workspaceSwitchDisabled ? '#737984' : '#8be0bd', borderRadius: 6, padding: '9px 15px', fontSize: 12, fontWeight: 650, cursor: vals.workspaceSwitchDisabled ? 'not-allowed' : 'pointer' }}>
              {vals.workspaceSwitching ? 'Switching…' : 'Choose folder'}
            </button>
          </div>
          {vals.workspaceError ? <div role="alert" style={{ color: '#ff8b8b', fontSize: 12 }}>{vals.workspaceError}</div> : null}
          {vals.driverBusy ? <div style={{ color: '#e8d49b', fontSize: 11 }}>Finish or cancel the active agent before switching workspace.</div> : null}
        </section>

        <section style={{ border: '1px solid rgba(255,255,255,0.08)', background: '#141b27', borderRadius: 8, overflow: 'hidden' }}>
          {vals.setupRows.map((row, idx) => (
            <div key={row.label} style={{ display: 'grid', gridTemplateColumns: '150px 1fr auto', gap: 14, alignItems: 'center', padding: '13px 16px', borderTop: idx === 0 ? 'none' : '1px solid rgba(255,255,255,0.06)' }}>
              <div style={{ fontSize: 12, color: '#9b9ba2' }}>{row.label}</div>
              <div style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontFamily: "'IBM Plex Mono',monospace", fontSize: 11, color: row.ok ? '#d4d4d8' : '#e3b341' }}>{row.value}</div>
              <span style={cssToObj(row.badgeStyle)}>{row.badge}</span>
            </div>
          ))}
        </section>

        {vals.setupPathHint ? (
          <div style={{ border: '1px solid rgba(227,179,65,0.28)', background: 'rgba(227,179,65,0.08)', borderRadius: 8, padding: '12px 14px', color: '#e8d49b', fontSize: 12, lineHeight: 1.5 }}>
            {vals.setupPathHint}
          </div>
        ) : null}
      </div>
    </div>
  )
}

function cssToObj(css) {
  return Object.fromEntries(css.split(';').filter(Boolean).map((p) => {
    const [k, ...v] = p.split(':')
    return [k.trim().replace(/-([a-z])/g, (_, c) => c.toUpperCase()), v.join(':').trim()]
  }))
}
