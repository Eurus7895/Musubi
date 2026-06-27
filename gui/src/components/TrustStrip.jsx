// Top trust strip: wordmark + the Hard Invariants pills + active model.
const pill = (label) => (
  <span key={label} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 10.5, color: '#9b9ba2', background: 'rgba(84,199,154,0.09)', border: '1px solid rgba(84,199,154,0.22)', padding: '3px 8px', borderRadius: 20, whiteSpace: 'nowrap', flexShrink: 0 }}>
    <span style={{ width: 6, height: 6, borderRadius: '50%', background: '#54c79a' }} />{label}
  </span>
)

export default function TrustStrip({ activeModel }) {
  return (
    <div style={{ height: 46, flexShrink: 0, background: '#111721', borderBottom: '1px solid rgba(255,255,255,0.06)', display: 'flex', alignItems: 'center', padding: '0 16px', gap: 13 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexShrink: 0 }}>
        <span style={{ fontWeight: 700, fontSize: 14, letterSpacing: '0.02em' }}>Musubi</span>
        <span style={{ fontSize: 12, color: '#7a7a82' }}>結び</span>
        <span style={{ fontSize: 10.5, color: '#5a5a62', fontFamily: "'IBM Plex Mono',monospace" }}>tie agents to policy</span>
      </div>
      <div style={{ width: 1, height: 18, background: 'rgba(255,255,255,0.08)', flexShrink: 0 }} />
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'nowrap', minWidth: 0, overflow: 'hidden' }}>
        {['zero-LLM substrate', 'fail-closed policy', 'append-only audit', 'evaluator firewall'].map(pill)}
      </div>
      <div style={{ flex: 1 }} />
      <div style={{ display: 'flex', alignItems: 'center', gap: 14, flexShrink: 0, fontFamily: "'IBM Plex Mono',monospace", fontSize: 11, color: '#6a6a72' }}>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <svg viewBox="0 0 24 24" width="13" height="13" fill="none"><path d="M4 7 L9 4 L15 7 L20 4 V17 L15 20 L9 17 L4 20 Z M9 4 V17 M15 7 V20" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" /></svg>
          musubi · dev
        </span>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, color: '#ff9b3d' }}>{activeModel}</span>
      </div>
    </div>
  )
}
