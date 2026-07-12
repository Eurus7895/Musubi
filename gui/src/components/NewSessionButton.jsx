export default function NewSessionButton({ disabled, label = 'New session', onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      title={disabled ? 'Wait for the active run before starting a new session' : 'Start a fresh isolated session'}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 7,
        height: 32,
        padding: '0 12px',
        borderRadius: 9,
        border: '1px solid ' + (disabled ? 'rgba(255,255,255,0.08)' : 'rgba(255,155,61,0.42)'),
        background: disabled ? 'rgba(255,255,255,0.03)' : 'rgba(255,155,61,0.10)',
        color: disabled ? '#4f5665' : '#ffc07f',
        fontFamily: "'IBM Plex Mono',monospace",
        fontSize: 11,
        fontWeight: 650,
        cursor: disabled ? 'not-allowed' : 'pointer',
        whiteSpace: 'nowrap',
      }}
    >
      <span aria-hidden="true" style={{ fontSize: 17, lineHeight: 1 }}>＋</span>
      <span>{label}</span>
    </button>
  )
}
