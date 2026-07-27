// Top trust strip: wordmark + live evidence for each Hard Invariant.
//
// These were four hard-coded strings — invariants, not state — sitting in the
// most valuable strip in the window. Because they never changed they read as
// decoration, and they burned the success colour so a real green result had no
// impact left. Same four claims, but each one is now a counter that moves, so
// a deny is visible the moment it lands.
const counter = ({ key, label, value, ok }) => (
  <span key={key} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, whiteSpace: 'nowrap', flexShrink: 0 }}>
    <span style={{ color: ok ? 'var(--ok)' : 'var(--danger)' }}>●</span>
    {label}{' '}
    <b style={{ color: 'var(--text)', fontWeight: 500 }}>{value}</b>
  </span>
)

export default function TrustStrip({ vals }) {
  const profiles = vals.profiles || []
  const hasActiveOption = profiles.some((p) => p.name === vals.activeProfileName)
  return (
    <div style={{ height: 44, flexShrink: 0, background: '#111721', borderBottom: '1px solid var(--line)', display: 'flex', alignItems: 'center', padding: '0 16px', gap: 14 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexShrink: 0 }}>
        <span style={{ fontWeight: 700, fontSize: 14, letterSpacing: '0.02em' }}>Musubi</span>
      </div>
      <div style={{ width: 1, height: 16, background: 'rgba(255,255,255,0.08)', flexShrink: 0 }} />
      <div style={{ display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'nowrap', minWidth: 0, overflow: 'hidden', fontFamily: 'var(--mono)', fontSize: 'var(--fs-2)', color: 'var(--text-2)' }}>
        {(vals.trustCounters || []).map(counter)}
      </div>
      <div style={{ flex: 1 }} />
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexShrink: 0, fontFamily: 'var(--mono)', fontSize: 'var(--fs-2)', color: 'var(--text-3)' }}>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <svg viewBox="0 0 24 24" width="13" height="13" fill="none"><path d="M4 7 L9 4 L15 7 L20 4 V17 L15 20 L9 17 L4 20 Z M9 4 V17 M15 7 V20" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" /></svg>
          {vals.runtimeSourceLabel}
        </span>
        {/* A profile picker is a thing, not an alert — so it is no longer
            painted in the colour reserved for "look here". */}
        <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <span>profile</span>
          <select
            value={vals.activeProfileName}
            onChange={(e) => {
              const selected = profiles.find((p) => p.name === e.target.value)
              selected?.onSelect()
            }}
            style={{
              maxWidth: 280,
              background: 'var(--raised)',
              border: '1px solid var(--line-strong)',
              borderRadius: 'var(--r-sm)',
              color: '#cfd6e0',
              fontFamily: 'var(--mono)',
              fontSize: 'var(--fs-2)',
              padding: '5px 28px 5px 8px',
              outline: 'none',
            }}
          >
            {!hasActiveOption && (
              <option value={vals.activeProfileName}>{vals.activeProfileName || vals.activeModel}</option>
            )}
            {profiles.map((p) => (
              <option key={p.name} value={p.name}>{p.name} · {p.model}</option>
            ))}
          </select>
        </label>
      </div>
    </div>
  )
}
