// Woven-net orchestration graphic — port of the prototype's buildNet().
// The driver knot (top-center) drops amber tie-threads into a sagging diamond
// net; each governed sub-agent hangs from the net, running ones lit amber.
export default function NetGraphic({ shown }) {
  const W = 1000, H = 120, C = 8, R = 3, topY = 16, gap = 23, maxSag = 30, L = 34, Rt = 966
  const X = (c) => L + (Rt - L) * c / C
  const Y = (r, c) => topY + r * gap + (r / R) * maxSag * Math.sin(Math.PI * c / C)

  const P = []
  for (let r = 0; r <= R; r++) {
    const row = []
    for (let c = 0; c <= C; c++) row.push([X(c), Y(r, c)])
    P.push(row)
  }

  const els = []
  let k = 0
  const line = (a, b, stroke, sw, extra) =>
    els.push(<line key={k++} x1={a[0]} y1={a[1]} x2={b[0]} y2={b[1]} stroke={stroke} strokeWidth={sw} strokeLinecap="round" vectorEffect="non-scaling-stroke" {...(extra || {})} />)

  // woven diamonds
  for (let r = 0; r < R; r++) for (let c = 0; c < C; c++) {
    line(P[r][c], P[r + 1][c + 1], 'rgba(255,255,255,0.12)', 1)
    line(P[r + 1][c], P[r][c + 1], 'rgba(255,255,255,0.12)', 1)
  }
  // horizontal ropes (top + bottom heavier)
  for (let r = 0; r <= R; r++) for (let c = 0; c < C; c++) {
    const top = r === 0, bot = r === R
    line(P[r][c], P[r][c + 1], top ? 'rgba(255,255,255,0.32)' : (bot ? 'rgba(255,255,255,0.22)' : 'rgba(255,255,255,0.08)'), (top || bot) ? 1.5 : 0.9)
  }
  // knots
  for (let r = 0; r <= R; r++) for (let c = 0; c <= C; c++)
    els.push(<circle key={k++} cx={P[r][c][0]} cy={P[r][c][1]} r={1.7} fill={(r === 0 || r === R) ? '#9b9ba2' : '#54545c'} />)
  // amber tie threads from the knot (driver, top-center) into the net
  ;[3, 4, 5].forEach((c) => line([500, 0], P[0][c], '#ff9b3d', 1.3, { opacity: 0.8 }))
  els.push(<circle key={k++} cx={500} cy={1} r={2.6} fill="#ff9b3d" />)
  // each card hangs from the net; running agent lit amber
  const cols = [1.33, 4, 6.67]
  ;(shown || []).forEach((a, i) => {
    const c = cols[i], cx = X(c), top = [cx, Y(R, c)], running = a.status === 'running'
    line(top, [cx, H], running ? '#ff9b3d' : 'rgba(255,255,255,0.28)', running ? 1.7 : 1.1, running ? {} : { strokeDasharray: '2 4' })
    els.push(<circle key={k++} cx={cx} cy={top[1]} r={2.6} fill={running ? '#ff9b3d' : '#8a8a92'} />)
  })

  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} preserveAspectRatio="none" style={{ display: 'block', overflow: 'visible' }}>
      {els}
    </svg>
  )
}
