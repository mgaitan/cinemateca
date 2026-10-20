import { useEffect, useState } from 'react'
import { ArrowLeft, Cast, CirclePlay, Download, Heart, Loader2, X } from 'lucide-react'
import * as backend from './api'
import Poster from './Poster'
import Player from './Player'
import type { Casting } from './cast'
import type { Film, FilmEdit, Session } from './types'

const movieUrl=(id:string)=>`/peliculas/${encodeURIComponent(id)}`
const versionLabel=(film:Film)=>[film.filename,film.category?.includes('/')?film.category.split('/').slice(-1)[0]:'',film.duration?`${film.duration} min`:'',film.size?`${(film.size/1024**3).toFixed(1)} GB`:''].filter(Boolean).join(' · ')

export default function FilmPage({id,viewer,casting}:{id:string;viewer:Session;casting:Casting}) {
  const [film,setFilm]=useState<Film|null>(null)
  const [versions,setVersions]=useState<Film[]>([])
  const [loading,setLoading]=useState(true)
  const [error,setError]=useState('')
  const [working,setWorking]=useState(false)
  const [playing,setPlaying]=useState(false)
  const [editing,setEditing]=useState(false)
  const [draft,setDraft]=useState<FilmEdit>({})

  useEffect(()=>{
    const controller=new AbortController()
    void backend.movie(id,controller.signal).then(data=>{
      if(controller.signal.aborted)return
      setFilm(data.movie);setVersions(data.versions);document.title=`${data.movie.title} · Cinemateca`
    }).catch(error=>{if(!controller.signal.aborted)setError(error instanceof Error?error.message:'No se pudo cargar la película.')})
      .finally(()=>{if(!controller.signal.aborted)setLoading(false)})
    return ()=>controller.abort()
  },[id])

  function applyEdit(changes:FilmEdit) {
    setFilm(previous=>previous?{...previous,...changes}:previous)
    setVersions(previous=>previous.map(version=>version.id===id?{...version,...changes}:version))
    if(changes.title)document.title=`${changes.title} · Cinemateca`
  }

  async function toggleFavorite() {
    if(!film)return
    setWorking(true);setError('')
    try {await backend.edit(id,{favorite:!film.favorite});applyEdit({favorite:!film.favorite})}
    catch(error){setError(error instanceof Error?error.message:'No se pudo guardar el favorito.')}
    finally{setWorking(false)}
  }

  async function saveEdit(event:React.FormEvent) {
    event.preventDefault()
    setWorking(true);setError('')
    try {await backend.edit(id,draft);applyEdit(draft);setEditing(false)}
    catch(error){setError(error instanceof Error?error.message:'No se pudieron guardar los cambios.')}
    finally{setWorking(false)}
  }

  return <main className="film-page">
    <a className="back-link" href="/"><ArrowLeft size={18}/>Volver al catálogo</a>
    {error && <p className="film-error" role="alert">{error}</p>}
    {loading?<div className="empty-state" role="status"><Loader2 size={24} className="spin"/><p>Cargando película…</p></div>:film && <>
      <div className="film-details"><Poster film={film}/><div className="film-description">
        <h1>{film.title}</h1><p className="film-facts">{[film.year,film.country,film.duration?`${film.duration} min`:''].filter(Boolean).join(' · ')}</p>
        {film.director && <p><strong>Dirección:</strong> {film.director}</p>}{!!film.cast?.length && <p><strong>Reparto:</strong> {film.cast.join(', ')}</p>}
        {film.genre && <p>{film.genre}</p>}{film.popularity != null && <p className="muted">Popularidad TMDB: {film.popularity.toLocaleString('es-AR',{maximumFractionDigits:1})}</p>}
        {film.overview && <p className="synopsis">{film.overview}</p>}
        {versions.length>1 && <label className="form-field version-select">Versión<select aria-label="Elegir versión" value={id} onChange={event=>window.location.assign(movieUrl(event.target.value))}>{versions.map(version=><option key={version.id} value={version.id}>{versionLabel(version)}</option>)}</select></label>}
        <div className="detail-actions">
          <button className="primary-button" disabled={!film.canDownload || playing} onClick={()=>setPlaying(true)}><CirclePlay size={18}/>Reproducir</button>
          {film.canDownload && <a className="secondary-button" href={`/api/stream/${encodeURIComponent(id)}?download=true`} download={film.filename || true}><Download size={18}/>Descargar</a>}
          {film.canDownload && casting.available && <button className="secondary-button" disabled={casting.busy} onClick={async()=>{if(await casting.start(film))setPlaying(false)}}><Cast size={18}/>{casting.busy?'Conectando…':'Reproducir en Chromecast'}</button>}
          {viewer.can_manage && <><button className="secondary-button" disabled={working} onClick={()=>void toggleFavorite()} aria-pressed={film.favorite}><Heart size={18} fill={film.favorite?'currentColor':'none'}/>Favorito</button><button className="secondary-button" onClick={()=>{setDraft({title:film.title,year:film.year,director:film.director,genre:film.genre,overview:film.overview,poster:film.poster,backdrop:film.backdrop});setEditing(true)}}>Editar</button></>}
        </div>
        <p className="file-note"><a href={`https://drive.google.com/file/d/${encodeURIComponent(id)}/view`} target="_blank" rel="noopener noreferrer">{film.filename || 'Ver archivo en Drive'}</a></p>{!film.canDownload && <p className="file-note">El propietario deshabilitó la descarga.</p>}
      </div></div>
      {playing && <section className="film-player" aria-label={`Reproduciendo ${film.title}`}><div className="section-heading"><h2>Reproduciendo {film.title}</h2><button className="secondary-button" onClick={()=>setPlaying(false)}><X size={17}/>Cerrar reproductor</button></div><Player key={id} film={film} casting={casting} onCastSuccess={()=>setPlaying(false)}/></section>}
      {editing && <section className="film-edit" aria-label="Editar película"><div className="section-heading"><h2>Editar película</h2><button className="secondary-button" onClick={()=>setEditing(false)}><X size={17}/>Cancelar</button></div><form onSubmit={event=>void saveEdit(event)}><div className="edit-fields">{([['title','Título'],['year','Año'],['director','Director'],['genre','Géneros (separados por comas)'],['poster','URL del póster'],['backdrop','URL de imagen']] as const).map(([field,label])=><label className="form-field" key={field}>{label}<input value={draft[field] ?? ''} required={field==='title'} maxLength={field==='year'?4:field==='poster'||field==='backdrop'?1000:field==='genre'?100:200} onChange={event=>setDraft(previous=>({...previous,[field]:event.target.value}))}/></label>)}<label className="form-field">Sinopsis<textarea rows={5} maxLength={2500} value={draft.overview || ''} onChange={event=>setDraft(previous=>({...previous,overview:event.target.value}))}/></label></div><button className="primary-button" disabled={working}>Guardar</button></form></section>}
    </>}
  </main>
}
