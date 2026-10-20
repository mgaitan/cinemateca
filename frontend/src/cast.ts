import { useEffect, useRef, useState } from 'react'
import * as backend from './api'
import type { Film, Subtitle } from './types'

interface CastData { fileId: string; compatible: boolean; offset: number; duration: number }
export interface CastOptions { compatible?: boolean; position?: number; tracks?: Subtitle[]; active?: string }
interface RemoteState extends CastData {
  title: string; device: string; position: number; paused: boolean; canPause: boolean; canSeek: boolean
  volume: number; muted: boolean; canControlVolume: boolean; tracks: chrome.cast.media.Track[]; active: number|undefined
}

let sdk:Promise<void>|undefined
function loadSdk() {
  if(sdk)return sdk
  sdk=new Promise<void>((resolve,reject)=>{
    const initialize=(available:boolean)=>{
      if(!available){reject(new Error('Google Cast no está disponible en este navegador.'));return}
      cast.framework.CastContext.getInstance().setOptions({
        receiverApplicationId:chrome.cast.media.DEFAULT_MEDIA_RECEIVER_APP_ID,
        autoJoinPolicy:chrome.cast.AutoJoinPolicy.ORIGIN_SCOPED,
        resumeSavedSession:true,
      })
      resolve()
    }
    if(window.cast?.framework){initialize(true);return}
    window.__onGCastApiAvailable=initialize
    const script=document.createElement('script')
    script.src='https://www.gstatic.com/cv/js/sender/v1/cast_sender.js?loadCastFramework=1'
    script.async=true
    script.onerror=()=>reject(new Error('No se pudo cargar Google Cast.'))
    document.head.appendChild(script)
  })
  return sdk
}

const spanish=(label:string)=>/(?:^|[. _-])(es|esp|spa|spanish|espanol|castellano|latino)(?:[. _-]|$)/i.test(label.normalize('NFD').replace(/\p{Diacritic}/gu,''))
const mediaData=(media?:chrome.cast.media.MediaInfo|null)=>media?.customData as CastData|undefined

