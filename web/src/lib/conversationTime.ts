import type { Session } from '../api/types'

export function sortSessionsByStart(sessions: Session[]): Session[] {
  const timestamp = (value: string) => Number.isFinite(Date.parse(value)) ? Date.parse(value) : Number.NEGATIVE_INFINITY
  return [...sessions].sort((a, b) => timestamp(b.started_at) - timestamp(a.started_at) || b.session_id.localeCompare(a.session_id))
}

/** Calendar days in the viewer's timezone, including DST changes. */
export function groupSessionsByTime(sessions: Session[], now = new Date()) {
  const day = (offset: number) => new Date(now.getFullYear(), now.getMonth(), now.getDate() + offset).getTime()
  const today = day(0), tomorrow = day(1), yesterday = day(-1), week = day(-6)
  const groups = new Map<string, { key: string; label: string; sessions: Session[] }>()
  for (const session of sortSessionsByStart(sessions)) {
    const time = Date.parse(session.started_at)
    const date = new Date(time)
    const key = !Number.isFinite(time) ? 'unknown' : time >= tomorrow ? 'future' : time >= today ? 'today'
      : time >= yesterday ? 'yesterday' : time >= week ? 'week' : `${date.getFullYear()}-${date.getMonth() + 1}`
    const label = key === 'unknown' ? '时间未记录' : key === 'future' ? '未来日期' : key === 'today' ? '今天'
      : key === 'yesterday' ? '昨天' : key === 'week' ? '近 7 天' : `${date.getFullYear()}年${date.getMonth() + 1}月`
    if (!groups.has(key)) groups.set(key, { key, label, sessions: [] })
    groups.get(key)!.sessions.push(session)
  }
  return [...groups.values()]
}
