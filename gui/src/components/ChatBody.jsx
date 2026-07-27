import { Fragment, useEffect, useRef } from 'react'
import Box from '../lib/Box.jsx'
import { cssToObj } from '../lib/css.js'
import { compactMarkdownTables, parseInlineSegments } from './chatLinks.js'

function InlineText({ text, onOpenArtifact, onOpenLog }) {
  const parts = parseInlineSegments(text)
  return parts.map((part, i) => {
    if (part.type === 'link') {
      const href = part.href
      if (href.startsWith('musubi-artifact:')) {
        const path = decodeURIComponent(href.slice('musubi-artifact:'.length))
        return (
          <button
            type="button"
            key={i}
            onClick={(e) => {
              e.preventDefault()
              e.stopPropagation()
              onOpenArtifact?.(path)
            }}
            style={{
              display: 'inline',
              padding: 0,
              border: 0,
              background: 'transparent',
              color: '#8ab4d8',
              cursor: 'pointer',
              font: 'inherit',
              textDecoration: 'underline',
              textUnderlineOffset: 2,
            }}
          >
            {part.label}
          </button>
        )
      }
      if (href.startsWith('musubi-log:')) {
        return (
          <button
            type="button"
            key={i}
            onClick={(e) => {
              e.preventDefault()
              e.stopPropagation()
              onOpenLog?.()
            }}
            style={{
              display: 'inline',
              padding: 0,
              border: 0,
              background: 'transparent',
              color: '#ffbe7a',
              cursor: 'pointer',
              font: 'inherit',
              textDecoration: 'underline',
              textUnderlineOffset: 2,
            }}
          >
            {part.label}
          </button>
        )
      }
      return <a key={i} href={href} target="_blank" rel="noreferrer" style={{ color: '#8ab4d8' }}>{part.label}</a>
    }
    if (part.type === 'strong') {
      return <strong key={i} style={{ color: '#f4f4f5', fontWeight: 650 }}>{part.text}</strong>
    }
    return <span key={i}>{part.text}</span>
  })
}

function FormattedMessage({ text, onOpenArtifact, onOpenLog }) {
  const lines = compactMarkdownTables(text).replace(/\r\n/g, '\n').split('\n')
  const blocks = []
  let list = null
  const flushList = () => {
    if (!list) return
    const Tag = list.kind === 'ol' ? 'ol' : 'ul'
    blocks.push(
      <Tag key={'list-' + blocks.length} style={{ margin: '6px 0', paddingLeft: 20 }}>
        {list.items.map((item, i) => <li key={i} style={{ margin: '3px 0' }}><InlineText text={item} onOpenArtifact={onOpenArtifact} onOpenLog={onOpenLog} /></li>)}
      </Tag>
    )
    list = null
  }

  for (const raw of lines) {
    const line = raw.trim()
    if (!line) {
      flushList()
      continue
    }
    const ordered = line.match(/^\d+\.\s+(.+)$/)
    const bullet = line.match(/^[-*]\s+(.+)$/)
    if (ordered || bullet) {
      const kind = ordered ? 'ol' : 'ul'
      if (!list || list.kind !== kind) {
        flushList()
        list = { kind, items: [] }
      }
      list.items.push((ordered || bullet)[1])
      continue
    }
    flushList()
    if (line.startsWith('### ')) {
      blocks.push(<div key={blocks.length} style={{ color: '#f4f4f5', fontWeight: 650, margin: '7px 0 3px' }}><InlineText text={line.slice(4)} onOpenArtifact={onOpenArtifact} onOpenLog={onOpenLog} /></div>)
    } else {
      blocks.push(<p key={blocks.length} style={{ margin: blocks.length ? '6px 0 0' : 0 }}><InlineText text={line} onOpenArtifact={onOpenArtifact} onOpenLog={onOpenLog} /></p>)
    }
  }
  flushList()
  return <>{blocks.length ? blocks : <span />}</>
}

function LogWindow({ vals }) {
  return (
    <div
      style={{
        position: 'absolute',
        inset: 0,
        zIndex: 20,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'rgba(7,10,15,0.72)',
        backdropFilter: 'blur(2px)',
        padding: 18,
      }}
      onClick={vals.onCloseLog}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: 'min(760px, 94vw)',
          maxHeight: '82%',
          display: 'flex',
          flexDirection: 'column',
          background: '#111721',
          border: '1px solid rgba(255,155,61,0.34)',
          borderRadius: 10,
          boxShadow: '0 22px 60px rgba(0,0,0,0.55)',
          overflow: 'hidden',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '12px 14px', borderBottom: '1px solid rgba(255,255,255,0.08)' }}>
          <span style={{ width: 7, height: 7, borderRadius: '50%', background: '#ff9b3d', flexShrink: 0 }} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 12.5, color: '#f4f4f5', fontWeight: 650 }}>Driver process log</div>
            {vals.driverTask && <div style={{ fontSize: 10.5, color: '#7a7a82', marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{vals.driverTask}</div>}
          </div>
          <button onClick={vals.onCloseLog} style={{ width: 28, height: 28, borderRadius: 7, border: '1px solid rgba(255,255,255,0.1)', background: '#19212f', color: '#cfcfd4', cursor: 'pointer' }}>
            <svg viewBox="0 0 24 24" width="15" height="15" fill="none"><path d="M7 7 L17 17 M17 7 L7 17" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" /></svg>
          </button>
        </div>
        <pre style={{ margin: 0, padding: 14, overflow: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontFamily: "'IBM Plex Mono',monospace", fontSize: 11, lineHeight: 1.5, color: '#d4d4d8', background: '#0d1117' }}>
          {vals.driverProcessLog || 'No process log is available for the latest run.'}
        </pre>
      </div>
    </div>
  )
}

