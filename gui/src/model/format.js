// Small pure helpers shared by the data source and the view-model.
export const fmtClock = (s) => {
  const n = Number.isFinite(s) ? s : 0
  const m = Math.floor(n / 60)
  return m + ':' + String(n % 60).padStart(2, '0')
}
export const rhex = () => {
  let s = ''
  for (let i = 0; i < 8; i++) s += Math.floor(Math.random() * 16).toString(16)
  return s
}
export const pick = (a) => a[Math.floor(Math.random() * a.length)]
