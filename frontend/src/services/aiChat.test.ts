import { beforeEach, describe, expect, it } from 'vitest'
import {
  CONVERSATIONS_MAX, HISTORY_MAX, currentConversationId, forApi, forStorage, loadHistory, loadModel, newConversationId, overflow, pickStarterGroups, pickStarters,
  saveHistory, saveModel, setCurrentConversationId, titleOf, trimHistory,
} from './aiChat'
import type { Conversation } from '../types/ai'
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


describe('pickStarterGroups — the page wins over the app', () => {
  it("shows a screen's flat starters even when the app has themed groups", () => {
    const groups = { nomajde: [{ label: 'Users', starters: ['a', 'b'] }] }
    const starters = { 'nomajde/explorer': ['Create an analysis'], nomajde: ['x'] }
    expect(pickStarterGroups(groups, starters, 'nomajde', 'explorer')).toEqual([{ label: null, starters: ['Create an analysis'] }])
    expect(pickStarterGroups(groups, starters, 'nomajde', 'f0092')).toEqual([{ label: 'Users', starters: ['a', 'b'] }])
    expect(pickStarterGroups(groups, starters, 'other', null)).toEqual([])
  })
})

describe('conversations', () => {
  it('titles a conversation by its first question, on one line', () => {
    expect(titleOf([{ role: 'user', content: '  Which business unit\n sold the most?  ' }, { role: 'assistant', content: 'France.' }])).toBe('Which business unit sold the most?')
    expect(titleOf([{ role: 'user', content: 'x'.repeat(200) }]).length).toBe(60)
    expect(titleOf([])).toBe('')
  })
  it('keeps the conversation of this device, a new one when there is none', () => {
    localStorage.clear()
    const id = currentConversationId()
    expect(id).toMatch(/^c-/)
    expect(currentConversationId()).toBe(id)
    const other = newConversationId()
    expect(other).not.toBe(id)
    setCurrentConversationId(other)
    expect(currentConversationId()).toBe(other)
  })
  it('drops the oldest beyond the cap', () => {
    const all: Conversation[] = Array.from({ length: CONVERSATIONS_MAX + 2 }, (_, i) => ({
      id: `c${i}`, title: `t${i}`, app: null, messages: [], updated_at: `2026-11-${String(i + 1).padStart(2, '0')}T10:00:00`,
    }))
    expect(overflow(all).map((c) => c.id).sort()).toEqual(['c0', 'c1'])
    expect(overflow(all.slice(0, 5))).toEqual([])
  })
})

describe('attached files', () => {
  const block = { type: 'image', source: { type: 'base64', media_type: 'image/png', data: 'AAAA' } }
  const turn = [
    { role: 'user' as const, content: 'What is this error?', attachments: ['error.png'], blocks: [block] },
    { role: 'assistant' as const, content: 'ORA-00911.' },
  ]
  it('sends the files as parts before the question', () => {
    expect(forApi(turn)).toEqual([
      { role: 'user', content: [block, { type: 'text', text: 'What is this error?' }] },
      { role: 'assistant', content: 'ORA-00911.' },
    ])
  })
  it('keeps the names, never the files', () => {
    const kept = forStorage(turn)
    expect(kept).toEqual([{ role: 'user', content: 'What is this error?', attachments: ['error.png'] }, { role: 'assistant', content: 'ORA-00911.' }])
    expect(JSON.stringify(kept)).not.toContain('AAAA')
    localStorage.clear()
    saveHistory(turn)
    expect(localStorage.getItem('liberty.ai.history')).not.toContain('AAAA')
  })
  it('says so when a resumed question no longer has its file', () => {
    expect(forApi(forStorage(turn))[0].content).toBe('[attached then, no longer available: error.png]\nWhat is this error?')
  })
})
