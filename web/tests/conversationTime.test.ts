import { expect, it } from 'vitest'
import type { Session } from '../src/api/types'
import { groupSessionsByTime, sortSessionsByStart } from '../src/lib/conversationTime'
const row = (id: string, date: Date | string) => ({ session_id:id, started_at:typeof date === 'string' ? date : date.toISOString() } as Session)
it('uses local calendar boundaries and distinct month buckets, with future/invalid times explicit', () => {
 const now = new Date(2026,9,22,12)
 const rows=[row('today',new Date(2026,9,22,0,1)),row('yesterday',new Date(2026,9,21,23,59)),row('week',new Date(2026,9,16,0)),row('month',new Date(2026,9,15,23,59)),row('last-month',new Date(2026,8,30,23,59)),row('future',new Date(2026,9,23,0)),row('invalid','bad-date')]
 const groups=groupSessionsByTime(rows.reverse(),now)
 expect(groups.map(group=>group.label)).toEqual(['未来日期','今天','昨天','近 7 天','2026年10月','2026年9月','时间未记录'])
 expect(groups.map(group=>group.sessions[0]?.session_id)).toEqual(['future','today','yesterday','week','month','last-month','invalid'])
})
it('sorts newest first without mutating the registry, and keeps equal/invalid times stable', () => {
 const rows=[row('a','bad-date'),row('b','bad-date'),row('c','2026-10-22T00:00:00Z'),row('d','2026-10-22T00:00:00Z'),row('e','2026-10-23T00:00:00Z')]
 expect(sortSessionsByStart(rows).map(session=>session.session_id)).toEqual(['e','d','c','b','a'])
 expect(rows.map(session=>session.session_id)).toEqual(['a','b','c','d','e'])
})
it('handles year changes using calendar arithmetic instead of month or hour subtraction', () => {
 const now=new Date(2027,0,1,12)
 const groups=groupSessionsByTime([row('yesterday',new Date(2026,11,31,23)),row('week',new Date(2026,11,26,0)),row('older',new Date(2026,11,25,23))],now)
 expect(groups.map(group=>group.label)).toEqual(['昨天','近 7 天','2026年12月'])
})
