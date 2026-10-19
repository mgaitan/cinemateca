export interface Film {
  id: string
  title: string
  year: string
  filename?: string
  mime?: string
  playable: boolean
  canDownload: boolean
  duration: number
  durationSeconds?: number
  size?: number
  genre: string
  category?: string
  popularity?: number | null
  director: string
  country?: string
  tmdbId?: number | null
  cast?: string[]
  overview: string
  poster: string
  backdrop: string
  collection: string
  favorite: boolean
  featured: boolean
  addedAt?: string
}
export interface Library { user_id: string; folder_id: string; folder_name: string; synced_at: number }
export interface Session { connected: boolean; configured: boolean; public?: boolean; can_manage?: boolean; default_folder_url?: string; name?: string; email?: string; library?: Library | null }
export interface Subtitle { id: string; label: string; src: string }
export interface SyncProgress { status: 'running' | 'completed'; phase: 'scan' | 'metadata' | 'completed'; count: number; processed: number; enriched: number; limited: boolean; folder: string }
export interface SyncResult extends SyncProgress { movies: Film[] }
export type FilmEdit = Partial<Pick<Film, 'title' | 'year' | 'director' | 'overview' | 'genre' | 'poster' | 'backdrop' | 'collection' | 'favorite' | 'featured'>>
