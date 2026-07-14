import Box from '../lib/Box.jsx'
import { cssToObj } from '../lib/css.js'
import ChatBody from '../components/ChatBody.jsx'
import NewSessionButton from '../components/NewSessionButton.jsx'
import TokenEconomics from '../components/TokenEconomics.jsx'

export default function Orchestrator({ vals }) {
  return (
    <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>
      <RunRail vals={vals} />
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0, overflow: 'auto', position: 'relative' }}>
        <Header vals={vals} />
        <RunWorkspace vals={vals} />
      </div>
      <div style={{ width: 322, flexShrink: 0, borderLeft: '1px solid rgba(255,255,255,0.06)', background: '#111721', display: 'flex', flexDirection: 'column', minHeight: 0 }}>
        {vals.hasDetail && <DetailPanel vals={vals} />}
        {vals.showFeed && <FeedPanel vals={vals} />}
      </div>
    </div>
  )
}

function Header({ vals }) {
  return (
    <div style={{ display: 'flex', alignItems: 'flex-start', gap: 16, padding: '22px 26px 8px' }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 18, fontWeight: 650 }}>Orchestrator</div>
        <div style={{ fontSize: 12, color: '#7a7a82', marginTop: 3 }}>Project conversations retain their chat history and expose the latest governed agent flow.</div>
      </div>
      <div style={{ display: 'flex', gap: 18, alignItems: 'center', flexShrink: 0 }}>
        <Metric value={vals.runningCount} label="running" color="#e9e9ea" />
        <Metric value={vals.totalDone} label="completed" color="#54c79a" />
      </div>
    </div>
  )
}

function Metric({ value, label, color }) {
  return (
    <div style={{ textAlign: 'right' }}>
      <div style={{ fontFamily: "'IBM Plex Mono',monospace", fontSize: 20, fontWeight: 650, color, lineHeight: 1 }}>{value}</div>
      <div style={{ fontSize: 10, color: '#6a6a72', textTransform: 'uppercase', letterSpacing: '0.08em', marginTop: 2 }}>{label}</div>
    </div>
  )
}

