import { useState } from 'react'
import { Cast, Loader2, Pause, Play, Square, Volume2, VolumeX, X } from 'lucide-react'
import type { Casting } from './cast'

const clock=(seconds:number)=>new Date(Math.max(0,seconds)*1000).toISOString().slice(11,19)

export default function CastControls({casting}:{casting:Casting}) {
  const [seeking,setSeeking]=useState<number|null>(null)
  const remote=casting.remote
  return <>
    {casting.error && <div className="error-banner" role="alert"><span>{casting.error}</span><button onClick={casting.clearError} aria-label="Cerrar error de Chromecast"><X size={18}/></button></div>}
    {remote && <section className="cast-controls" aria-label="Reproducción en Chromecast">
      <div className="cast-heading"><Cast size={20}/><div><strong>{remote.title}</strong><p>En {remote.device} · {remote.compatible?'Audio compatible':'Audio original'}</p></div>{casting.busy && <Loader2 size={18} className="spin"/>}</div>
      <div className="cast-actions">
        <button className="secondary-button" onClick={casting.togglePause} disabled={!remote.canPause || casting.busy}>{remote.paused?<Play size={17}/>:<Pause size={17}/>} {remote.paused?'Reanudar':'Pausar'}</button>
        <button className="secondary-button" onClick={casting.stop}><Square size={17}/>Detener transmisión</button>
        {remote.canControlVolume && <><button className="secondary-button" onClick={casting.toggleMute} aria-label={remote.muted?'Activar sonido':'Silenciar'}>{remote.muted?<VolumeX size={17}/>:<Volume2 size={17}/>}</button><input type="range" min="0" max="1" step="0.05" value={remote.volume} aria-label="Volumen de Chromecast" onChange={event=>casting.setVolume(Number(event.target.value))}/></>}
        {remote.tracks.length>0 && <label>Subtítulos <select aria-label="Subtítulos de Chromecast" value={remote.active ?? ''} onChange={event=>casting.selectSubtitle(event.target.value?Number(event.target.value):undefined)}><option value="">Desactivados</option>{remote.tracks.map(track=><option key={track.trackId} value={track.trackId}>{track.name}</option>)}</select></label>}
      </div>
      {remote.duration>0 && <div className="playback-timeline"><input type="range" aria-label="Posición en Chromecast" min="0" max={Math.max(0,remote.duration-1)} step="1" value={seeking ?? Math.min(remote.position,remote.duration-1)} disabled={!remote.canSeek || casting.busy} onChange={event=>setSeeking(Number(event.target.value))} onPointerUp={event=>{setSeeking(null);void casting.seek(Number(event.currentTarget.value))}} onKeyUp={event=>{if(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End','PageUp','PageDown'].includes(event.key)){setSeeking(null);void casting.seek(Number(event.currentTarget.value))}}}/><span>{clock(seeking ?? remote.position)} / {clock(remote.duration)}</span></div>}
    </section>}
  </>
}
