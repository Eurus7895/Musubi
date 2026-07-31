// Derived numbers for the session timeline's request rows.
//
// Pure and in a .js module so `node --test` can import it — the console suite
// reads .jsx as text, because nothing in the test path transforms JSX, so a
// helper left inline in a component can only ever be asserted by spelling.

/**
 * How heavy a worker was relative to the heaviest worker in the same request.
 * 1 marks that peak, which is the row worth finding when a turn cost more than
 * expected.
 *
 * The denominator is the request, not the session: the comparison an operator
 * makes is "which of these workers cost this turn", and a session-wide peak
 * flattens every row of a cheap request to nearly nothing.
 *
 * Returns 0 when there is nothing to compare against — a single worker, or a
 * request that burned no tokens — so a lone row is never marked the peak of
 * itself.
 */
export function tokenShareOf(agent, siblings) {
  if (!Array.isArray(siblings) || siblings.length < 2) return 0
  const peak = siblings.reduce((max, item) => Math.max(max, Number(item?.tokens) || 0), 0)
  if (peak <= 0) return 0
  return Math.max(0, Math.min(1, (Number(agent?.tokens) || 0) / peak))
}