function RunRail({ vals }) {
  return (
    <aside style={{ width: 250, flexShrink: 0, borderRight: '1px solid rgba(255,255,255,0.06)', background: '#0f151f', display: 'flex', flexDirection: 'column', minHeight: 0 }}>
      <div style={{ padding: '18px 16px 12px', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
        <div style={{ fontSize: 13, fontWeight: 650 }}>Sessions</div>
        <div style={{ marginTop: 3, fontSize: 10.5, color: '#6a6a72', lineHeight: 1.4 }}>newest first · project conversations</div>
      </div>
      <div style={{ flex: 1, overflow: 'auto', padding: 12, display: 'flex', flexDirection: 'column', gap: 9 }}>
        {vals.runs.length ? vals.runs.map((run) => (
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
          <div style={{ border: '1px dashed rgba(255,255,255,0.12)', borderRadius: 10, padding: 14, color: '#7a7a82', fontSize: 12, lineHeight: 1.45 }}>
            No sessions yet. Send a message to start one.
          </div>
        )}
      </div>
    </aside>
  )
}

function RunWorkspace({ vals }) {
  return (
    <main style={{ flex: 1, minHeight: 0, padding: '14px 26px 24px', display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ position: 'absolute', inset: 0, pointerEvents: 'none', backgroundImage: 'repeating-linear-gradient(45deg, rgba(255,255,255,0.035) 0 1px, transparent 1px 26px), repeating-linear-gradient(-45deg, rgba(255,255,255,0.035) 0 1px, transparent 1px 26px)', WebkitMaskImage: 'radial-gradient(ellipse 68% 58% at 55% 36%, #000 22%, transparent 74%)', maskImage: 'radial-gradient(ellipse 68% 58% at 55% 36%, #000 22%, transparent 74%)' }} />
      <DriverCard vals={vals} />
      <Timeline vals={vals} />
    </main>
  )
}

function DriverCard({ vals }) {
  const summary = vals.driverSummary || {
    title: 'Run summary',
    countLine: vals.runStatusSummary || 'No run selected',
    focusLine: '',
    alertLine: '',
    metaLine: vals.activeModel,
  }
  return (
    <div style={{ position: 'relative', zIndex: 1, alignSelf: 'center', width: 'min(460px, 100%)', background: '#19212f', border: '1px solid rgba(255,155,61,0.4)', borderRadius: 14, padding: '15px 20px', boxShadow: '0 18px 42px rgba(0,0,0,0.28)' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 9, marginBottom: 8 }}>
        <span style={cssToObj(vals.driverDotStyle)} />
        <span style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.1em', color: '#ff9b3d', fontWeight: 650 }}>{summary.title}</span>
      </div>
      <div style={{ textAlign: 'center', fontFamily: "'IBM Plex Mono',monospace", fontSize: 14, fontWeight: 650, color: '#fff' }}>{summary.countLine}</div>
      <div style={{ textAlign: 'center', marginTop: 7, fontSize: 12.5, color: '#f4f4f5', lineHeight: 1.4 }}>{summary.focusLine}</div>
      {summary.alertLine && <div style={{ margin: '11px auto 0', width: 'fit-content', maxWidth: '100%', fontSize: 11.5, color: '#ffcc77', lineHeight: 1.35, background: 'rgba(227,179,65,0.09)', border: '1px solid rgba(227,179,65,0.24)', borderRadius: 7, padding: '6px 9px' }}>{summary.alertLine}</div>}
      <div style={{ textAlign: 'center', fontFamily: "'IBM Plex Mono',monospace", fontSize: 10.5, color: '#7a7a82', marginTop: 10 }}>{summary.metaLine}</div>
      <TokenEconomics economics={summary.economics} />
    </div>
  )
}

function Timeline({ vals }) {
  const steps = vals.activeRunSteps || []
  return (
    <section style={{ position: 'relative', zIndex: 1, flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', background: 'rgba(17,23,33,0.78)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 12, overflow: 'hidden', boxShadow: '0 14px 34px rgba(0,0,0,0.24)' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 12, padding: '13px 16px 9px', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
        <div style={{ minWidth: 0 }}>
          <span style={{ fontSize: 13, fontWeight: 650 }}>{vals.sessionTitle}</span>
          <span style={{ marginLeft: 10, fontFamily: "'IBM Plex Mono',monospace", fontSize: 11, color: '#7a7a82' }}>{vals.sessionSubtitle}</span>
        </div>
        <span style={{ fontFamily: "'IBM Plex Mono',monospace", fontSize: 10.5, color: '#ff9b3d', whiteSpace: 'nowrap' }}>agent flow</span>
      </div>
      <div style={{ flex: 1, minHeight: 0, overflow: 'auto', padding: 18 }}>
        {steps.length ? (
          <div style={{ display: 'flex', alignItems: 'stretch', gap: 16, minHeight: 210 }}>
            {steps.map((st) => (
              <div key={st.handle} style={{ display: 'flex', alignItems: 'center', gap: 16, flexShrink: 0 }}>
                <StepCard step={st} />
                {st.showConnector && <Connector />}
              </div>
            ))}
          </div>
        ) : (
          <div style={{ height: '100%', minHeight: 220, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#7a7a82', fontSize: 12 }}>
            No agent activity has been recorded for this session yet.
          </div>
        )}
      </div>
    </section>
  )
}

function StepCard({ step }) {
  return (
    <Box css={step.cardStyle} onClick={step.onSelect} hover="border-color:rgba(255,255,255,0.2)">
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginBottom: 10 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 7, minWidth: 0 }}>
          <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', minWidth: 28, height: 20, padding: '0 6px', borderRadius: 6, fontFamily: "'IBM Plex Mono',monospace", fontSize: 10.5, fontWeight: 650, color: '#cfcfd4', background: step.isCurrent ? 'rgba(255,155,61,0.13)' : 'rgba(255,255,255,0.06)', border: '1px solid ' + (step.isCurrent ? 'rgba(255,155,61,0.42)' : 'rgba(255,255,255,0.12)') }}>{step.orderLabel}</span>
          <span style={cssToObj(step.roleChipStyle)}>{step.role}</span>
        </div>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontFamily: "'IBM Plex Mono',monospace", fontSize: 10.5, color: step.statusColor }}><span style={cssToObj(step.dotStyle)} />{step.statusLabel}</span>
      </div>
      <div style={{ fontFamily: "'IBM Plex Mono',monospace", fontSize: 10.5, color: '#8a8a92', marginBottom: 8 }}>{step.handle}</div>
      <div style={{ minHeight: 54, maxHeight: 70, overflow: 'hidden', fontSize: 12, lineHeight: 1.45, color: '#f4f4f5', marginBottom: 12 }}>{step.brief}</div>
      {step.stopHint && <div style={{ fontSize: 11, lineHeight: 1.35, color: '#ffcc77', background: 'rgba(227,179,65,0.09)', border: '1px solid rgba(227,179,65,0.24)', borderRadius: 7, padding: '7px 8px', marginBottom: 10 }}>{step.stopHint}</div>}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <div style={{ flex: 1, height: 4, borderRadius: 3, background: 'rgba(255,255,255,0.08)', overflow: 'hidden' }}><div style={cssToObj(step.barFillStyle)} /></div>
        <span style={{ fontFamily: "'IBM Plex Mono',monospace", fontSize: 10.5, color: '#8a8a92' }}>{step.turnsLabel}</span>
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, fontFamily: "'IBM Plex Mono',monospace", fontSize: 10.5, color: '#6a6a72' }}>
        <span>{step.toolsLabel}</span>
        <span>{step.attemptLabel || (step.isCurrent ? 'current' : 'step')}</span>
      </div>
    </Box>
  )
}

function Connector() {
  return (
    <div style={{ display: 'flex', alignItems: 'center', color: '#3a4250', flexShrink: 0 }}>
      <svg viewBox="0 0 42 24" width="42" height="24" fill="none"><path d="M3 12 H35 M29 6 L35 12 L29 18" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" /></svg>
    </div>
  )
}

function DetailPanel({ vals }) {
  const d = vals.detail
  const modelLabel = d.model || 'not captured'
  const profileLabel = d.profile || 'audit only'
  return (
    <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0, height: '100%' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '15px 18px', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
        <Box as="button" onClick={vals.clearSelect} css="display:flex;align-items:center;justify-content:center;width:26px;height:26px;border-radius:7px;border:1px solid rgba(255,255,255,0.1);background:#232c3c;color:#9b9ba2;cursor:pointer" hover="color:#fff"><svg viewBox="0 0 24 24" width="15" height="15" fill="none"><path d="M14 6 L8 12 L14 18" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg></Box>
        <span style={{ fontSize: 13, fontWeight: 650 }}>Step detail</span>
      </div>
      <div style={{ flex: 1, overflow: 'auto', padding: 18 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
          <span style={cssToObj(d.roleChipStyle)}>{d.role}</span>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 11, color: d.statusColor, fontFamily: "'IBM Plex Mono',monospace" }}><span style={cssToObj(d.dotStyle)} />{d.statusLabel}</span>
        </div>
        <div style={{ fontFamily: "'IBM Plex Mono',monospace", fontSize: 13, color: '#fff', marginBottom: 4 }}>{d.handle}</div>
        <div style={{ fontFamily: "'IBM Plex Mono',monospace", fontSize: 10.5, color: '#6a6a72', marginBottom: 14 }}>{d.workerLabel} · audit {d.auditId} · parent {d.parent}</div>
        <div style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.09em', color: '#6a6a72', marginBottom: 6 }}>Model profile</div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontFamily: "'IBM Plex Mono',monospace", fontSize: 12.5, color: '#e9e9ea', marginBottom: 18, padding: '11px 13px', background: '#19212f', border: '1px solid rgba(255,255,255,0.06)', borderRadius: 8 }}><span style={{ width: 7, height: 7, borderRadius: '50%', background: d.modelColor, flexShrink: 0 }} />{modelLabel}<span style={{ color: '#6a6a72' }}>· {profileLabel}</span></div>
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
          <span style={{ fontSize: 13, fontWeight: 650 }}>Chat · driver</span>
          <span style={{ fontSize: 10, color: '#6a6a72' }}>final result and process summaries</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <NewSessionButton onClick={vals.onNewSession} disabled={vals.clearDriverDisabled} />
          <span style={{ fontFamily: "'IBM Plex Mono',monospace", fontSize: 10, color: '#ff9b3d', display: 'inline-flex', alignItems: 'center', gap: 6 }}><span style={{ width: 6, height: 6, borderRadius: '50%', background: '#ff9b3d', animation: 'pulse 1.6s ease-in-out infinite' }} />driver</span>
        </div>
      </div>
      <ChatBody vals={vals} />
    </div>
  )
}