// The running state is the Now banner's job, and the banner is far larger and
// far better placed. What is left here is a pointer: one line that says the
// run is live and where to watch it, so the chat stays narrative.
function ProcessMessage({ vals }) {
  return (
    <div style={{ padding: '6px 16px', display: 'flex', alignItems: 'center', gap: 9 }}>
      <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--live)', animation: 'pulse 1.4s ease-in-out infinite', flexShrink: 0 }} />
      <span style={{ fontFamily: 'var(--sans)', fontSize: 'var(--fs-3)', color: 'var(--text-2)', minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {vals.nowRun?.actor || 'The driver'} is working
      </span>
      <button
        onClick={vals.onOpenLog}
        style={{ marginLeft: 'auto', flexShrink: 0, padding: 0, border: 0, background: 'transparent', color: '#ffb66f', fontFamily: 'var(--sans)', fontSize: 'var(--fs-3)', cursor: 'pointer' }}
      >
        see the log
      </button>
    </div>
  )
}

// Scrollable Orchestrator conversation + composer. Pipeline execution now
// shares this durable conversation instead of owning a Studio chat surface.
export default function ChatBody({ vals, config = null }) {
  const scrollRef = useRef(null)
  const shouldStickRef = useRef(true)
  const lastMessageCountRef = useRef(0)
  const sendDisabled = !!vals.sendDisabled
  const inputDisabled = !!vals.inputDisabled
  const latestUserMessageIndex = vals.chat.reduce(
    (latest, message, index) => (message.role === 'you' ? index : latest),
    -1,
  )

  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const messageCount = vals.chat.length
    const addedMessage = messageCount > lastMessageCountRef.current
    lastMessageCountRef.current = messageCount
    if (shouldStickRef.current || addedMessage) {
      el.scrollTop = el.scrollHeight
    }
  }, [vals.chat, vals.driverStatusText, vals.driverProcessLog, vals.driverProcessOpen])

  const onScroll = () => {
    const el = scrollRef.current
    if (!el) return
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
    shouldStickRef.current = distanceFromBottom < 48
  }

  return (
    <div style={{ position: 'relative', display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>
      <div ref={scrollRef} onScroll={onScroll} style={{ flex: 1, overflow: 'auto', padding: '12px 0', display: 'flex', flexDirection: 'column', gap: 2 }}>
        {vals.chat.map((c, i) => (
          <Fragment key={i}>
            <div style={cssToObj(c.rowStyle)}>
              {c.showMeta && <div style={cssToObj(c.metaStyle)}>{c.meta}</div>}
              <div style={cssToObj(c.bubbleStyle)}>
                {c.formatted ? <FormattedMessage text={c.text} onOpenArtifact={vals.onOpenArtifact} onOpenLog={vals.onOpenLog} /> : c.text}
              </div>
            </div>
            {vals.driverBusy && i === latestUserMessageIndex && <ProcessMessage vals={vals} />}
          </Fragment>
        ))}
      </div>
      {/* Run configuration is a start-of-run decision, so it sits with the
          composer rather than in a header band above the evidence. */}
      <div className="composer">
        {config}
        <div className="composer__row">
        <input
          value={vals.draft}
          onChange={vals.onDraft}
          onKeyDown={vals.onDraftKey}
          disabled={inputDisabled}
          placeholder={vals.disabledText || (vals.driverBusy ? 'Agent is still working...' : (vals.placeholder || 'Message the driver...'))}
          style={{ flex: 1, minWidth: 0, background: inputDisabled ? 'rgba(25,33,47,0.58)' : 'var(--raised)', border: '1px solid var(--line-strong)', borderRadius: 'var(--r-sm)', padding: '9px 12px', color: inputDisabled ? 'var(--text-3)' : '#e9e9ea', fontFamily: 'var(--sans)', fontSize: 'var(--fs-4)', outline: 'none', cursor: inputDisabled ? 'not-allowed' : 'text' }}
        />
        <Box
          as="button"
          onClick={sendDisabled ? undefined : vals.onSend}
          disabled={sendDisabled}
          title={vals.sendTitle || 'Send'}
          aria-label={vals.sendTitle || 'Send'}
          css={
            'display:flex;align-items:center;justify-content:center;width:36px;height:36px;flex-shrink:0;border-radius:var(--r-sm);cursor:' + (sendDisabled ? 'not-allowed' : 'pointer') + ';' +
            (sendDisabled
              ? 'border:1px solid rgba(255,255,255,0.08);background:rgba(255,255,255,0.04);color:#5f6673;'
              : 'border:1px solid rgba(255,155,61,0.4);background:rgba(255,155,61,0.14);color:var(--live);')
          }
          hover={sendDisabled ? '' : 'background:rgba(255,155,61,0.24)'}
        >
          {/* One glyph, one meaning. Stopping a run is a labelled button in
              the Now banner, not this control wearing a different colour. */}
          <svg viewBox="0 0 24 24" width="17" height="17" fill="none"><path d="M5 12 H18 M13 7 L18 12 L13 17" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg>
        </Box>
        </div>
      </div>
      {vals.logWindowOpen && vals.hasDriverLog && <LogWindow vals={vals} />}
    </div>
  )
}
