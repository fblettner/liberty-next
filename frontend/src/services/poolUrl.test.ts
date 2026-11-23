import { describe, expect, it } from 'vitest'
import { DIALECTS, DRIVERS, NOT_A_DATABASE, accountInUrl, accountShown, composeUrl, driversFor, isDirectory, isServer, kindOf, optionOf, poolsOfKind, plainPassword, poolFigures, poolSummary, targetField, urlParts, withDialect, withOption } from './poolUrl'

describe("a pool's URL as parts", () => {
  it('reads the usual URLs and puts them back unchanged', () => {
    const urls = [
      'postgresql+asyncpg://nomasx1@db.example.com:5432/nomasx1',
      'postgresql+asyncpg://user:p%40ss@host:5432/dbname',
      'oracle+oracledb://jde@ora.example.com:1521/?service_name=JDEPD',
      'oracle+oracledb://jde@ora.example.com:1521/ORCL',
      'mssql+aioodbc://sa@host:1433/db?driver=ODBC+Driver+18+for+SQL+Server&TrustServerCertificate=yes',
      'mssql+pymssql://jde@sql.example.com:1433/JDE920', 'db2+ibm_db://jde@db2.example.com:50000/JDE', 'ibmi://jde@as400.example.com/S1234567',
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

describe("a pool's driver", () => {
  it('lists the drivers of the kind of database, and the one in place when it is another', () => {
    expect(driversFor('postgresql+asyncpg')).toEqual(['postgresql+asyncpg', 'postgresql+psycopg'])
    expect(driversFor('postgresql+pg8000')).toEqual(['postgresql+pg8000', 'postgresql+asyncpg', 'postgresql+psycopg'])
    expect(driversFor('oracle+oracledb')).toEqual(['oracle+oracledb'])
    expect(driversFor('firebird+fdb')).toEqual(['firebird+fdb'])                       // a kind the page does not list: the driver it has
    expect(driversFor('mssql+aioodbc')).toEqual(['mssql+pymssql', 'mssql+aioodbc'])     // SQL Server: the driver that needs nothing first
    expect(driversFor('ibmi')).toEqual(['ibmi'])
    for (const [kind, d] of Object.entries(DIALECTS)) expect(DRIVERS[kind][0], kind).toBe(d.scheme)
  })
  it('follows the kind of database: the driver, and the port when it was the usual one', () => {
    expect(withDialect('postgresql+asyncpg://app@db.example.com:5432/app', 'mysql')).toBe('mysql+aiomysql://app@db.example.com:3306/app')
    expect(withDialect('postgresql+asyncpg://app@db.example.com:6543/app', 'mysql')).toBe('mysql+aiomysql://app@db.example.com:6543/app')   // a port of its own stays
    // Oracle is reached by a service name, the others by a database: the name is carried over
    expect(withDialect('postgresql+asyncpg://app@db.example.com/app', 'oracle')).toBe('oracle+oracledb://app@db.example.com:1521/?service_name=app')
    expect(withDialect('oracle+oracledb://jde@ora:1521/?service_name=JDEPD&expire_time=2', 'postgresql')).toBe('postgresql+asyncpg://jde@ora:5432/JDEPD?expire_time=2')
    expect(withDialect('oracle+oracledb://jde@ora:1521/ORCL', 'postgresql')).toBe('postgresql+asyncpg://jde@ora:5432/ORCL')
    // a file has no host: to or from SQLite the URL starts again from the template
    expect(withDialect('postgresql+asyncpg://app@h:5432/app', 'sqlite')).toBe('sqlite+aiosqlite:///./database.db')
    expect(withDialect('sqlite+aiosqlite:///./database.db', 'postgresql')).toBe('postgresql+asyncpg://user@host:5432/dbname')
  })
  it('leaves the URL alone when there is nothing to follow', () => {
    expect(withDialect('postgresql+psycopg://app@h:5432/app', 'postgresql')).toBe('postgresql+psycopg://app@h:5432/app')   // already of that kind: its driver is kept
    expect(withDialect('postgresql+asyncpg://app@h:5432/app', 'firebird')).toBe('postgresql+asyncpg://app@h:5432/app')
    // to Db2 and to an IBM i: the driver and the usual port follow (an IBM i has no port: the driver knows it)
    expect(withDialect('oracle+oracledb://jde@h:1521/?service_name=JDE', 'db2')).toBe('db2+ibm_db://jde@h:50000/JDE')
    expect(withDialect('oracle+oracledb://jde@h:1521/?service_name=JDE', 'ibmi')).toBe('ibmi://jde@h/JDE')
    expect(withDialect('postgresql+asyncpg://jde@h:5432/JDE920', 'mssql')).toBe('mssql+pymssql://jde@h:1433/JDE920')
    expect(withDialect('not a url', 'oracle')).toBe('not a url')
    expect(withDialect('', 'oracle')).toBe('')
  })
})

describe('the one field that names the database', () => {
  it('follows the kind of database', () => {
    expect(targetField(urlParts('postgresql+asyncpg://u@h:5432/app')!)).toBe('database')
    expect(targetField(urlParts('oracle+oracledb://u@h:1521/?service_name=X')!)).toBe('service')
    expect(targetField(urlParts('oracle+oracledb://u@h:1521/')!)).toBe('service')     // a new Oracle pool: a service name
    expect(targetField(urlParts('oracle+oracledb://u@h:1521/ORCL')!)).toBe('sid')     // written with a SID: kept as one
    expect(targetField(urlParts('sqlite+aiosqlite:///./database.db')!)).toBe('file')
  })
})

describe('an LDAP directory is a kind of pool', () => {
  it('is read like a database URL: host, port, account, base', () => {
    const p = urlParts('ldaps://cn=liberty,ou=services,dc=example,dc=com@ad.example.com:636/dc=example,dc=com')
    expect(p).toMatchObject({ scheme: 'ldaps', user: 'cn=liberty,ou=services,dc=example,dc=com', host: 'ad.example.com', port: '636', database: 'dc=example,dc=com' })
    expect(targetField(p!)).toBe('base')
    expect(poolSummary('ldap://svc%40corp.local@10.0.0.5:3268')).toEqual({ dialect: 'ldap', where: '10.0.0.5:3268' })
    expect(poolFigures({ url: 'ldaps://cn=x@ad:636/dc=y', password: 'ENC:…' })).toMatchObject({ dialect: 'ldap', where: 'ad:636 / dc=y', user: 'cn=x', passwordSet: true })
  })

  it('both schemes are the one kind, over TLS or plain', () => {
    expect(kindOf('ldaps')).toBe('ldap')
    expect(kindOf('ldap')).toBe('ldap')
    expect(kindOf('postgresql+asyncpg')).toBe('postgresql')
    expect(isDirectory('ldaps')).toBe(true)
    expect(isDirectory('oracle+oracledb')).toBe(false)
    expect(driversFor('ldap')).toEqual(['ldaps', 'ldap'])
  })

  it('moves a pool to or from a directory keeping the host and the account, not what named the database', () => {
    expect(withDialect('postgresql+asyncpg://app@db.example.com:5432/app', 'ldap')).toBe('ldaps://app@db.example.com:636')
    expect(withDialect('ldap://cn=x@ad.example.com:389/dc=y', 'postgresql')).toBe('postgresql+asyncpg://cn=x@ad.example.com:5432')
    expect(withDialect('ldaps://cn=x@ad.example.com:3269/dc=y', 'oracle')).toBe('oracle+oracledb://cn=x@ad.example.com:3269')    // a port of its own stays
    expect(withDialect('ldap://cn=x@ad:389', 'ldap')).toBe('ldap://cn=x@ad:389')                                                // already a directory: plain stays plain
  })

  it("writes the account as typed, escaping only what would end it in the URL", () => {
    expect(accountInUrl('cn=liberty,ou=services,dc=example,dc=com')).toBe('cn=liberty,ou=services,dc=example,dc=com')
    expect(accountInUrl('svc@corp.local')).toBe('svc%40corp.local')
    expect(accountInUrl('CORP\\svc')).toBe('CORP\\svc')
    expect(accountShown(accountInUrl('a:b/c?d%e@f'))).toBe('a:b/c?d%e@f')
    // and the URL built with it is still one the page shows field by field
    expect(urlParts(`ldaps://${accountInUrl('svc@corp.local')}@ad.example.com:636`)?.user).toBe('svc%40corp.local')
  })
})

describe('an HTTP server is a kind of pool', () => {
  it('is read like the others: host, port, account, base path', () => {
    const p = urlParts('https://JDE@ais.example.com:7077/jderest/v2')
    expect(p).toMatchObject({ scheme: 'https', user: 'JDE', host: 'ais.example.com', port: '7077', database: 'jderest/v2' })
    expect(targetField(p!)).toBe('path')
    expect(kindOf('https')).toBe('http')
    expect(kindOf('http')).toBe('http')
    expect(isServer('https')).toBe(true)
    expect(isServer('ldaps')).toBe(false)
    expect(poolSummary('https://JDE@ais.example.com:7077/jderest/v2')).toEqual({ dialect: 'http', where: 'ais.example.com:7077/jderest/v2' })
    expect(driversFor('https')).toEqual(['https', 'http'])
    expect(NOT_A_DATABASE).toEqual(['ldap', 'http'])
  })

  it('moves a pool to or from a server keeping the host and the account only', () => {
    expect(withDialect('postgresql+asyncpg://app@db.example.com:5432/app', 'http')).toBe('https://app@db.example.com')
    expect(withDialect('https://JDE@ais.example.com:7077/jderest/v2', 'ldap')).toBe('ldaps://JDE@ais.example.com:7077')
  })

  it('lists the pools of one kind for a picker', () => {
    const pools = { jde: { url: 'oracle+oracledb://jde@ora:1521/?service_name=JDEPD' }, jde_ais: { url: 'https://JDE@ais.example.com:7077/jderest/v2' },
      corp: { url: 'ldaps://cn=x@ad.example.com:636/dc=y' }, odd: { url: 'not a url' }, none: {} }
    expect(poolsOfKind(pools, 'http')).toEqual([{ value: 'jde_ais', label: 'ais.example.com:7077/jderest/v2', mono: 'jde_ais' }])
    expect(poolsOfKind(pools, 'ldap').map((o) => o.value)).toEqual(['corp'])
    expect(poolsOfKind(null, 'http')).toEqual([])
  })
})
