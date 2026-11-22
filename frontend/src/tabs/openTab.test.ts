import { describe, expect, it } from 'vitest'
import { withOpened } from './openTab'

const tab = (id: string) => ({ id })
const ids = (tabs: { id: string }[]) => tabs.map((t) => t.id)
const none = () => false

describe('the open tabs when a page is opened', () => {
  it('adds the page; the same page is not opened twice', () => {
    const tabs = [tab('a'), tab('b')]
    expect(ids(withOpened(tabs, tab('c'), { multi: true, max: 5 }, none))).toEqual(['a', 'b', 'c'])
    expect(withOpened(tabs, tab('a'), { multi: true, max: 5 }, none)).toBe(tabs)
  })

  it('the cap closes the oldest — never a page that holds unsaved edits', () => {
    const tabs = [tab('a'), tab('b'), tab('c')]
    expect(ids(withOpened(tabs, tab('d'), { multi: true, max: 3 }, none))).toEqual(['b', 'c', 'd'])
    expect(ids(withOpened(tabs, tab('d'), { multi: true, max: 3 }, (id) => id === 'a'))).toEqual(['a', 'c', 'd'])
    // every tab holds edits: the cap gives way, nothing is lost
    expect(ids(withOpened(tabs, tab('d'), { multi: true, max: 3 }, () => true))).toEqual(['a', 'b', 'c', 'd'])
    expect(ids(withOpened(tabs, tab('d'), { multi: true, max: 0 }, none))).toEqual(['d'])       // a cap under 1 is 1
  })

  it('single-tab mode replaces the page shown — but keeps one that holds unsaved edits', () => {
    const tabs = [tab('theme')]
    expect(ids(withOpened(tabs, tab('pools'), { multi: false, max: 5 }, none))).toEqual(['pools'])
    expect(ids(withOpened(tabs, tab('pools'), { multi: false, max: 5 }, (id) => id === 'theme'))).toEqual(['theme', 'pools'])
    // once saved, the next page opened replaces both
    expect(ids(withOpened([tab('theme'), tab('pools')], tab('menus'), { multi: false, max: 5 }, none))).toEqual(['menus'])
  })
})
