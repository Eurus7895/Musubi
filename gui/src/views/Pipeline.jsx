import Box from '../lib/Box.jsx'
import { cssToObj } from '../lib/css.js'
import ChatBody from '../components/ChatBody.jsx'

export default function Pipeline({ vals }) {
  return (
    <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>
      {/* ░ palette ░ */}
      <div style={{ width: 248, flexShrink: 0, borderRight: '1px solid rgba(255,255,255,0.06)', background: '#111721', display: 'flex', flexDirection: 'column', minHeight: 0 }}>
        <div style={{ padding: '16px 16px 10px', flexShrink: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 600 }}>Agents</div>
          <div style={{ fontSize: 11, color: '#6a6a72', marginTop: 2 }}>Click to add to the pipeline →</div>
        </div>
        <div style={{ flex: 1, overflow: 'auto', padding: '0 14px 14px', display: 'flex', flexDirection: 'column', gap: 8, minHeight: 0 }}>
          {vals.pipeCatalog.map((c) => (
            <Box as="button" key={c.role} onClick={c.onAdd} css={c.cardStyle} hover="border-color:rgba(255,155,61,0.4)">
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 7 }}>
                <span style={cssToObj(c.roleChipStyle)}>{c.role}</span>
                <span style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', width: 19, height: 19, borderRadius: 6, background: 'rgba(255,155,61,0.14)', color: '#ff9b3d', fontSize: 15, lineHeight: 1, fontWeight: 500 }}>+</span>
              </div>
              <div style={{ fontSize: 11, color: '#9b9ba2', lineHeight: 1.42, marginBottom: 8 }}>{c.desc}</div>
              <div style={{ fontFamily: "'IBM Plex Mono',monospace", fontSize: 10, color: '#6a6a72' }}>{c.toolsLabel}</div>
            </Box>
          ))}
        </div>
        <div style={{ borderTop: '1px solid rgba(255,255,255,0.06)', padding: '13px 14px', flexShrink: 0 }}>
          <div style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.09em', color: '#6a6a72', marginBottom: 9 }}>Presets</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {vals.pipePresets.map((p) => (
              <button key={p.name} onClick={p.onLoad} style={cssToObj(p.btnStyle)}><span>{p.name}</span><span style={{ color: '#6a6a72', fontSize: 10 }}>{p.countLabel}</span></button>
            ))}
          </div>
        </div>
      </div>

      {/* ░ builder canvas ░ */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0, overflow: 'auto' }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 16, padding: '22px 26px 14px', flexWrap: 'wrap' }}>
          <div style={{ flex: 1, minWidth: 200 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <div style={{ fontSize: 18, fontWeight: 600 }}>Pipeline studio</div>
              <span style={{ fontFamily: "'IBM Plex Mono',monospace", fontSize: 11, color: '#ff9b3d', background: 'rgba(255,155,61,0.1)', border: '1px solid rgba(255,155,61,0.3)', padding: '2px 9px', borderRadius: 6 }}>{vals.pipeName}</span>
            </div>
            <div style={{ fontSize: 12, color: '#6a6a72', marginTop: 4, fontFamily: "'IBM Plex Mono',monospace" }}>{vals.pipeStatusText}</div>
          </div>
          <div style={{ display: 'flex', gap: 9, flexShrink: 0 }}>
            <Box as="button" onClick={vals.onClearPipe} css="font-family:'IBM Plex Mono',monospace;font-size:12px;padding:9px 14px;border-radius:9px;cursor:pointer;background:#19212f;border:1px solid rgba(255,255,255,0.1);color:#9b9ba2" hover="color:#e9e9ea">clear</Box>
            <button onClick={vals.runAction} style={cssToObj(vals.runStyle)}>{vals.runLabel}</button>
          </div>
        </div>

        <div style={{ flex: 1, minHeight: 0, padding: '6px 26px 26px' }}>
          {vals.pipeEmpty && (
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 12, height: 280, background: '#0f1620', border: '1px dashed rgba(255,255,255,0.12)', borderRadius: 14, color: '#6a6a72', textAlign: 'center' }}>
              <svg viewBox="0 0 24 24" width="34" height="34" fill="none" style={{ color: '#3a4250' }}><circle cx="5" cy="12" r="2.1" stroke="currentColor" strokeWidth="1.5" /><circle cx="12" cy="12" r="2.1" stroke="currentColor" strokeWidth="1.5" /><circle cx="19" cy="12" r="2.1" stroke="currentColor" strokeWidth="1.5" /><path d="M7.1 12 H9.9 M14.1 12 H16.9" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" /></svg>
              <div style={{ fontSize: 13, color: '#9b9ba2' }}>No agents staged</div>
              <div style={{ fontSize: 11.5, maxWidth: 280, lineHeight: 1.5 }}>Add agents from the left, or load the <span style={{ color: '#ff9b3d' }}>feature-dev</span> preset to compose your pipeline.</div>
            </div>
          )}

          {vals.pipeHasSteps && (
            <>
              <div style={{ background: '#0f1620', border: '1px solid rgba(255,255,255,0.06)', borderRadius: 14, padding: '20px 18px', overflowX: 'auto' }}>
                <div style={{ display: 'flex', alignItems: 'stretch', gap: 0, minWidth: 'min-content' }}>

                  {/* driver origin · click to chat */}
                  <Box onClick={vals.openPipeChat} css={vals.pipeDriverStyle} hover="border-color:rgba(255,155,61,0.75)">
                    <div style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.1em', color: '#ff9b3d', fontWeight: 600, marginBottom: 7 }}>driver</div>
                    <div style={{ fontFamily: "'IBM Plex Mono',monospace", fontSize: 12, color: '#e9e9ea' }}>the knot</div>
                    <div style={{ fontSize: 10, color: '#6a6a72', marginTop: 6, lineHeight: 1.4 }}>spawns each agent in order</div>
                    <div style={{ display: 'inline-flex', alignItems: 'center', gap: 5, marginTop: 10, fontFamily: "'IBM Plex Mono',monospace", fontSize: 9.5, color: '#ff9b3d', background: 'rgba(255,155,61,0.1)', border: '1px solid rgba(255,155,61,0.28)', padding: '3px 9px', borderRadius: 20 }}><svg viewBox="0 0 24 24" width="11" height="11" fill="none"><path d="M5 6 H19 V15 H11 L7 18 V15 H5 Z" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" /></svg>chat</div>
                  </Box>
                  <div style={{ width: 42, flexShrink: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', alignSelf: 'center', color: '#ff9b3d' }}><svg viewBox="0 0 40 24" width="36" height="18" fill="none"><path d="M2 12 H32 M27 7 L32 12 L27 17" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" opacity="0.7" /></svg></div>

                  {/* the ordered chain */}
                  {vals.pipeStepsView.map((st) => (
                    <div key={st.uid} style={{ display: 'flex', alignItems: 'stretch', flexShrink: 0 }}>
                      <div style={cssToObj(st.cardStyle)}>
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                            <span style={cssToObj(st.orderBadge)}>{st.orderLabel}</span>
                            <span style={cssToObj(st.roleChipStyle)}>{st.role}</span>
                          </div>
                          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 10, color: st.statusColor, fontFamily: "'IBM Plex Mono',monospace" }}><span style={cssToObj(st.dotStyle)} />{st.statusLabel}</span>
                        </div>
                        <div style={{ fontSize: 11.5, color: '#cfcfd4', lineHeight: 1.42, height: 50, overflow: 'hidden', marginBottom: 10 }}>{st.desc}</div>
                        <div style={{ height: 4, background: 'rgba(255,255,255,0.08)', borderRadius: 3, overflow: 'hidden', marginBottom: 10 }}><div style={cssToObj(st.barFillStyle)} /></div>
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', fontFamily: "'IBM Plex Mono',monospace", fontSize: 10, color: '#6a6a72' }}>
                          <span>{st.toolsLabel}</span>
                          <span>{st.maxLabel}</span>
                        </div>
                        {st.showHandle && (
                          <div style={{ marginTop: 10, paddingTop: 9, borderTop: '1px solid rgba(255,255,255,0.06)', fontFamily: "'IBM Plex Mono',monospace", fontSize: 10.5, color: '#8a8a92' }}>{st.handle}</div>
                        )}
                        {st.showControls && (
                          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 10, paddingTop: 9, borderTop: '1px solid rgba(255,255,255,0.06)' }}>
                            <Box as="button" onClick={st.onUp} title="Move earlier" css="display:flex;align-items:center;justify-content:center;width:26px;height:24px;border-radius:6px;background:#232c3c;border:1px solid rgba(255,255,255,0.1);color:#9b9ba2;cursor:pointer" hover="color:#fff"><svg viewBox="0 0 24 24" width="13" height="13" fill="none"><path d="M14 6 L8 12 L14 18" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg></Box>
                            <Box as="button" onClick={st.onDown} title="Move later" css="display:flex;align-items:center;justify-content:center;width:26px;height:24px;border-radius:6px;background:#232c3c;border:1px solid rgba(255,255,255,0.1);color:#9b9ba2;cursor:pointer" hover="color:#fff"><svg viewBox="0 0 24 24" width="13" height="13" fill="none"><path d="M10 6 L16 12 L10 18" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg></Box>
                            <div style={{ flex: 1 }} />
                            <Box as="button" onClick={st.onRemove} title="Remove" css="display:flex;align-items:center;justify-content:center;width:26px;height:24px;border-radius:6px;background:transparent;border:1px solid rgba(232,106,95,0.3);color:#e86a5f;cursor:pointer" hover="background:rgba(232,106,95,0.12)"><svg viewBox="0 0 24 24" width="13" height="13" fill="none"><path d="M6 6 L18 18 M18 6 L6 18" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" /></svg></Box>
                          </div>
                        )}
                      </div>
                      {st.showConnector && (
                        <div style={{ width: 56, flexShrink: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 5, alignSelf: 'center' }}>
                          <svg viewBox="0 0 24 24" width="22" height="22" fill="none" style={cssToObj(st.connStyle)}><path d="M12 3 L19 6 V11 C19 15.5 15.7 18.6 12 20.5 C8.3 18.6 5 15.5 5 11 V6 Z" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" /><path d="M9 11.6 L11.2 13.8 L15 9.6" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" /></svg>
                          <div style={{ fontFamily: "'IBM Plex Mono',monospace", fontSize: 8.5, color: '#5a5a62' }}>handoff</div>
                        </div>
                      )}
                    </div>
                  ))}

                  {/* audit terminal */}
                  <div style={{ width: 42, flexShrink: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', alignSelf: 'center', color: '#3a4250' }}><svg viewBox="0 0 40 24" width="36" height="18" fill="none"><path d="M2 12 H32 M27 7 L32 12 L27 17" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg></div>
                  <div style={{ width: 144, flexShrink: 0, alignSelf: 'center', background: '#141b27', border: '1px solid rgba(138,180,216,0.32)', borderRadius: 12, padding: 14, textAlign: 'center' }}>
                    <div style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.1em', color: '#8ab4d8', fontWeight: 600, marginBottom: 7 }}>audit</div>
                    <div style={{ fontFamily: "'IBM Plex Mono',monospace", fontSize: 12, color: '#e9e9ea' }}>append-only</div>
                    <div style={{ fontSize: 10, color: '#6a6a72', marginTop: 6, lineHeight: 1.4 }}>every handoff tied here</div>
                  </div>

                </div>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginTop: 14, flexWrap: 'wrap', fontFamily: "'IBM Plex Mono',monospace", fontSize: 10.5, color: '#6a6a72' }}>
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}><span style={{ width: 7, height: 7, borderRadius: '50%', background: '#e3b341' }} />queued</span>
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}><span style={{ width: 7, height: 7, borderRadius: '50%', background: '#ff9b3d' }} />running</span>
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}><span style={{ width: 7, height: 7, borderRadius: '50%', background: '#54c79a' }} />done</span>
                <span style={{ color: '#4a4a52' }}>·</span>
                <span>each agent turn-capped · firewalled brief · every spawn &amp; handoff appended to the audit</span>
              </div>
            </>
          )}
        </div>
      </div>

      {/* ░ chat · driver (opens on driver click) ░ */}
      {vals.pipeChatOpen && (
        <div style={{ width: 322, flexShrink: 0, borderLeft: '1px solid rgba(255,255,255,0.06)', background: '#111721', display: 'flex', flexDirection: 'column', minHeight: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '15px 18px', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
              <span style={{ fontSize: 13, fontWeight: 600 }}>Chat · driver</span>
              <span style={{ fontSize: 10, color: '#6a6a72' }}>every reply tied to policy</span>
            </div>
            <Box as="button" onClick={vals.closePipeChat} title="Close" css="display:flex;align-items:center;justify-content:center;width:26px;height:26px;border-radius:7px;border:1px solid rgba(255,255,255,0.1);background:#232c3c;color:#9b9ba2;cursor:pointer" hover="color:#fff"><svg viewBox="0 0 24 24" width="15" height="15" fill="none"><path d="M6 6 L18 18 M18 6 L6 18" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" /></svg></Box>
          </div>
          <ChatBody vals={vals} />
        </div>
      )}
    </div>
  )
}
