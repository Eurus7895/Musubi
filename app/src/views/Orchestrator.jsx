import { useEffect, useRef } from 'react'
import Box from '../lib/Box.jsx'
import { cssToObj } from '../lib/css.js'
import NetGraphic from '../sim/NetGraphic.jsx'
import ChatBody from '../components/ChatBody.jsx'

// Scales the 1000×520 stage to fit its container (port of fitStage()).
function useFitStage() {
  const stageRef = useRef(null)
  useEffect(() => {
    const el = stageRef.current
    if (!el || !el.parentElement) return
    const p = el.parentElement
    const fit = () => {
      const sc = Math.min(1, (p.clientWidth - 16) / 1000, (p.clientHeight - 16) / 520)
      el.style.transform = 'scale(' + sc.toFixed(4) + ')'
    }
    fit()
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(fit) : null
    ro?.observe(p)
    return () => ro?.disconnect()
  }, [])
  return stageRef
}

export default function Orchestrator({ vals }) {
  const stageRef = useFitStage()

  return (
    <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>
      {/* canvas */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0, overflow: 'auto', position: 'relative' }}>
        {/* header */}
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 16, padding: '22px 26px 8px' }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 18, fontWeight: 600, letterSpacing: '-0.02em' }}>Orchestrator</div>
            <div style={{ fontSize: 12, color: '#6a6a72', marginTop: 3 }}>The knot ties every thread to policy. Sub-agents are governed threads — turn-capped, firewalled brief, restricted tools, every spawn bound into the audit.</div>
          </div>
          <div style={{ display: 'flex', gap: 18, alignItems: 'center', flexShrink: 0 }}>
            <div style={{ textAlign: 'right' }}><div style={{ fontFamily: "'IBM Plex Mono',monospace", fontSize: 20, fontWeight: 600, color: '#e9e9ea', lineHeight: 1 }}>{vals.runningCount}</div><div style={{ fontSize: 10, color: '#6a6a72', textTransform: 'uppercase', letterSpacing: '0.08em', marginTop: 2 }}>running</div></div>
            <div style={{ textAlign: 'right' }}><div style={{ fontFamily: "'IBM Plex Mono',monospace", fontSize: 20, fontWeight: 600, color: '#54c79a', lineHeight: 1 }}>{vals.totalDone}</div><div style={{ fontSize: 10, color: '#6a6a72', textTransform: 'uppercase', letterSpacing: '0.08em', marginTop: 2 }}>completed</div></div>
            <Box as="button" onClick={vals.togglePause} css="display:inline-flex;align-items:center;gap:7px;background:#232c3c;border:1px solid rgba(255,255,255,0.1);color:#e9e9ea;padding:8px 13px;border-radius:8px;cursor:pointer;font-family:'IBM Plex Mono',monospace;font-size:11px" hover="border-color:rgba(255,155,61,0.5)">{vals.pauseLabel}</Box>
          </div>
        </div>

        {/* graph */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: '10px 26px 20px', position: 'relative' }}>
          {/* watermark */}
          <div style={{ position: 'absolute', inset: 0, pointerEvents: 'none', backgroundImage: 'repeating-linear-gradient(45deg, rgba(255,255,255,0.04) 0 1px, transparent 1px 26px), repeating-linear-gradient(-45deg, rgba(255,255,255,0.04) 0 1px, transparent 1px 26px)', WebkitMaskImage: 'radial-gradient(ellipse 62% 58% at 50% 42%, #000 30%, transparent 75%)', maskImage: 'radial-gradient(ellipse 62% 58% at 50% 42%, #000 30%, transparent 75%)' }} />

          {/* woven net · driver at top, governed threads hang below */}
          <div ref={stageRef} style={{ position: 'relative', width: 1000, height: 520, flexShrink: 0, transformOrigin: 'top center' }}>
            <div style={{ position: 'absolute', left: 0, top: 158, width: 1000, height: 130, pointerEvents: 'none' }}>
              <NetGraphic shown={vals.webShown} />
            </div>

            {/* driver · the knot (hub) */}
            <div style={cssToObj(vals.driverStyle)}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 9, marginBottom: 9 }}>
                <span style={cssToObj(vals.driverDotStyle)} />
                <span style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.1em', color: '#ff9b3d', fontWeight: 600 }}>Driver · the knot</span>
              </div>
              <div style={{ fontFamily: "'IBM Plex Mono',monospace", fontSize: 14, fontWeight: 600, color: '#fff' }}>{vals.activeModel}</div>
              <div style={{ fontSize: 11, color: '#7a7a82', marginTop: 5, lineHeight: 1.45 }}>Reaches the model through <span style={{ color: '#cfcfd4' }}>one inject point</span> · LMRouter</div>
              <div style={{ display: 'flex', justifyContent: 'center', gap: 14, marginTop: 11, fontFamily: "'IBM Plex Mono',monospace", fontSize: 10.5, color: '#6a6a72' }}>
                <span>cycle <span style={{ color: '#cfcfd4' }}>{vals.driverCycle}</span></span>
                <span>spawns <span style={{ color: '#cfcfd4' }}>{vals.totalSpawned}</span></span>
              </div>
            </div>

            {/* governed sub-agent threads, tied around the knot */}
            {vals.subagents.map((a) => (
              <Box key={a.handle} css={a.cardStyle} onClick={a.onSelect} hover="border-color:rgba(255,155,61,0.45)">
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginBottom: 9 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 7, minWidth: 0 }}>
                    <span style={cssToObj(a.orderBadge)}>{a.orderLabel}</span>
                    <span style={cssToObj(a.roleChipStyle)}>{a.role}</span>
                  </div>
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 10, color: a.statusColor, fontFamily: "'IBM Plex Mono',monospace" }}><span style={cssToObj(a.dotStyle)} />{a.statusLabel}</span>
                </div>
                <div style={{ fontFamily: "'IBM Plex Mono',monospace", fontSize: 11, color: '#8a8a92', marginBottom: 4 }}>{a.handle}</div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8, fontFamily: "'IBM Plex Mono',monospace", fontSize: 10, color: '#9b9ba2' }}><span style={{ width: 5, height: 5, borderRadius: '50%', background: a.modelColor, flexShrink: 0 }} />{a.model}<span style={{ color: '#6a6a72' }}>· {a.profile}</span></div>
                <div style={{ fontSize: 11.5, color: '#cfcfd4', lineHeight: 1.4, height: 32, overflow: 'hidden' }}>{a.brief}</div>
                <div style={{ height: 1, background: 'rgba(255,255,255,0.06)', margin: '10px 0 9px' }} />
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                  <div style={{ flex: 1, height: 3, background: 'rgba(255,255,255,0.08)', borderRadius: 2, overflow: 'hidden' }}><div style={cssToObj(a.barFillStyle)} /></div>
                  <span style={{ fontFamily: "'IBM Plex Mono',monospace", fontSize: 10, color: '#7a7a82' }}>{a.turnsLabel}</span>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', fontFamily: "'IBM Plex Mono',monospace", fontSize: 10, color: '#6a6a72' }}>
                  <span>{a.toolCount} tools</span>
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><svg viewBox="0 0 24 24" width="11" height="11" fill="none"><circle cx="12" cy="12" r="8" stroke="currentColor" strokeWidth="1.8" /><path d="M12 8 V12 L15 14" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" /></svg>{a.wallLabel}</span>
                </div>
              </Box>
            ))}
          </div>
        </div>
      </div>

      {/* right panel: detail OR feed */}
      <div style={{ width: 322, flexShrink: 0, borderLeft: '1px solid rgba(255,255,255,0.06)', background: '#111721', display: 'flex', flexDirection: 'column', minHeight: 0 }}>
        {vals.hasDetail && <DetailPanel vals={vals} />}
        {vals.showFeed && <FeedPanel vals={vals} />}
      </div>
    </div>
  )
}

