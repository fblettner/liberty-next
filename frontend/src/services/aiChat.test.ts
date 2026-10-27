import { beforeEach, describe, expect, it } from 'vitest'
import { HISTORY_MAX, loadHistory, loadModel, pickStarterGroups, pickStarters, saveHistory, saveModel, trimHistory } from './aiChat'
import type { ChatMessage } from '../types/ai'

const starters = { '*': ['What can you do?'], nomajde: ['Who has no role?'], 'nomajde/f0092': ['Users never logged in'] }

describe('aiChat', () => {
  beforeEach(() => { localStorage.clear() })

  it('picks the most specific starters', () => {
    expect(pickStarters(starters, 'nomajde', 'f0092')).toEqual(['Users never logged in'])
    expect(pickStarters(starters, 'nomajde', 'f0093')).toEqual(['Who has no role?'])
    expect(pickStarters(starters, 'nomasx1', null)).toEqual(['What can you do?'])
    expect(pickStarters({ nomajde: [] }, 'nomajde', null)).toEqual([])
    expect(pickStarters(undefined, 'nomajde', null)).toEqual([])
  })

  it('trims to the last messages and starts on a user turn', () => {
    const msgs: ChatMessage[] = Array.from({ length: HISTORY_MAX + 3 }, (_, i) => ({ role: i % 2 === 0 ? 'user' : 'assistant', content: `m${i}` }))
    const kept = trimHistory(msgs)
    expect(kept.length).toBe(HISTORY_MAX - 1)      // the cut landed on an assistant turn → dropped
    expect(kept[0]).toEqual({ role: 'user', content: 'm4' })
    expect(kept[kept.length - 1].content).toBe(`m${HISTORY_MAX + 2}`)
  })

  it('round-trips the history and the model through localStorage, ignoring junk', () => {
    saveHistory([{ role: 'user', content: 'hi' }, { role: 'assistant', content: 'hello' }])
    expect(loadHistory()).toEqual([{ role: 'user', content: 'hi' }, { role: 'assistant', content: 'hello' }])
    saveHistory([])
    expect(loadHistory()).toEqual([])
    localStorage.setItem('liberty.ai.history', JSON.stringify([{ role: 'system', content: 'x' }, { role: 'user' }, 'junk', { role: 'user', content: 'ok' }]))
    expect(loadHistory()).toEqual([{ role: 'user', content: 'ok' }])
    localStorage.setItem('liberty.ai.history', '{not json')
    expect(loadHistory()).toEqual([])
    expect(loadModel()).toBeNull()
    saveModel('claude-haiku-5-5')
    expect(loadModel()).toBe('claude-haiku-5-5')
    saveModel(null)
    expect(loadModel()).toBeNull()
  })
})

describe('pickStarterGroups', () => {
  it('prefers the most specific key with groups, else the flat starters as one group', () => {
    const groups = { nomajde: [{ label: 'Users', starters: ['a'] }, { label: 'Empty', starters: [] }], 'nomajde/f0092': [{ label: 'This screen', starters: ['b'] }] }
    expect(pickStarterGroups(groups, starters, 'nomajde', 'f0092')).toEqual([{ label: 'This screen', starters: ['b'] }])
    expect(pickStarterGroups(groups, starters, 'nomajde', 'f0093')).toEqual([{ label: 'Users', starters: ['a'] }])
    expect(pickStarterGroups(groups, starters, 'nomasx1', null)).toEqual([{ label: null, starters: ['What can you do?'] }])
    expect(pickStarterGroups(undefined, undefined, 'x', null)).toEqual([])
  })
})
