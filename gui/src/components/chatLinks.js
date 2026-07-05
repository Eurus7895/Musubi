export function parseInlineSegments(text) {
  const source = String(text || '')
  const pattern = /(\[[^\]]+\]\([^)]+\)|\*\*[^*]+\*\*)/g
  const parts = []
  let cursor = 0
  for (const match of source.matchAll(pattern)) {
    if (match.index > cursor) {
      parts.push({ type: 'text', text: source.slice(cursor, match.index) })
    }
    const token = match[0]
    const link = token.match(/^\[([^\]]+)\]\(([^)]+)\)$/)
    if (link) {
      parts.push({ type: 'link', label: link[1], href: link[2] })
    } else {
      parts.push({ type: 'strong', text: token.slice(2, -2) })
    }
    cursor = match.index + token.length
  }
  if (cursor < source.length) {
    parts.push({ type: 'text', text: source.slice(cursor) })
  }
  return parts
}