function DetailPanel({ vals }) {
  const d = vals.detail
  return (
    <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0, height: '100%' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '15px 18px', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
        <Box as="button" onClick={vals.clearSelect} css="display:flex;align-items:center;justify-content:center;width:26px;height:26px;border-radius:7px;border:1px solid rgba(255,255,255,0.1);background:#232c3c;color:#9b9ba2;cursor:pointer" hover="color:#fff"><svg viewBox="0 0 24 24" width="15" height="15" fill="none"><path d="M14 6 L8 12 L14 18" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg></Box>
        <span style={{ fontSize: 13, fontWeight: 600 }}>Sub-agent detail</span>
      </div>
      <div style={{ flex: 1, overflow: 'auto', padding: 18 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
          <span style={cssToObj(d.roleChipStyle)}>{d.role}</span>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 11, color: d.statusColor, fontFamily: "'IBM Plex Mono',monospace" }}><span style={cssToObj(d.dotStyle)} />{d.statusLabel}</span>
        </div>
        <div style={{ fontFamily: "'IBM Plex Mono',monospace", fontSize: 13, color: '#fff', marginBottom: 4 }}>{d.handle}</div>
        <div style={{ fontFamily: "'IBM Plex Mono',monospace", fontSize: 10.5, color: '#6a6a72', marginBottom: 14 }}>parent · {d.parent}</div>
        <div style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.09em', color: '#6a6a72', marginBottom: 6 }}>Model · resolved per agent</div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontFamily: "'IBM Plex Mono',monospace", fontSize: 12.5, color: '#e9e9ea', marginBottom: 18, padding: '11px 13px', background: '#19212f', border: '1px solid rgba(255,255,255,0.06)', borderRadius: 8 }}><span style={{ width: 7, height: 7, borderRadius: '50%', background: d.modelColor, flexShrink: 0 }} />{d.model}<span style={{ color: '#6a6a72' }}>· {d.profile}</span></div>
        <div style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.09em', color: '#6a6a72', marginBottom: 6 }}>Brief</div>
        <div style={{ fontSize: 12.5, color: '#cfcfd4', lineHeight: 1.5, marginBottom: 18, padding: '11px 13px', background: '#19212f', border: '1px solid rgba(255,255,255,0.06)', borderRadius: 8 }}>{d.brief}</div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginBottom: 18 }}>
          <div style={{ background: '#19212f', border: '1px solid rgba(255,255,255,0.06)', borderRadius: 8, padding: '10px 12px' }}><div style={{ fontSize: 10, color: '#6a6a72', marginBottom: 4 }}>turns</div><div style={{ fontFamily: "'IBM Plex Mono',monospace", fontSize: 15, color: '#e9e9ea' }}>{d.turnsLabel}</div></div>
          <div style={{ background: '#19212f', border: '1px solid rgba(255,255,255,0.06)', borderRadius: 8, padding: '10px 12px' }}><div style={{ fontSize: 10, color: '#6a6a72', marginBottom: 4 }}>wall-clock</div><div style={{ fontFamily: "'IBM Plex Mono',monospace", fontSize: 15, color: '#e9e9ea' }}>{d.wallFull}</div></div>
          <div style={{ background: '#19212f', border: '1px solid rgba(255,255,255,0.06)', borderRadius: 8, padding: '10px 12px' }}><div style={{ fontSize: 10, color: '#6a6a72', marginBottom: 4 }}>per-turn timeout</div><div style={{ fontFamily: "'IBM Plex Mono',monospace", fontSize: 15, color: '#e9e9ea' }}>60s</div></div>
          <div style={{ background: '#19212f', border: '1px solid rgba(255,255,255,0.06)', borderRadius: 8, padding: '10px 12px' }}><div style={{ fontSize: 10, color: '#6a6a72', marginBottom: 4 }}>tools used</div><div style={{ fontFamily: "'IBM Plex Mono',monospace", fontSize: 15, color: '#e9e9ea' }}>{d.toolsUsed}</div></div>
        </div>
        <div style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.09em', color: '#6a6a72', marginBottom: 8 }}>Allowed tools · restricted surface</div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginBottom: 18 }}>
          {d.tools.map((tool) => (
            <div key={tool} style={{ display: 'flex', alignItems: 'center', gap: 8, fontFamily: "'IBM Plex Mono',monospace", fontSize: 11.5, color: '#b8b8be', background: '#19212f', border: '1px solid rgba(255,255,255,0.06)', borderRadius: 7, padding: '7px 11px' }}><span style={{ width: 5, height: 5, borderRadius: '50%', background: '#54c79a' }} />{tool}</div>
          ))}
        </div>
        <div style={cssToObj(d.firewallStyle)}>{d.firewallNote}</div>
      </div>
    </div>
  )
}

function FeedPanel({ vals }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0, height: '100%' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '15px 18px', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          <span style={{ fontSize: 13, fontWeight: 600 }}>Chat · driver</span>
          <span style={{ fontSize: 10, color: '#6a6a72' }}>every reply tied to policy</span>
        </div>
        <span style={{ fontFamily: "'IBM Plex Mono',monospace", fontSize: 10, color: '#ff9b3d', display: 'inline-flex', alignItems: 'center', gap: 6 }}><span style={{ width: 6, height: 6, borderRadius: '50%', background: '#ff9b3d', animation: 'pulse 1.6s ease-in-out infinite' }} />the knot</span>
      </div>
      <ChatBody vals={vals} />
    </div>
  )
}
