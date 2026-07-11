import Box from '../lib/Box.jsx'
import { cssToObj } from '../lib/css.js'
import ChatBody from '../components/ChatBody.jsx'
import NewSessionButton from '../components/NewSessionButton.jsx'

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
                {vals.pipeStageOverflowLabel && (
                  <div style={{ display: 'inline-flex', marginBottom: 12, fontFamily: "'IBM Plex Mono',monospace", fontSize: 10.5, color: '#8ab4d8', background: 'rgba(138,180,216,0.1)', border: '1px solid rgba(138,180,216,0.25)', borderRadius: 6, padding: '4px 7px' }}>
                    {vals.pipeStageOverflowLabel} scroll to view the full flow
                  </div>
                )}
                <div style={{ display: 'flex', alignItems: 'stretch', gap: 0, minWidth: 'min-content' }}>

                  {/* driver origin */}
                  <div style={cssToObj(vals.pipeDriverStyle)}>
                    <div style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.1em', color: '#ff9b3d', fontWeight: 600, marginBottom: 7 }}>driver</div>
                    <div style={{ fontFamily: "'IBM Plex Mono',monospace", fontSize: 12, color: '#e9e9ea' }}>the knot</div>
                    <div style={{ fontSize: 10, color: '#6a6a72', marginTop: 6, lineHeight: 1.4 }}>spawns each agent in order</div>
                  </div>
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

          <PipelineRunHistory vals={vals} />
        </div>
      </div>

      {/* ░ chat · driver (opens on driver click) ░ */}
      <div style={{ width: 322, flexShrink: 0, borderLeft: '1px solid rgba(255,255,255,0.06)', background: '#111721', display: 'flex', flexDirection: 'column', minHeight: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '15px 18px', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
              <span style={{ fontSize: 13, fontWeight: 600 }}>Chat · pipeline</span>
              <span style={{ fontSize: 10, color: '#6a6a72' }}>{vals.pipeName} · isolated session</span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <NewSessionButton
                onClick={vals.pipeChatBody.onNewSession}
                disabled={vals.pipeChatBody.clearDriverDisabled}
              />
            </div>
          </div>
          <ChatBody vals={vals.pipeChatBody} />
      </div>
    </div>
  )
}

