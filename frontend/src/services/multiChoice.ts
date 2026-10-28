// The value of a "multiple" job parameter (several applications…): a list of picked values, or
// 'all' — every choice, including those added later (the backend expands it at run time). An
// empty list means "not set" (the job's own default). No React here.
export type MultiValue = string[] | 'all'

/** Read whatever the form holds (a list, 'all', "1,3", a single number) as a MultiValue. */
export function toMulti(v: unknown): MultiValue {
  if (v === 'all') return 'all'
  if (Array.isArray(v)) return v.includes('all') ? 'all' : v.map(String)
  if (v == null || v === '') return []
  return String(v).split(',').map((s) => s.trim()).filter(Boolean)
}

export function isPicked(value: MultiValue, choice: string): boolean {
  return value === 'all' || value.includes(choice)
}

/** Tick / untick one choice. Unticking one while "All" is on keeps every other choice. */
export function toggleChoice(value: MultiValue, choice: string, all: string[]): MultiValue {
  const picked = value === 'all' ? [...all] : [...value]
  const next = picked.includes(choice) ? picked.filter((c) => c !== choice) : [...picked, choice]
  return all.filter((c) => next.includes(c)).concat(next.filter((c) => !all.includes(c)))
}
