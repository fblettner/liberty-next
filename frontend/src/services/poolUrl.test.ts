import { describe, expect, it } from 'vitest'
import { DIALECTS, composeUrl, optionOf, plainPassword, poolFigures, poolSummary, urlParts, withOption } from './poolUrl'

describe("a pool's URL as parts", () => {
  it('reads the usual URLs and puts them back unchanged', () => {
    const urls = [
      'postgresql+asyncpg://nomasx1@db.example.com:5432/nomasx1',
      'postgresql+asyncpg://user:p%40ss@host:5432/dbname',
      'oracle+oracledb://jde@ora.example.com:1521/?service_name=JDEPD',
      'oracle+oracledb://jde@ora.example.com:1521/ORCL',
      'mssql+aioodbc://sa@host:1433/db?driver=ODBC+Driver+18+for+SQL+Server&TrustServerCertificate=yes',
      'sqlite+aiosqlite:///./database.db',
      'sqlite+aiosqlite:////var/lib/liberty/app.db',
      'postgresql+asyncpg://${PGUSER}@${PGHOST:-localhost}:${PGPORT:-5432}/${PGDATABASE}',
      'postgresql://host/db',
      'postgresql+asyncpg://user@[::1]:5432/db',
      ...Object.values(DIALECTS).map((d) => d.template),
    ]
    for (const url of urls) {
      const parts = urlParts(url)
      expect(parts, url).not.toBeNull()
      expect(composeUrl(parts!), url).toBe(url)
    }
  })
  it('gives each part', () => {
    expect(urlParts('oracle+oracledb://jde@ora.example.com:1521/?service_name=JDEPD')).toEqual({
      scheme: 'oracle+oracledb', user: 'jde', password: null, host: 'ora.example.com', port: '1521', database: '', options: 'service_name=JDEPD' })
    expect(urlParts('postgresql+asyncpg://${PGUSER}@${PGHOST:-localhost}:5432/app')).toMatchObject({ user: '${PGUSER}', host: '${PGHOST:-localhost}', port: '5432', database: 'app' })
    expect(urlParts('postgresql+asyncpg://u:secret@h/db')?.password).toBe('secret')
    expect(urlParts('sqlite+aiosqlite:///./database.db')).toMatchObject({ host: '', database: './database.db' })
  })
  it('refuses what it cannot put back the same', () => {
    expect(urlParts('')).toBeNull()
    expect(urlParts('not a url')).toBeNull()
    expect(urlParts('postgresql+asyncpg://u@h:5432/db#fragment?x')).not.toBeNull()   // kept whole in the database part
    expect(urlParts('postgresql+asyncpg://a@b@host/db')).toBeNull()
  })
  it('changes one part and leaves the rest', () => {
    const p = urlParts('oracle+oracledb://jde@ora:1521/?service_name=JDEPD&expire_time=2')!
    expect(composeUrl({ ...p, host: 'ora2', options: withOption(p.options, 'service_name', 'JDEPY') })).toBe('oracle+oracledb://jde@ora2:1521/?service_name=JDEPY&expire_time=2')
    expect(withOption('a=1&service_name=X', 'service_name', '')).toBe('a=1')
    expect(withOption(null, 'service_name', 'X')).toBe('service_name=X')
    expect(withOption('service_name=X', 'service_name', '')).toBeNull()
    expect(optionOf('a=1&service_name=X=Y', 'service_name')).toBe('X=Y')
    expect(optionOf(null, 'service_name')).toBe('')
  })
  it("says what a pool connects to, never the account's secret", () => {
    expect(poolSummary('postgresql+asyncpg://u:secret@db.example.com:5432/app')).toEqual({ dialect: 'postgresql', where: 'db.example.com:5432 / app' })
    expect(poolSummary('oracle+oracledb://jde@ora:1521/?service_name=JDEPD')).toEqual({ dialect: 'oracle', where: 'ora:1521 / JDEPD' })
    expect(poolSummary('sqlite+aiosqlite:///./database.db')).toEqual({ dialect: 'sqlite', where: './database.db' })
    expect(poolSummary('')).toEqual({ dialect: '', where: '' })
  })
  it('undoes the escapes of a password moved out of the URL', () => {
    expect(plainPassword('p%40ss%2Fword')).toBe('p@ss/word')
    expect(plainPassword('100%')).toBe('100%')
  })
})

describe("what a pool's stages say", () => {
  it('reads the settings', () => {
    expect(poolFigures({ url: 'oracle+oracledb://jde@ora:1521/?service_name=JDEPD', password: 'ENC:xyz', schemas: { CO: 'PRODCTL', DTA: 'PRODDTA' }, pool_size: 8, trim_strings: true })).toEqual({
      dialect: 'oracle', where: 'ora:1521 / JDEPD', scheme: 'oracle+oracledb', understood: true, user: 'jde', passwordSet: true, passwordInUrl: false,
      schemas: ['CO', 'DTA'], dblinks: [], size: 8, overflow: 10, flags: ['trim_strings'] })
    expect(poolFigures({ url: 'postgresql+asyncpg://u:secret@h/db' })).toMatchObject({ passwordSet: false, passwordInUrl: true, size: 5 })
    expect(poolFigures({ url: '', dialect: 'oracle' })).toMatchObject({ dialect: 'oracle', where: '', understood: false })
  })
})