export function useCast() {
  const [ready,setReady]=useState(false)
  const [available,setAvailable]=useState(false)
  const [busy,setBusy]=useState(false)
  const [error,setError]=useState('')
  const [remote,setRemote]=useState<RemoteState|null>(null)
  const runtime=useRef<{context:cast.framework.CastContext;player:cast.framework.RemotePlayer;controller:cast.framework.RemotePlayerController}|null>(null)

  useEffect(()=>{
    let disposed=false
    let cleanup=()=>{}
    void loadSdk().then(()=>{
      if(disposed)return
      const context=cast.framework.CastContext.getInstance()
      const player=new cast.framework.RemotePlayer()
      const controller=new cast.framework.RemotePlayerController(player)
      runtime.current={context,player,controller}
      const update=()=>{
        const session=context.getCurrentSession()
        const media=session?.getMediaSession()
        const data=mediaData(media?.media)
        setAvailable(context.getCastState()!==cast.framework.CastState.NO_DEVICES_AVAILABLE)
        if(media?.idleReason===chrome.cast.media.IdleReason.ERROR)setError('Chromecast no pudo reproducir este archivo. Probá el otro modo de audio; el códec de video también debe ser compatible con tu Chromecast.')
        if(!session || !player.isMediaLoaded || !data?.fileId){setRemote(null);return}
        setRemote({...data,duration:data.duration || data.offset+(player.duration || 0),title:player.title,device:session.getCastDevice().friendlyName,
          position:data.offset+(player.currentTime || 0),paused:player.isPaused,canPause:player.canPause,
          canSeek:data.compatible?data.duration>0:player.canSeek,volume:player.volumeLevel,
          muted:player.isMuted,canControlVolume:player.canControlVolume,
          tracks:media?.media?.tracks?.filter(track=>track.type===chrome.cast.media.TrackType.TEXT) || [],
          active:media?.activeTrackIds?.[0],
        })
      }
      context.addEventListener(cast.framework.CastContextEventType.CAST_STATE_CHANGED,update)
      context.addEventListener(cast.framework.CastContextEventType.SESSION_STATE_CHANGED,update)
      controller.addEventListener(cast.framework.RemotePlayerEventType.ANY_CHANGE,update)
      cleanup=()=>{
        context.removeEventListener(cast.framework.CastContextEventType.CAST_STATE_CHANGED,update)
        context.removeEventListener(cast.framework.CastContextEventType.SESSION_STATE_CHANGED,update)
        controller.removeEventListener(cast.framework.RemotePlayerEventType.ANY_CHANGE,update)
        runtime.current=null
      }
      setReady(true);update()
    }).catch(()=>{if(!disposed)setReady(false)})
    return ()=>{disposed=true;cleanup()}
  },[])

  function showError(error:unknown) {
    if(error===chrome.cast.ErrorCode.CANCEL)return
    setError(error instanceof Error?error.message:'No se pudo transmitir al Chromecast. Comprobá la conexión y la compatibilidad del archivo.')
  }

  async function start(film:Film,options:CastOptions={}) {
    const current=runtime.current
    if(!current || busy)return false
    setBusy(true);setError('')
    try {
      if(!current.context.getCurrentSession())await current.context.requestSession()
      const session=current.context.getCurrentSession()
      if(!session)return false
      const compatible=options.compatible ?? true
      const duration=film.durationSeconds || film.duration*60
      const position=Math.max(0,Math.min(options.position || 0,duration?Math.max(0,duration-1):172800))
      const offset=compatible?position:0
      const [ticket,subtitles]=await Promise.all([
        backend.castTicket(film.id),
        options.tracks?Promise.resolve({tracks:options.tracks}):backend.subtitles(film.id).catch(()=>({tracks:[]})),
      ])
      const url=new URL(`/api/${compatible?'play':'stream'}/${encodeURIComponent(film.id)}`,window.location.origin)
      url.searchParams.set('cast_token',ticket.token)
      if(compatible)url.searchParams.set('start',String(offset))
      const contentType=compatible?'video/mp4':film.filename?.toLowerCase().endsWith('.webm')?'video/webm':/\.(mp4|m4v)$/i.test(film.filename || '')?'video/mp4':film.mime || 'video/mp4'
      const media=new chrome.cast.media.MediaInfo(url.href,contentType)
      media.streamType=chrome.cast.media.StreamType.BUFFERED
      if(duration)media.duration=duration-offset
      media.customData={fileId:film.id,compatible,offset,duration} satisfies CastData
      const metadata=new chrome.cast.media.MovieMediaMetadata()
      metadata.title=film.title
      metadata.subtitle=[film.year,film.director].filter(Boolean).join(' · ')
      if(film.poster)metadata.images=[new chrome.cast.Image(film.poster)]
      media.metadata=metadata
      media.tracks=subtitles.tracks.map((subtitle,index)=>{
        const track=new chrome.cast.media.Track(index+1,chrome.cast.media.TrackType.TEXT)
        const source=new URL(subtitle.src,window.location.origin)
        source.searchParams.set('cast_token',ticket.token);source.searchParams.set('offset',String(offset))
        track.trackContentId=source.href;track.trackContentType='text/vtt';track.name=subtitle.label
        track.subtype=chrome.cast.media.TextTrackType.SUBTITLES
        if(spanish(subtitle.label))track.language='es'
        return track
      })
      const active=options.active===undefined?subtitles.tracks.find(track=>spanish(track.label))?.id || subtitles.tracks[0]?.id:options.active
      const activeIndex=subtitles.tracks.findIndex(track=>track.id===active)
      const request=new chrome.cast.media.LoadRequest(media)
      request.autoplay=true;request.currentTime=compatible?0:position
      request.activeTrackIds=activeIndex>=0?[activeIndex+1]:[]
      await session.loadMedia(request)
      return true
    } catch(error){showError(error);return false}
    finally{setBusy(false)}
  }

  async function seek(position:number) {
    const current=runtime.current
    const session=current?.context.getCurrentSession()
    const media=session?.getMediaSession()
    const previous=media?.media
    const data=mediaData(previous)
    if(!current || !session || !previous || !data || busy)return
    const duration=data.duration || data.offset+(current.player.duration || 0)
    const target=Math.max(0,Math.min(position,duration?Math.max(0,duration-1):172800))
    if(!data.compatible){current.player.currentTime=target;current.controller.seek();return}
    setBusy(true);setError('')
    try {
      const url=new URL(previous.contentId)
      url.searchParams.set('start',String(target))
      const updated=new chrome.cast.media.MediaInfo(url.href,previous.contentType)
      updated.metadata=previous.metadata;updated.streamType=previous.streamType
      updated.duration=data.duration-target
      updated.customData={...data,offset:target}
      updated.tracks=previous.tracks?.map(track=>{
        const source=new URL(track.trackContentId)
        source.searchParams.set('offset',String(target))
        return Object.assign(new chrome.cast.media.Track(track.trackId,track.type),track,{trackContentId:source.href})
      })
      const request=new chrome.cast.media.LoadRequest(updated)
      request.autoplay=!current.player.isPaused;request.activeTrackIds=media?.activeTrackIds || []
      await session.loadMedia(request)
    } catch(error){showError(error)}
    finally{setBusy(false)}
  }

  function selectSubtitle(trackId:number|undefined) {
    const media=runtime.current?.context.getCurrentSession()?.getMediaSession()
    if(!media)return
    media.editTracksInfo(new chrome.cast.media.EditTracksInfoRequest(trackId===undefined?[]:[trackId]),
      ()=>setRemote(previous=>previous?{...previous,active:trackId}:previous),showError)
  }

  function setVolume(volume:number) {
    if(!runtime.current)return
    runtime.current.player.volumeLevel=volume
    runtime.current.controller.setVolumeLevel()
  }

  return {ready,available,busy,error,remote,start,seek,selectSubtitle,setVolume,
    clearError:()=>setError(''),
    togglePause:()=>runtime.current?.controller.playOrPause(),
    toggleMute:()=>runtime.current?.controller.muteOrUnmute(),
    stop:()=>runtime.current?.context.endCurrentSession(true),
  }
}

export type Casting = ReturnType<typeof useCast>
