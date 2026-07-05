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

export function compactMarkdownTables(text) {
  const lines = String(text || '').replace(/\r\n/g, '\n').split('\n')
  const out = []
  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i].trim()
    const next = lines[i + 1]?.trim() || ''
    const isHeader = line.startsWith('|') && line.endsWith('|')
    const isSeparator = /^\|[\s:-]+\|/.test(next)
    if (!isHeader || !isSeparator) {
      out.push(lines[i])
      continue
    }
    const headers = splitTableRow(line)
    i += 2
    while (i < lines.length) {
      const row = lines[i].trim()
      if (!row.startsWith('|') || !row.endsWith('|')) {
        i -= 1
        break
      }
      const cells = splitTableRow(row)
      const title = cells[0] || ''
      const detail = cells.slice(1)
        .map((cell, index) => headers[index + 1] ? `${headers[index + 1]}: ${cell}` : cell)
        .filter(Boolean)
        .join(' - ')
      out.push(detail ? `- ${title}: ${detail}` : `- ${title}`)
      i += 1
    }
  }
  return out.join('\n')
}

function splitTableRow(line) {
  return line
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((cell) => cell.trim())
}
