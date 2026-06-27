import { cssToObj } from '../lib/css.js'

const GRID = '48px 64px 92px 96px 1fr 92px'

export default function Audit({ vals }) {
  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
      <div style={{ padding: '22px 26px 6px', flexShrink: 0 }}>
        <div style={{ fontSize: 18, fontWeight: 600 }}>Audit ledger</div>
        <div style={{ fontSize: 12, color: '#6a6a72', marginTop: 3, fontFamily: "'IBM Plex Mono',monospace" }}>append-only · write-once · no silent sub-agents (HI #8) · storage/audit.db</div>
      </div>
      <div style={{ display: 'flex', gap: 8, padding: '12px 26px', flexShrink: 0 }}>
        <button onClick={vals.setAuditAll} style={cssToObj(vals.auditFAll)}>all</button>
        <button onClick={vals.setAuditSpawn} style={cssToObj(vals.auditFSpawn)}>spawned</button>
        <button onClick={vals.setAuditDone} style={cssToObj(vals.auditFDone)}>completed</button>
        <div style={{ flex: 1 }} />
        <span style={{ fontFamily: "'IBM Plex Mono',monospace", fontSize: 10, color: '#5a5a62', alignSelf: 'center' }}>{vals.auditCountLabel}</span>
      </div>
      <div style={{ flex: 1, overflow: 'auto', padding: '0 26px 26px' }}>
        <div style={{ display: 'grid', gridTemplateColumns: GRID, gap: 0, fontFamily: "'IBM Plex Mono',monospace", fontSize: 10, color: '#5a5a62', textTransform: 'uppercase', letterSpacing: '0.06em', padding: '0 14px 8px', borderBottom: '1px solid rgba(255,255,255,0.08)' }}><span>#</span><span>time</span><span>event</span><span>role</span><span>handle · detail</span><span style={{ textAlign: 'right' }}>status</span></div>
        {vals.auditView.map((r, i) => (
          <div key={i} style={{ display: 'grid', gridTemplateColumns: GRID, gap: 0, alignItems: 'center', padding: '9px 14px', borderBottom: '1px solid rgba(255,255,255,0.04)', animation: 'rowin .25s ease' }}>
            <span style={{ fontFamily: "'IBM Plex Mono',monospace", fontSize: 11, color: '#5a5a62' }}>{r.id}</span>
            <span style={{ fontFamily: "'IBM Plex Mono',monospace", fontSize: 10.5, color: '#7a7a82' }}>{r.ts}</span>
            <span style={cssToObj(r.eventStyle)}>{r.event}</span>
            <span style={cssToObj(r.roleChipStyle)}>{r.role}</span>
            <span style={{ fontFamily: "'IBM Plex Mono',monospace", fontSize: 11, color: '#b8b8be', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}><span style={{ color: '#e9e9ea' }}>{r.handle}</span> · {r.detail}</span>
            <span style={{ textAlign: 'right', fontFamily: "'IBM Plex Mono',monospace", fontSize: 10.5, color: r.statusColor }}>{r.statusLabel}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
