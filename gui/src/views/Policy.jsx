import { cssToObj } from '../lib/css.js'

export default function Policy({ vals }) {
  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, overflow: 'auto' }}>
      <div style={{ padding: '22px 26px 6px' }}>
        <div style={{ fontSize: 18, fontWeight: 600 }}>Policy engine</div>
        <div style={{ fontSize: 12, color: '#6a6a72', marginTop: 3, fontFamily: "'IBM Plex Mono',monospace" }}>fail-closed · PreToolUse gate · denies unknown (agent, tool) · exit 0 = allow, 1 = deny</div>
      </div>
      <div style={{ display: 'flex', gap: 12, padding: '14px 26px 4px' }}>
        <div style={{ flex: 1, background: '#141b27', border: '1px solid rgba(255,255,255,0.06)', borderRadius: 10, padding: '14px 16px' }}><div style={{ fontFamily: "'IBM Plex Mono',monospace", fontSize: 22, fontWeight: 600, color: '#54c79a' }}>{vals.allowCount}</div><div style={{ fontSize: 11, color: '#6a6a72', marginTop: 2 }}>allowed this session</div></div>
        <div style={{ flex: 1, background: '#141b27', border: '1px solid rgba(255,255,255,0.06)', borderRadius: 10, padding: '14px 16px' }}><div style={{ fontFamily: "'IBM Plex Mono',monospace", fontSize: 22, fontWeight: 600, color: '#e86a5f' }}>{vals.denyCount}</div><div style={{ fontSize: 11, color: '#6a6a72', marginTop: 2 }}>denied · fail-closed</div></div>
        {/* Was hard-coded to 4 while its two neighbours were live — a
            credibility leak in the one view that sells credibility. */}
        <div style={{ flex: 1, background: '#141b27', border: '1px solid rgba(255,255,255,0.06)', borderRadius: 10, padding: '14px 16px' }}><div style={{ fontFamily: "'IBM Plex Mono',monospace", fontSize: 22, fontWeight: 600, color: '#e9e9ea' }}>{vals.policyRoles.length}</div><div style={{ fontSize: 11, color: '#6a6a72', marginTop: 2 }}>policy roles defined</div></div>
      </div>
      <div style={{ display: 'flex', gap: 18, padding: '14px 26px 26px', alignItems: 'flex-start', minHeight: 0 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.09em', color: '#6a6a72', marginBottom: 10 }}>Live PreToolUse decisions</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {vals.policy.map((d, i) => (
              <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 12, background: '#141b27', border: '1px solid rgba(255,255,255,0.06)', borderRadius: 9, padding: '10px 14px', animation: 'rowin .25s ease' }}>
                <span style={{ fontFamily: "'IBM Plex Mono',monospace", fontSize: 10, color: '#5a5a62', width: 52, flexShrink: 0 }}>{d.ts}</span>
                <span style={cssToObj(d.verdictStyle)}>{d.verdict}</span>
                <span style={{ fontFamily: "'IBM Plex Mono',monospace", fontSize: 12, color: '#d4d4d8', flexShrink: 0 }}>{d.tool}</span>
                <span style={cssToObj(d.roleChipStyle)}>{d.role}</span>
                <span style={{ fontSize: 11, color: '#6a6a72', marginLeft: 'auto', textAlign: 'right', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{d.reason}</span>
              </div>
            ))}
          </div>
        </div>
        <div style={{ width: 340, flexShrink: 0 }}>
          <div style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.09em', color: '#6a6a72', marginBottom: 10 }}>Tool surface by role</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 9 }}>
            {vals.policyRoles.map((r) => (
              <div key={r.role} style={{ background: '#141b27', border: '1px solid rgba(255,255,255,0.06)', borderRadius: 10, padding: '12px 14px' }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 7 }}><span style={cssToObj(r.chipStyle)}>{r.role}</span><span style={{ fontFamily: "'IBM Plex Mono',monospace", fontSize: 10, color: '#6a6a72' }}>{r.scope}</span></div>
                <div style={{ fontFamily: "'IBM Plex Mono',monospace", fontSize: 11, color: '#b8b8be', lineHeight: 1.6 }}>{r.tools}</div>
              </div>
            ))}
            <div style={{ background: 'rgba(255,155,61,0.06)', border: '1px solid rgba(255,155,61,0.25)', borderRadius: 10, padding: '12px 14px', display: 'flex', gap: 10 }}>
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" style={{ flexShrink: 0, color: '#ff9b3d' }}><path d="M12 3 L19 6 V11 C19 15.5 15.7 18.6 12 20.5 C8.3 18.6 5 15.5 5 11 V6 Z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" /></svg>
              <div><div style={{ fontSize: 11.5, color: '#ffba75', fontWeight: 600, marginBottom: 2 }}>Evaluator firewall · HI #3</div><div style={{ fontSize: 11, color: '#9b9ba2', lineHeight: 1.45 }}>The reviewer sees <span style={{ color: '#cfcfd4' }}>code only</span> — no request, plan, design, or memory. Enforced in the substrate, not the prompt.</div></div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
