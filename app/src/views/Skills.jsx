import Box from '../lib/Box.jsx'
import { cssToObj } from '../lib/css.js'

export default function Skills({ vals }) {
  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, overflow: 'auto' }}>
      <div style={{ padding: '22px 26px 6px' }}>
        <div style={{ fontSize: 18, fontWeight: 600 }}>Skill catalog</div>
        <div style={{ fontSize: 12, color: '#6a6a72', marginTop: 3, fontFamily: "'IBM Plex Mono',monospace" }}>.github/skills/&lt;name&gt;/SKILL.md · pushed to pipeline agents · pulled on demand via musubi_get_skill</div>
      </div>
      <div style={{ background: 'rgba(255,155,61,0.05)', border: '1px solid rgba(255,155,61,0.2)', borderRadius: 10, padding: '11px 15px', margin: '14px 26px 4px', fontSize: 12, color: '#9b9ba2' }}>Decision rule — <span style={{ color: '#ffba75', fontWeight: 600 }}>default to skill, not agent.</span> Skills are the cheapest optimisation surface; agents are medium-cost; multi-agent topologies are a dissolving pattern.</div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 12, padding: '16px 26px 26px' }}>
        {vals.skills.map((s) => (
          <Box key={s.name} css="background:#141b27;border:1px solid rgba(255,255,255,0.06);border-radius:10px;padding:14px 15px" hover="border-color:rgba(255,255,255,0.14)">
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 9 }}>
              <span style={cssToObj(s.modeStyle)}>{s.mode}</span>
              <span style={{ fontFamily: "'IBM Plex Mono',monospace", fontSize: 10, color: '#6a6a72' }}>applies-to: {s.appliesTo}</span>
            </div>
            <div style={{ fontFamily: "'IBM Plex Mono',monospace", fontSize: 13, color: '#fff', marginBottom: 6 }}>{s.name}</div>
            <div style={{ fontSize: 11.5, color: '#9b9ba2', lineHeight: 1.45 }}>{s.desc}</div>
          </Box>
        ))}
      </div>
    </div>
  )
}
