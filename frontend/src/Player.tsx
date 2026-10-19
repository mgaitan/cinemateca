import { useEffect, useRef, useState } from 'react'
import * as backend from './api'
import type { Film, Subtitle } from './types'

const clock=(seconds:number)=>new Date(Math.max(0,seconds)*1000).toISOString().slice(11,19)

export default function Player({film}:{film:Film}) {
  const videoRef=useRef<HTMLVideoElement>(null)
  const [tracks,setTracks]=useState<Subtitle[]>([])
  const [active,setActive]=useState('')
  const [subtitleError,setSubtitleError]=useState('')
  const [error,setError]=useState('')
  const [waiting,setWaiting]=useState(true)
  const [compatible,setCompatible]=useState(true)
  const [offset,setOffset]=useState(0)
  const [position,setPosition]=useState(0)
  const [seeking,setSeeking]=useState<number|null>(null)
  const duration=film.durationSeconds || film.duration*60
  const subtitleOffset=compatible?offset:0
  const source=compatible?`/api/play/${encodeURIComponent(film.id)}?start=${offset}`:`/api/stream/${encodeURIComponent(film.id)}`

  useEffect(()=>{
    const controller=new AbortController()
    void backend.subtitles(film.id,controller.signal).then(result=>{
      setTracks(result.tracks)
      const spanish=result.tracks.find(track=>/(?:^|[. _-])(es|esp|spa|spanish|espanol|castellano|latino)(?:[. _-]|$)/i.test(track.label.normalize('NFD').replace(/\p{Diacritic}/gu,'')))
      setActive(spanish?.id || result.tracks[0]?.id || '')
    }).catch(error=>{if(!controller.signal.aborted)setSubtitleError(error instanceof Error?error.message:'No se pudieron cargar los subtítulos de Drive.')})
    return ()=>controller.abort()
  },[film.id])

  useEffect(()=>{
    const video=videoRef.current
    if(!video)return
    video.muted=false
    video.volume=1
    return ()=>{video.pause();video.removeAttribute('src');video.load()}
  },[])

  function updateSubtitles() {
    const video=videoRef.current
    if(!video)return
    for(const element of Array.from(video.querySelectorAll('track')))element.track.mode=element.dataset.id===active?'showing':'disabled'
  }

  useEffect(updateSubtitles,[active,tracks,subtitleOffset])

  function seek(seconds:number) {
    const target=Math.max(0,Math.min(seconds,Math.max(0,duration-1)))
    setSeeking(null);setPosition(target);setError('')
    if(compatible){setWaiting(true);setOffset(target)}
    else if(videoRef.current)videoRef.current.currentTime=target
  }

  return <>
    <video ref={videoRef} controls autoPlay playsInline className={compatible?'video-player compatible-player':'video-player'} src={source}
      onLoadedMetadata={()=>{setWaiting(false);updateSubtitles()}} onPlaying={()=>setWaiting(false)} onWaiting={()=>setWaiting(true)}
      onTimeUpdate={()=>{const video=videoRef.current;if(video && Number.isFinite(video.currentTime))setPosition((compatible?offset:0)+video.currentTime)}}
      onError={()=>{setWaiting(false);setError('No se pudo reproducir el video con este modo de audio. Probá el modo Original o descargá el archivo.')}}>
      {tracks.map(track=><track key={`${track.id}-${subtitleOffset}`} data-id={track.id} kind="subtitles" label={track.label} src={`${track.src}?offset=${subtitleOffset}`} default={track.id===active} onLoad={updateSubtitles} onError={()=>setSubtitleError(`No se pudo cargar ${track.label}.`)}/>)}
    </video>
    {compatible && duration>0 && <div className="playback-timeline"><input type="range" aria-label="Posición de reproducción" min={0} max={Math.max(0,duration-1)} step={1} value={seeking ?? Math.min(position,duration-1)} onChange={event=>setSeeking(Number(event.target.value))} onPointerUp={event=>seek(Number(event.currentTarget.value))} onKeyUp={event=>seek(Number(event.currentTarget.value))}/><span>{clock(seeking ?? position)} / {clock(duration)}</span></div>}
    <div className="player-options">
      <label>Audio<select aria-label="Modo de audio" value={compatible?'compatible':'original'} onChange={event=>{setCompatible(event.target.value==='compatible');setOffset(0);setPosition(0);setSeeking(null);setError('');setWaiting(true)}}><option value="compatible">Compatible (AAC estéreo)</option><option value="original">Original</option></select></label>
      {tracks.length>0 && <label>Subtítulos<select aria-label="Subtítulos" value={active} onChange={event=>{setActive(event.target.value);setSubtitleError('')}}><option value="">Desactivados</option>{tracks.map(track=><option key={track.id} value={track.id}>{track.label}</option>)}</select></label>}
    </div>
    {waiting && !error && <p className="muted" role="status">Preparando reproducción…</p>}
    {error && <p role="alert">{error}</p>}{subtitleError && <p role="alert">{subtitleError}</p>}
  </>
}
