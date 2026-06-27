import Box from '../lib/Box.jsx'

// Left activity bar: woven-knot logo + the six view nav buttons + settings.
export default function ActivityBar({ vals }) {
  return (
    <div style={{ width: 60, flexShrink: 0, background: '#111721', borderRight: '1px solid rgba(255,255,255,0.06)', display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '14px 0', gap: 6 }}>
      <div style={{ width: 36, height: 36, display: 'flex', alignItems: 'center', justifyContent: 'center', marginBottom: 10 }}>
        <svg viewBox="0 0 40 40" width="32" height="32" fill="none">
          <g strokeLinecap="round" fill="none" strokeWidth="2.8">
            <path d="M6 25 H22 M28 25 H34" stroke="#e9e9ea" />
            <path d="M6 15 H12 M18 15 H34" stroke="#e9e9ea" />
            <path d="M25 6 V12 M25 18 V34" stroke="#e9e9ea" />
            <path d="M15 6 V22 M15 28 V34" stroke="#ff9b3d" />
          </g>
        </svg>
      </div>

      <Box as="button" onClick={vals.selOrch} title="Orchestrator" css={vals.orchNav} hover="color:#cfcfd4">
        <svg viewBox="0 0 24 24" width="20" height="20" fill="none"><path d="M5 6 L12 11 M12 4 L12 11 M19 6 L12 11 M12 11 L12 18" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" /><circle cx="5" cy="6" r="1.5" fill="currentColor" /><circle cx="12" cy="4" r="1.5" fill="currentColor" /><circle cx="19" cy="6" r="1.5" fill="currentColor" /><circle cx="12" cy="11" r="2.1" fill="currentColor" /><circle cx="12" cy="18" r="1.6" fill="currentColor" /></svg>
      </Box>
      <Box as="button" onClick={vals.selPipe} title="Run pipeline" css={vals.pipeNav} hover="color:#cfcfd4">
        <svg viewBox="0 0 24 24" width="20" height="20" fill="none"><circle cx="5" cy="12" r="2.1" stroke="currentColor" strokeWidth="1.6" /><circle cx="12" cy="12" r="2.1" stroke="currentColor" strokeWidth="1.6" /><circle cx="19" cy="12" r="2.1" stroke="currentColor" strokeWidth="1.6" /><path d="M7.1 12 H9.9 M14.1 12 H16.9" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" /></svg>
      </Box>
      <Box as="button" onClick={vals.selPolicy} title="Policy" css={vals.polNav} hover="color:#cfcfd4">
        <svg viewBox="0 0 24 24" width="19" height="19" fill="none"><path d="M12 3 L19 6 V11 C19 15.5 15.7 18.6 12 20.5 C8.3 18.6 5 15.5 5 11 V6 Z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" /><path d="M9 11.6 L11.2 13.8 L15 9.6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" /></svg>
      </Box>
      <Box as="button" onClick={vals.selAudit} title="Audit ledger" css={vals.audNav} hover="color:#cfcfd4">
        <svg viewBox="0 0 24 24" width="19" height="19" fill="none"><path d="M6 5 H18 M6 9.5 H18 M6 14 H18 M6 18.5 H13" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" /></svg>
      </Box>
      <Box as="button" onClick={vals.selModels} title="Model router" css={vals.modNav} hover="color:#cfcfd4">
        <svg viewBox="0 0 24 24" width="19" height="19" fill="none"><rect x="7" y="7" width="10" height="10" rx="2" stroke="currentColor" strokeWidth="1.5" /><path d="M10 4 V7 M14 4 V7 M10 17 V20 M14 17 V20 M4 10 H7 M4 14 H7 M17 10 H20 M17 14 H20" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" /></svg>
      </Box>
      <Box as="button" onClick={vals.selSkills} title="Skill catalog" css={vals.sklNav} hover="color:#cfcfd4">
        <svg viewBox="0 0 24 24" width="19" height="19" fill="none"><path d="M12 4 L13.4 10.6 L20 12 L13.4 13.4 L12 20 L10.6 13.4 L4 12 L10.6 10.6 Z" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" /></svg>
      </Box>

      <div style={{ flex: 1 }} />
      <Box as="button" title="Settings" css="display:flex;align-items:center;justify-content:center;width:44px;height:44px;border-radius:10px;border:none;cursor:pointer;background:transparent;color:#5a5a62" hover="color:#cfcfd4">
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none"><path d="M5 8 H19 M5 16 H19" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" /><circle cx="9" cy="8" r="2.1" fill="#111721" stroke="currentColor" strokeWidth="1.5" /><circle cx="15" cy="16" r="2.1" fill="#111721" stroke="currentColor" strokeWidth="1.5" /></svg>
      </Box>
    </div>
  )
}
