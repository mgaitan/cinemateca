import type { Session, Film, FilmEdit, Subtitle, SyncResult, CatalogPage, CatalogQuery } from './types'
export class ApiError extends Error {
  constructor(message:string, public code?:string, public status?:number) { super(message) }
}
async function api<T>(path:string, options?:RequestInit):Promise<T> {
  const response = await fetch(path,{credentials:'same-origin',...options,headers:{'Content-Type':'application/json',...options?.headers}})
  if (!response.ok) {
    let message = `Error HTTP ${response.status}`
    let code:string|undefined
    try {
      const data = await response.json()
      if (typeof data.detail === 'string') message = data.detail
      else if (data.detail && typeof data.detail.message === 'string') { message = data.detail.message; code = data.detail.code }
    } catch { /* non-JSON error */ }
    throw new ApiError(message,code,response.status)
  }
  return response.json()
}
export const session=()=>api<Session>('/api/me')
export const movie=(id:string,signal?:AbortSignal)=>api<{movie:Film;versions:Film[]}>(`/api/movies/${encodeURIComponent(id)}`,{signal})
export const catalog=(filters:CatalogQuery,offset=0,limit=16,signal?:AbortSignal)=>api<CatalogPage>(`/api/movies?${new URLSearchParams({...filters,favorite:String(filters.favorite),offset:String(offset),limit:String(limit)})}`,{signal})
export const setLibrary=(url:string)=>api<{folder_id:string,folder_name:string}>('/api/library',{method:'POST',body:JSON.stringify({url})})
export const sync=(start=false,signal?:AbortSignal)=>api<SyncResult>(`/api/sync?start=${start}`,{method:'POST',signal})
export const edit=(id:string,changes:FilmEdit)=>api<{ok:boolean}>(`/api/movies/${encodeURIComponent(id)}`,{method:'PATCH',body:JSON.stringify(changes)})
export const logout=()=>api<{ok:boolean}>('/auth/logout',{method:'POST'})
export const sharing=(enabled:boolean)=>api<{public:boolean}>('/api/sharing',{method:'POST',body:JSON.stringify({enabled})})
export const subtitles=(id:string,signal?:AbortSignal)=>api<{tracks:Subtitle[]}>(`/api/movies/${encodeURIComponent(id)}/subtitles`,{signal})
export const castTicket=(id:string)=>api<{token:string;expires_at:number}>(`/api/movies/${encodeURIComponent(id)}/cast`,{method:'POST'})