function PipelineRunHistory({ vals }) {
  const steps = vals.activePipeRunSteps || []
  const summary = vals.pipeRunSummary || {}
  return (
    <section style={{ marginTop: 18, display: 'grid', gridTemplateColumns: '230px minmax(0, 1fr)', gap: 14, minHeight: 260 }}>
      <aside style={{ background: '#0f1620', border: '1px solid rgba(255,255,255,0.07)', borderRadius: 12, overflow: 'hidden', minHeight: 0 }}>
        <div style={{ padding: '12px 13px 9px', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
          <div style={{ fontSize: 13, fontWeight: 650 }}>Studio runs</div>
          <div style={{ marginTop: 3, fontSize: 10.5, color: '#6a6a72', lineHeight: 1.35 }}>pipeline sessions only</div>
        </div>
        <div style={{ maxHeight: 310, overflow: 'auto', padding: 10, display: 'flex', flexDirection: 'column', gap: 8 }}>
          {vals.pipeRuns.length ? vals.pipeRuns.map((run) => (
            <Box key={run.id} as="button" css={run.cardStyle} onClick={run.onSelect} hover="border-color:rgba(255,155,61,0.45)">
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
                <span style={{ fontFamily: "'IBM Plex Mono',monospace", fontSize: 10.5, color: '#ffbe7a' }}>{run.orderLabel}</span>
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontFamily: "'IBM Plex Mono',monospace", fontSize: 10.5, color: run.statusColor }}><span style={cssToObj(run.dotStyle)} />{run.statusLabel}</span>
              </div>
              <div style={{ marginTop: 7, fontFamily: "'IBM Plex Mono',monospace", fontSize: 11.5, color: '#f4f4f5', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{run.title}</div>
              <div style={{ marginTop: 4, fontSize: 11, color: '#8a8a92' }}>{run.subtitle}</div>
              {run.currentBrief && <div style={{ marginTop: 8, fontSize: 11, color: '#cfcfd4', lineHeight: 1.35, maxHeight: 45, overflow: 'hidden' }}>{run.currentBrief}</div>}
            </Box>
          )) : (
            <div style={{ border: '1px dashed rgba(255,255,255,0.12)', borderRadius: 10, padding: 13, color: '#7a7a82', fontSize: 12, lineHeight: 1.45 }}>
              No pipeline runs yet. Use the studio chat to start one.
            </div>
          )}
        </div>
      </aside>

      <div style={{ background: '#0f1620', border: '1px solid rgba(255,255,255,0.07)', borderRadius: 12, overflow: 'hidden', minWidth: 0, display: 'flex', flexDirection: 'column' }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 14, padding: '13px 15px 11px', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
          <div style={{ minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 9, flexWrap: 'wrap' }}>
              <span style={{ fontSize: 13, fontWeight: 650 }}>{vals.pipeSessionTitle}</span>
              <span style={{ fontFamily: "'IBM Plex Mono',monospace", fontSize: 10.5, color: '#7a7a82' }}>{vals.pipeSessionSubtitle}</span>
            </div>
            <div style={{ marginTop: 6, fontSize: 12, color: '#cfcfd4', lineHeight: 1.4 }}>
              <span style={{ fontFamily: "'IBM Plex Mono',monospace", color: '#fff', fontWeight: 650 }}>{summary.countLine || 'no run selected'}</span>
              {summary.focusLine ? <span> · {summary.focusLine}</span> : null}
            </div>
          </div>
          <span style={{ fontFamily: "'IBM Plex Mono',monospace", fontSize: 10.5, color: '#ff9b3d', whiteSpace: 'nowrap' }}>spawn order</span>
        </div>
        {summary.alertLine && (
          <div style={{ margin: '11px 15px 0', width: 'fit-content', maxWidth: 'calc(100% - 30px)', fontSize: 11.5, color: '#ffcc77', lineHeight: 1.35, background: 'rgba(227,179,65,0.09)', border: '1px solid rgba(227,179,65,0.24)', borderRadius: 7, padding: '6px 9px' }}>{summary.alertLine}</div>
        )}
        <div style={{ flex: 1, overflow: 'auto', padding: 15 }}>
          {steps.length ? (
            <div style={{ display: 'flex', alignItems: 'stretch', gap: 14, minHeight: 178 }}>
              {steps.map((st) => (
                <div key={st.handle} style={{ display: 'flex', alignItems: 'center', gap: 14, flexShrink: 0 }}>
                  <PipelineRunStep step={st} />
                  {st.showConnector && <PipelineConnector />}
                </div>
              ))}
            </div>
          ) : (
            <div style={{ minHeight: 190, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#7a7a82', fontSize: 12 }}>
              No workers have been spawned for this pipeline session yet.
            </div>
          )}
        </div>
      </div>
    </section>
  )
}

function PipelineRunStep({ step }) {
  return (
    <div style={cssToObj(step.cardStyle)}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginBottom: 9 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 7, minWidth: 0 }}>
          <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', minWidth: 28, height: 20, padding: '0 6px', borderRadius: 6, fontFamily: "'IBM Plex Mono',monospace", fontSize: 10.5, fontWeight: 650, color: '#cfcfd4', background: step.isCurrent ? 'rgba(255,155,61,0.13)' : 'rgba(255,255,255,0.06)', border: '1px solid ' + (step.isCurrent ? 'rgba(255,155,61,0.42)' : 'rgba(255,255,255,0.12)') }}>{step.orderLabel}</span>
          <span style={cssToObj(step.roleChipStyle)}>{step.role}</span>
        </div>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontFamily: "'IBM Plex Mono',monospace", fontSize: 10.5, color: step.statusColor }}><span style={cssToObj(step.dotStyle)} />{step.statusLabel}</span>
      </div>
      <div style={{ fontFamily: "'IBM Plex Mono',monospace", fontSize: 10.5, color: '#8a8a92', marginBottom: 8 }}>{step.handle}</div>
      <div style={{ minHeight: 50, maxHeight: 62, overflow: 'hidden', fontSize: 11.5, lineHeight: 1.42, color: '#f4f4f5', marginBottom: 11 }}>{step.brief}</div>
      {step.stopHint && <div style={{ fontSize: 11, lineHeight: 1.35, color: '#ffcc77', background: 'rgba(227,179,65,0.09)', border: '1px solid rgba(227,179,65,0.24)', borderRadius: 7, padding: '7px 8px', marginBottom: 10 }}>{step.stopHint}</div>}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <div style={{ flex: 1, height: 4, borderRadius: 3, background: 'rgba(255,255,255,0.08)', overflow: 'hidden' }}><div style={cssToObj(step.barFillStyle)} /></div>
        <span style={{ fontFamily: "'IBM Plex Mono',monospace", fontSize: 10.5, color: '#8a8a92' }}>{step.turnsLabel}</span>
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, fontFamily: "'IBM Plex Mono',monospace", fontSize: 10.5, color: '#6a6a72' }}>
        <span>{step.toolsLabel}</span>
        <span>{step.attemptLabel || (step.isCurrent ? 'current' : 'step')}</span>
      </div>
    </div>
  )
}

function PipelineConnector() {
  return (
    <div style={{ display: 'flex', alignItems: 'center', color: '#3a4250', flexShrink: 0 }}>
      <svg viewBox="0 0 42 24" width="42" height="24" fill="none"><path d="M3 12 H35 M29 6 L35 12 L29 18" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" /></svg>
    </div>
  )
}
