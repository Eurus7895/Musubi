function fmt(value) {
  return new Intl.NumberFormat('en-US').format(Number(value) || 0)
}

export default function TokenEconomics({ economics }) {
  const data = economics || {}
  const metrics = [
    ['input', fmt(data.inputTokens)],
    ['cached input', fmt(data.cachedInputTokens)],
    ['output', fmt(data.outputTokens)],
    ['LM time', `${fmt(data.lmMs)} ms`],
  ]
  return (
    <div style={{ marginTop: 11, borderTop: '1px solid rgba(255,255,255,0.07)', paddingTop: 10 }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,minmax(0,1fr))', gap: 7 }}>
        {metrics.map(([label, value]) => (
          <div key={label} style={{ minWidth: 0, textAlign: 'center' }}>
            <div style={{ fontFamily: "'IBM Plex Mono',monospace", fontSize: 10.5, color: '#f4f4f5', overflow: 'hidden', textOverflow: 'ellipsis' }}>{value}</div>
            <div style={{ marginTop: 2, fontSize: 9, color: '#6f7785', textTransform: 'uppercase', letterSpacing: '0.04em' }}>{label}</div>
          </div>
        ))}
      </div>
      <div style={{ marginTop: 8, textAlign: 'center', fontFamily: "'IBM Plex Mono',monospace", fontSize: 9.5, color: '#8ab4d8' }}>
        {fmt(data.cycles)} audited cycles · {data.tokenSource || 'estimated'} tokens
      </div>
      {!!data.tools?.length && (
        <div style={{ marginTop: 5, textAlign: 'center', fontFamily: "'IBM Plex Mono',monospace", fontSize: 9.5, color: '#7a7a82', overflowWrap: 'anywhere' }}>
          {data.tools.map((tool) => `${tool.name}×${tool.count}`).join(' · ')}
        </div>
      )}
    </div>
  )
}
