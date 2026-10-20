import { useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { CirclePlay, Download, Heart, Loader2, RefreshCw, Search, Settings, X } from 'lucide-react'
import * as backend from './api'
import Player from './Player'
import type { CatalogPage, Film, FilmEdit, Session, SyncProgress } from './types'

type Dialog = 'drive' | 'edit' | 'player' | null
type SortOrder = 'title' | 'year' | 'popularity'
const normalize = (value:string) => value.normalize('NFD').replace(/\p{Diacritic}/gu,'').toLocaleLowerCase()
const filmKey = (film:Film) => film.tmdbId?`tmdb:${film.tmdbId}`:`${normalize(film.title)}|${film.year}`
const versionLabel = (film:Film) => [film.filename,film.category?.includes('/')?film.category.split('/').slice(-1)[0]:'',film.duration?`${film.duration} min`:'',film.size?`${(film.size/1024**3).toFixed(1)} GB`:''].filter(Boolean).join(' · ')

function Poster({film}:{film:Film}) {
  const [failed,setFailed]=useState(false)
  const source=film.poster || film.backdrop
  useEffect(()=>setFailed(false),[source])
  return <div className="poster">
    {source && !failed ? <img src={source} alt="" loading="lazy" onError={()=>setFailed(true)}/> : <span>{film.title}</span>}
  </div>
}

function Modal({label,onClose,children,wide=false}:{label:string,onClose:()=>void,children:ReactNode,wide?:boolean}) {
  const element=useRef<HTMLDialogElement>(null)
  useEffect(()=>{if(element.current && !element.current.open)element.current.showModal()},[])
  return <dialog ref={element} className={wide?'modal modal-wide':'modal'} aria-label={label} onCancel={onClose} onClick={event=>{if(event.target===event.currentTarget)onClose()}}>
    <div className="modal-content">
      <button className="close-button" onClick={onClose} aria-label="Cerrar"><X size={22}/></button>
      {children}
    </div>
  </dialog>
}

export default function App() {
  const [viewer,setViewer]=useState<Session>({connected:false,configured:false})
  const [films,setFilms]=useState<Film[]>([])
  const [loading,setLoading]=useState(true)
  const [working,setWorking]=useState(false)
  const [error,setError]=useState('')
  const [drivePermissionMissing,setDrivePermissionMissing]=useState(false)
  const [favoritesOnly,setFavoritesOnly]=useState(false)
  const [query,setQuery]=useState('')
  const [genre,setGenre]=useState('')
  const [sortOrder,setSortOrder]=useState<SortOrder>('title')
  const [selected,setSelected]=useState<Film|null>(null)
  const [dialog,setDialog]=useState<Dialog>(null)
  const [folderUrl,setFolderUrl]=useState('')
  const [editDraft,setEditDraft]=useState<FilmEdit>({})
  const [syncProgress,setSyncProgress]=useState<SyncProgress|null>(null)
  const [syncRetries,setSyncRetries]=useState(0)
  const [count,setCount]=useState(0)
  const [genres,setGenres]=useState<string[]>([])
  const [nextOffset,setNextOffset]=useState<number|null>(null)
  const [loadingMore,setLoadingMore]=useState(false)
  const [moreFailed,setMoreFailed]=useState(false)
  const [catalogRevision,setCatalogRevision]=useState(0)
  const sentinel=useRef<HTMLDivElement>(null)
  const requestVersion=useRef(0)
  const loadedLimit=useRef(16)
  const requestingMore=useRef(false)
  const readable=Boolean(viewer.public || (viewer.connected && viewer.library))
  const filters=useMemo(()=>({q:query,genre,favorite:favoritesOnly,sort:sortOrder}),[query,genre,favoritesOnly,sortOrder])
  const syncing=syncProgress?.status==='running'

  function showError(error:unknown) {
    setError(error instanceof Error ? error.message : 'No se pudo completar la operación')
    setDrivePermissionMissing(error instanceof backend.ApiError && error.code==='drive_scope_required')
  }

  async function loadSession() {
    try {
      const user=await backend.session()
      setViewer(user)
      setFolderUrl(current=>current || user.default_folder_url || '')
      if(!user.public && !(user.connected && user.library)) {
        setFilms([])
        setSyncProgress(null)
        setCount(0)
      }
      setCatalogRevision(previous=>previous+1)
    } catch(error) {showError(error)}
    finally {setLoading(false)}
  }

  useEffect(()=>{
    const url=new URL(window.location.href)
    const oauthError=url.searchParams.get('error')
    if(oauthError==='drive_scope_required')showError(new backend.ApiError('Autorizá el permiso para ver y descargar archivos de Drive.','drive_scope_required'))
    else if(oauthError==='oauth_denied')showError(new Error('No se completó la autorización de Google.'))
    if(oauthError){url.searchParams.delete('error');window.history.replaceState(null,'',url)}
    void loadSession()
  },[])

  function applyCatalog(data:CatalogPage) {
    setFilms(data.movies)
    setCount(data.count)
    setGenres(data.genres)
    setNextOffset(data.next_offset)
    setSyncProgress(data.sync)
  }

  useEffect(()=>{
    if(!readable)return
    const version=++requestVersion.current
    const controller=new AbortController()
    loadedLimit.current=16
    requestingMore.current=false
    setLoading(true);setLoadingMore(false);setMoreFailed(false);setNextOffset(null)
    let timer:number
    let retries=0
    async function refresh() {
      if(requestingMore.current){timer=window.setTimeout(refresh,1500);return}
      const limit=loadedLimit.current
      try {
        const data=await backend.catalog(filters,0,limit,controller.signal)
        if(controller.signal.aborted)return
        if(loadedLimit.current===limit)applyCatalog(data)
        retries=0;setSyncRetries(0);setLoading(false)
        if(data.sync?.status==='running')timer=window.setTimeout(refresh,1500)
      } catch(error) {
        if(controller.signal.aborted)return
        const temporary=error instanceof TypeError || (error instanceof backend.ApiError && [429,500,502,503,504,524].includes(error.status || 0))
        if(temporary && retries<8){retries+=1;setSyncRetries(retries);timer=window.setTimeout(refresh,Math.min(15000,1000*2**(retries-1)));return}
        showError(error);setLoading(false)
      }
    }
    timer=window.setTimeout(refresh,query?250:0)
    return ()=>{window.clearTimeout(timer);controller.abort();if(requestVersion.current===version)requestVersion.current+=1}
  },[readable,filters,catalogRevision])

  async function loadMore() {
    if(nextOffset===null || requestingMore.current)return
    const version=requestVersion.current
    requestingMore.current=true;setLoadingMore(true);setMoreFailed(false)
    try {
      const data=await backend.catalog(filters,nextOffset)
      if(requestVersion.current!==version)return
      setFilms(previous=>{
        const updated=new Map(previous.map(film=>[film.id,film]))
        for(const film of data.movies)updated.set(film.id,film)
        return [...updated.values()]
      })
      loadedLimit.current=Math.min(2000,nextOffset+16)
      setCount(data.count);setGenres(data.genres);setNextOffset(data.next_offset)
    } catch(error){if(requestVersion.current===version){setMoreFailed(true);showError(error)}}
    finally{if(requestVersion.current===version){requestingMore.current=false;setLoadingMore(false)}}
  }

  useEffect(()=>{
    const element=sentinel.current
    if(!element || loading || loadingMore || moreFailed || nextOffset===null)return
    const observer=new IntersectionObserver(entries=>{if(entries.some(entry=>entry.isIntersecting))void loadMore()},{rootMargin:'400px'})
    observer.observe(element)
    return ()=>observer.disconnect()
  },[nextOffset,loading,loadingMore,moreFailed,filters])

  const groups=useMemo(()=>{
    const grouped=new Map<string,Film[]>()
    for(const film of films){const key=filmKey(film);const versions=grouped.get(key) || [];versions.push(film);grouped.set(key,versions)}
    return [...grouped.values()]
  },[films])
  const versions=selected?groups.find(group=>group.some(film=>film.id===selected.id)) || [selected]:[]

  const filtered=groups

  const close=()=>{setSelected(null);setDialog(null)}
  const clearFilters=()=>{setQuery('');setGenre('')}

  async function startSync() {
    setWorking(true);setError('');setSyncRetries(0)
    try {setSyncProgress(await backend.sync(true));setCatalogRevision(previous=>previous+1)}
    catch(error){showError(error)}
    finally{setWorking(false)}
  }

  async function connectFolder(event:React.FormEvent) {
    event.preventDefault()
    if(!folderUrl.trim())return
    setWorking(true);setError('')
    try {
      await backend.setLibrary(folderUrl)
      const user=await backend.session()
      setViewer(user);setDialog(null)
      setSyncProgress(await backend.sync(true));setCatalogRevision(previous=>previous+1)
    } catch(error){showError(error)}
    finally{setWorking(false)}
  }

  async function toggleFavorite(film:Film) {
    try {
      await backend.edit(film.id,{favorite:!film.favorite})
      setFilms(previous=>previous.map(item=>item.id===film.id?{...item,favorite:!film.favorite}:item))
      setSelected(previous=>previous?.id===film.id?{...previous,favorite:!film.favorite}:previous)
      if(favoritesOnly)setCatalogRevision(previous=>previous+1)
    } catch(error){showError(error)}
  }

  async function saveEdit(event:React.FormEvent) {
    event.preventDefault()
    if(!selected)return
    setWorking(true);setError('')
    try {
      await backend.edit(selected.id,editDraft)
      const updated={...selected,...editDraft}
      setFilms(previous=>previous.map(film=>film.id===selected.id?updated:film))
      setSelected(updated);setDialog(null)
      setCatalogRevision(previous=>previous+1)
    } catch(error){showError(error)}
    finally{setWorking(false)}
  }

  async function disconnect() {
    setWorking(true)
    try {
      await backend.logout()
      await loadSession();close()
    } catch(error){showError(error)}
    finally{setWorking(false)}
  }

  async function changeSharing() {
    setWorking(true);setError('')
    try {await backend.sharing(!viewer.public);setViewer(await backend.session())}
    catch(error){showError(error)}
    finally{setWorking(false)}
  }

  return <div className="app-shell">
    <header className="site-header">
      <button className="brand" onClick={()=>{setFavoritesOnly(false);clearFilters()}}>Cinemateca</button>
      <nav aria-label="Catálogo">
        <button className={!favoritesOnly?'active':''} onClick={()=>setFavoritesOnly(false)}>Películas</button>
        <button className={favoritesOnly?'active':''} onClick={()=>setFavoritesOnly(true)}>Favoritos</button>
      </nav>
      <button className="drive-button" onClick={()=>setDialog('drive')}><Settings size={17}/>{viewer.can_manage || viewer.public?'Administrar':'Conectar Drive'}</button>
    </header>
    {error && <div className="error-banner" role="alert"><span>{error}</span>{drivePermissionMissing && <a href="/auth/login">Volver a autorizar Drive</a>}<button onClick={()=>setError('')} aria-label="Cerrar error"><X size={18}/></button></div>}
    <main>
      <div className="catalog-heading">
        <h1>{favoritesOnly?'Favoritos':'Películas'} <span>{count}</span></h1>
        {viewer.connected && viewer.library && <button className="secondary-button" onClick={()=>void startSync()} disabled={working || syncing}><RefreshCw size={16} className={syncing?'spin':''}/>{syncing?'Sincronizando':'Sincronizar'}</button>}
      </div>
      <div className="catalog-filters">
        <label className="search-field"><Search size={18}/><input type="search" placeholder="Buscar película, director, año…" aria-label="Buscar películas" value={query} onChange={event=>setQuery(event.target.value)}/></label>
        <label><span>Categoría</span><select aria-label="Filtrar por categoría" value={genre} onChange={event=>setGenre(event.target.value)}><option value="">Todas las categorías</option>{genres.map(name=><option key={name} value={name}>{name}</option>)}</select></label>
        <label><span>Ordenar por</span><select aria-label="Ordenar películas" value={sortOrder} onChange={event=>setSortOrder(event.target.value as SortOrder)}><option value="title">Título (A–Z)</option><option value="year">Año (más recientes)</option><option value="popularity">Popularidad TMDB</option></select></label>
        {(query || genre) && <button className="text-button" onClick={clearFilters}>Limpiar filtros</button>}
      </div>
      {syncProgress && <div className="sync-progress" role="status" aria-live="polite">
        {syncing && <Loader2 className="spin" size={16}/>}
        {syncing && syncRetries>0 && <span>Reintentando conexión…</span>}
        <span>{syncProgress.phase==='scan'?`Revisando Drive: ${syncProgress.processed} elementos · ${syncProgress.count} películas`:syncProgress.phase==='metadata'?`Cargando pósters, categorías y popularidad: ${syncProgress.enriched} de ${syncProgress.count}`:`Sincronización completa: ${syncProgress.count} películas`}{syncProgress.limited?' · Se alcanzó el límite de exploración.':''}</span>
        {syncProgress.phase==='metadata' && <progress value={syncProgress.enriched} max={syncProgress.count} aria-label="Avance de metadatos"/>}
      </div>}
      {loading ? <div className="empty-state" role="status"><Loader2 className="spin" size={24}/><p>Cargando películas…</p></div> :
        filtered.length ? <div className="movie-grid">{filtered.map(group=>{const film=group[0];return <article className="movie-card" key={filmKey(film)}>
          <button className="poster-button" onClick={()=>setSelected(film)} aria-label={`Ver ficha de ${film.title}`}><Poster film={film}/>{group.length>1 && <span className="version-count">{group.length} versiones</span>}</button>
          <div className="movie-meta"><div><button className="movie-title" onClick={()=>setSelected(film)}>{film.title}</button><p>{[film.year,film.country].filter(Boolean).join(' · ')}</p></div>{viewer.can_manage && <button className={film.favorite?'favorite-button saved':'favorite-button'} onClick={()=>void toggleFavorite(film)} aria-label={`${film.favorite?'Quitar':'Agregar'} ${film.title} ${film.favorite?'de':'a'} favoritos`} aria-pressed={film.favorite}><Heart size={18} fill={film.favorite?'currentColor':'none'}/></button>}</div>
        </article>})}</div> : <div className="empty-state"><p>{syncing?'Buscando películas…':favoritesOnly?'No hay favoritos.':query || genre?'No se encontraron películas.':viewer.public?'No hay películas cargadas.':!viewer.connected?'Conectá Google Drive para cargar tus películas.':'No hay películas cargadas.'}</p>{!viewer.public && (!viewer.connected || !viewer.library)?<button className="primary-button" onClick={()=>setDialog('drive')}>{viewer.connected?'Elegir carpeta':'Conectar Drive'}</button>:viewer.can_manage && !syncing && !count && !query && !genre && !favoritesOnly && <button className="primary-button" onClick={()=>void startSync()} disabled={working}>Sincronizar</button>}</div>}
      <div ref={sentinel} className="catalog-more">
        {loadingMore && <span role="status"><Loader2 size={18} className="spin"/>Cargando más películas…</span>}
        {!loading && !loadingMore && nextOffset!==null && <button className="secondary-button" onClick={()=>void loadMore()}>{moreFailed?'Reintentar':'Cargar 16 más'}</button>}
      </div>
    </main>
    {selected && !dialog && <Modal label={selected.title} onClose={close} wide>
      <div className="film-details"><Poster film={selected}/><div className="film-description">
        <h2>{selected.title}</h2><p className="film-facts">{[selected.year,selected.country,selected.duration?`${selected.duration} min`:''].filter(Boolean).join(' · ')}</p>
        {selected.director && <p><strong>Dirección:</strong> {selected.director}</p>}{!!selected.cast?.length && <p><strong>Reparto:</strong> {selected.cast.join(', ')}</p>}
        {selected.genre && <p>{selected.genre}</p>}{selected.popularity != null && <p className="muted">Popularidad TMDB: {selected.popularity.toLocaleString('es-AR',{maximumFractionDigits:1})}</p>}
        {selected.overview && <p className="synopsis">{selected.overview}</p>}
        {versions.length>1 && <label className="form-field version-select">Versión<select aria-label="Elegir versión" value={selected.id} onChange={event=>setSelected(versions.find(film=>film.id===event.target.value) || selected)}>{versions.map(film=><option key={film.id} value={film.id}>{versionLabel(film)}</option>)}</select></label>}
        <div className="detail-actions"><button className="primary-button" disabled={!selected.canDownload} onClick={()=>setDialog('player')}><CirclePlay size={18}/>Reproducir</button>{selected.canDownload && <a className="secondary-button" href={`/api/stream/${encodeURIComponent(selected.id)}?download=true`} download={selected.filename || true}><Download size={18}/>Descargar</a>}{viewer.can_manage && <><button className="secondary-button" onClick={()=>void toggleFavorite(selected)}><Heart size={18} fill={selected.favorite?'currentColor':'none'}/>Favorito</button><button className="secondary-button" onClick={()=>{setEditDraft({title:selected.title,year:selected.year,director:selected.director,genre:selected.genre,overview:selected.overview,poster:selected.poster,backdrop:selected.backdrop});setDialog('edit')}}>Editar</button></>}</div>
        <p className="file-note"><a href={`https://drive.google.com/file/d/${encodeURIComponent(selected.id)}/view`} target="_blank" rel="noopener noreferrer">{selected.filename || 'Ver archivo en Drive'}</a></p>{!selected.canDownload && <p className="file-note">El propietario deshabilitó la descarga.</p>}
      </div></div>
    </Modal>}
    {dialog==='player' && selected && <Modal label={`Reproduciendo ${selected.title}`} onClose={close} wide><h2>{selected.title}</h2><Player film={selected}/></Modal>}
    {dialog==='drive' && <Modal label="Google Drive" onClose={close}><h2>Google Drive</h2>{viewer.connected?<>
      <p className="muted">{viewer.email}</p><form onSubmit={event=>void connectFolder(event)}><label className="form-field">Carpeta compartida<input required value={folderUrl} onChange={event=>setFolderUrl(event.target.value)} placeholder="Enlace o ID de la carpeta"/></label><button className="primary-button" disabled={working || syncing}>{working?<Loader2 size={16} className="spin"/>:null}Usar carpeta y sincronizar</button></form>
      {viewer.library && <div className="sharing-settings"><p>{viewer.public?'Acceso abierto: cualquiera puede ver y descargar. Solo vos podés editar y sincronizar.':'Acceso privado: los visitantes no pueden ver la biblioteca.'}</p><button className="secondary-button" onClick={()=>void changeSharing()} disabled={working}>{viewer.public?'Desactivar acceso abierto':'Activar acceso abierto (solo lectura)'}</button></div>}
      <button className="text-button disconnect" onClick={()=>void disconnect()} disabled={working}>Desconectar cuenta</button>
    </>:<><p>{viewer.public?'El catálogo es público. Solo el propietario necesita iniciar sesión para administrarlo.':'Autorizá el acceso a Drive para cargar la carpeta compartida.'}</p>{viewer.configured?<a className="primary-button" href="/auth/login">{viewer.public?'Ingresar como propietario':'Conectar Google'}</a>:<p role="alert">Google OAuth no está configurado.</p>}</>}</Modal>}
    {dialog==='edit' && selected && <Modal label="Editar película" onClose={close}><h2>Editar película</h2><form onSubmit={event=>void saveEdit(event)}><div className="edit-fields">{([['title','Título'],['year','Año'],['director','Director'],['genre','Géneros (separados por comas)'],['poster','URL del póster'],['backdrop','URL de imagen']] as const).map(([field,label])=><label className="form-field" key={field}>{label}<input value={editDraft[field] ?? ''} required={field==='title'} maxLength={field==='year'?4:field==='poster'||field==='backdrop'?1000:field==='genre'?100:200} onChange={event=>setEditDraft(previous=>({...previous,[field]:event.target.value}))}/></label>)}<label className="form-field">Sinopsis<textarea rows={5} maxLength={2500} value={editDraft.overview || ''} onChange={event=>setEditDraft(previous=>({...previous,overview:event.target.value}))}/></label></div><button className="primary-button" disabled={working}>Guardar</button></form></Modal>}
  </div>
}
