import { useEffect, useRef } from 'react'
import Box from '../lib/Box.jsx'
import { cssToObj } from '../lib/css.js'

// Scrollable message list + composer, shared by the orchestrator feed and the
// pipeline driver chat. Auto-scrolls to the newest message.
export default function ChatBody({ vals }) {
  const scrollRef = useRef(null)
  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [vals.chat])

  return (
    <>
      <div ref={scrollRef} style={{ flex: 1, overflow: 'auto', padding: '12px 0', display: 'flex', flexDirection: 'column', gap: 2 }}>
        {vals.chat.map((c, i) => (
          <div key={i} style={cssToObj(c.rowStyle)}>
            {c.showMeta && <div style={cssToObj(c.metaStyle)}>{c.meta}</div>}
            <div style={cssToObj(c.bubbleStyle)}>{c.text}</div>
          </div>
        ))}
      </div>
      <div style={{ borderTop: '1px solid rgba(255,255,255,0.06)', padding: '11px 14px', display: 'flex', gap: 8, alignItems: 'center' }}>
        <input
          value={vals.draft}
          onChange={vals.onDraft}
          onKeyDown={vals.onDraftKey}
          placeholder="Message the driver..."
          style={{ flex: 1, minWidth: 0, background: '#19212f', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 9, padding: '9px 12px', color: '#e9e9ea', fontFamily: "'IBM Plex Sans',system-ui,sans-serif", fontSize: 12.5, outline: 'none' }}
        />
        <Box as="button" onClick={vals.onSend} title="Send" css="display:flex;align-items:center;justify-content:center;width:36px;height:36px;flex-shrink:0;border-radius:9px;border:1px solid rgba(255,155,61,0.4);background:rgba(255,155,61,0.14);color:#ff9b3d;cursor:pointer" hover="background:rgba(255,155,61,0.24)">
          <svg viewBox="0 0 24 24" width="17" height="17" fill="none"><path d="M5 12 H18 M13 7 L18 12 L13 17" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg>
        </Box>
      </div>
    </>
  )
}
