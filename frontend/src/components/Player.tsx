import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import { fmt } from '../format'
import { IconPause, IconPlay } from './ui'
import { tr } from '../i18n'

export interface PlayerHandle { seek: (sec: number, play?: boolean) => void }
interface Props {
  meetingId: string; audioUrl: string | null; duration: number
  onTime: (sec: number, playing: boolean) => void
}

const BARS = 64

function fallbackBars(seed: number): number[] {
  let s = seed || 7
  const rnd = () => { s = (s * 9301 + 49297) % 233280; return s / 233280 }
  return Array.from({ length: BARS }, (_, i) => Math.min(1, (18 + Math.round((Math.sin(i / 3.2) * 0.3 + 0.5 + rnd() * 0.5) * 60)) / 100))
}

export const Player = forwardRef<PlayerHandle, Props>(function Player({ meetingId, audioUrl, duration, onTime }, ref) {
  const audio = useRef<HTMLAudioElement>(null)
  const [pos, setPos] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [total, setTotal] = useState(duration)
  const [bars, setBars] = useState<number[] | null>(null)
  const [failed, setFailed] = useState(false)
  const pending = useRef<number | null>(null)
  const seed = useMemo(() => meetingId.charCodeAt(0) * 9301 + duration, [meetingId, duration])

  useEffect(() => {
    let alive = true
    setBars(null)
    if (audioUrl) api.get<{ bars: number[] }>(`/api/meetings/${meetingId}/waveform`).then(r => { if (alive) setBars(r.bars) }).catch(() => undefined)
    return () => { alive = false }
  }, [meetingId, audioUrl])

  useEffect(() => { onTime(pos, playing) }, [pos, playing]) // eslint-disable-line react-hooks/exhaustive-deps

  const apply = (sec: number) => {
    const el = audio.current
    const clamped = Math.max(0, Math.min(sec, total || sec))
    setPos(clamped)
    if (el && audioUrl) {
      if (el.readyState >= 1) el.currentTime = clamped
      else pending.current = clamped
    }
  }

  useImperativeHandle(ref, () => ({
    seek(sec, play) {
      apply(sec)
      if (play) void start()
    },
  }))

  const start = async () => {
    const el = audio.current
    if (!el || !audioUrl || failed) return
    try { await el.play() } catch { setPlaying(false) }
  }
  const toggle = () => {
    const el = audio.current
    if (!el) return
    if (el.paused) {
      if (total && el.currentTime >= total - 0.2) apply(0)
      void start()
    } else el.pause()
  }

  const dur = total || duration
  const shown = bars && bars.length ? bars : fallbackBars(seed)
  const progress = dur ? pos / dur : 0
  const enabled = !!audioUrl && !failed

  return (
    <div className="player">
      {audioUrl && (
        <audio ref={audio} src={audioUrl} preload="metadata"
          onLoadedMetadata={e => {
            const d = e.currentTarget.duration
            if (isFinite(d) && d > 0) setTotal(d)
            if (pending.current != null) { e.currentTarget.currentTime = pending.current; pending.current = null }
          }}
          onTimeUpdate={e => setPos(e.currentTarget.currentTime)}
          onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onEnded={() => setPlaying(false)}
          onError={() => { setFailed(true); setPlaying(false) }} />
      )}
      <button className="play" onClick={toggle} disabled={!enabled} aria-label={playing ? tr('Пауза') : tr('Воспроизвести')}>
        {playing ? <IconPause /> : <IconPlay style={{ marginLeft: 2 }} />}
      </button>
      <div className="wave" role="slider" aria-label={tr('Позиция в записи')} aria-valuemin={0} aria-valuemax={Math.round(dur)} aria-valuenow={Math.round(pos)}>
        {shown.map((v, i) => (
          <div key={i} onClick={() => apply((i / shown.length) * dur)}>
            <i className={i / shown.length < progress ? 'on' : ''} style={{ height: Math.max(14, Math.round(v * 100)) + '%' }} />
          </div>
        ))}
      </div>
      <div className="player-time mono num">{fmt(pos)} / {fmt(dur)}</div>
    </div>
  )
})
