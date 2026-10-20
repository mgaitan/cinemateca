import { useEffect, useState } from 'react'
import type { Film } from './types'

export default function Poster({film}:{film:Film}) {
  const [failed,setFailed]=useState(false)
  const source=film.poster || film.backdrop
  useEffect(()=>setFailed(false),[source])
  return <div className="poster">
    {source && !failed ? <img src={source} alt="" loading="lazy" onError={()=>setFailed(true)}/> : <span>{film.title}</span>}
  </div>
}
