function fmt(value) {
  return new Intl.NumberFormat('en-US').format(Number(value) || 0)
}

// Was a four-column strip of bare numerals with 9px labels beneath them; on a
// sparse run it read as four unlabelled zeros. It is a labelled ledger now:
// each row names the quantity in language and puts the figure in mono, where
// the eye expects to compare numbers.
export default function TokenEconomics({ economics }) {
  const data = economics || {}
  const input = Number(data.inputTokens) || 0
  const cached = Number(data.cachedInputTokens) || 0
  const rows = [
    ['tokens in / out', `${fmt(input)} / ${fmt(data.outputTokens)}`, null],
    ['cached', fmt(cached), input ? `${Math.round((cached / input) * 100)}%` : null],
    ['LM time', `${((Number(data.lmMs) || 0) / 1000).toFixed(1)} s`, null],
    ['audited cycles', fmt(data.cycles), data.tokenSource || 'estimated'],
  ]
  return (
    <div className="session-economics">
      <strong>This session</strong>
      {rows.map(([label, value, note]) => (
        <div key={label}>
          <span>{label}</span>
          <span><b>{value}</b>{note ? <> <em>{note}</em></> : null}</span>
        </div>
      ))}
      {!!data.tools?.length && (
        <div><span>tools</span><span><b>{data.tools.map((tool) => `${tool.name}×${tool.count}`).join(' · ')}</b></span></div>
      )}
    </div>
  )
}
