// Developer reference → Command line (C12): every console script the package installs (read from
// its entry points) and, from each one's argparse parser, its global options and sub-commands with
// their arguments — usage, values, defaults, choices. Left, the scripts and their commands; right,
// the script's summary or the command's detail.
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { Check, Copy, Search } from 'lucide-react'
import { Banner, Button, Card, Centered, Tag } from '../../common'
import { colors, fontSize, fonts, radius } from '../../theme'
import { ApiError } from '../../api/client'
import { filterCommands, getCliReference } from '../../services/devref'
import type { CliArgument, CliReference, CliScript } from '../../types/devref'
import { Doc } from './Doc'
import { Body, Head, Main, Side, SideCat, SideItem, SideList, SideSearch } from './styled'

const Section = styled(Card)`display: flex; flex-direction: column; gap: 10px; min-width: 0;`
const Title = styled.div`font-size: ${fontSize.sm}; font-weight: 600; color: ${colors.text.secondary};`
const Usage = styled.div`
  display: flex; align-items: flex-start; gap: 8px; padding: 8px 10px; border: 1px solid ${colors.border}; border-radius: ${radius.md};
  background: ${colors.bg.input};
  & code { flex: 1; font-family: ${fonts.mono}; font-size: ${fontSize.sm}; color: ${colors.text.primary}; white-space: pre-wrap; overflow-wrap: anywhere; }
`
const Table = styled.table`
  width: 100%; border-collapse: collapse; font-size: ${fontSize.sm};
  & th { text-align: left; font-weight: 600; color: ${colors.text.muted}; padding: 6px 10px; border-bottom: 1px solid ${colors.border}; white-space: nowrap; }
  & td { padding: 7px 10px; border-bottom: 1px solid ${colors.border}; vertical-align: top; color: ${colors.text.secondary}; }
  & tr:last-child td { border-bottom: none; }
  & .mono { font-family: ${fonts.mono}; color: ${colors.text.primary}; white-space: nowrap; }
  & .flags { display: flex; gap: 4px; flex-wrap: wrap; margin-top: 4px; }
`
const Link = styled.button`border: none; background: none; padding: 0; cursor: pointer; font-family: ${fonts.mono}; font-size: ${fontSize.sm}; color: ${colors.blue.main};
  &:hover { text-decoration: underline; }`
const Sub = styled.span`width: 100%; font-size: ${fontSize.micro}; color: ${colors.text.muted}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;`
const Muted = styled.span`color: ${colors.text.muted};`

function CopyUsage({ text }: { text: string }) {
  const { t } = useTranslation()
  const [done, setDone] = useState(false)
  const copy = () => { void navigator.clipboard?.writeText(text).then(() => { setDone(true); window.setTimeout(() => setDone(false), 1200) }) }
  return (
    <Usage>
      <code>{text}</code>
      <Button type="button" $variant="ghost" $size="sm" onClick={copy} title={t('developer.cliCopy')}>{done ? <Check size={14} /> : <Copy size={14} />}</Button>
    </Usage>
  )
}

function Arguments({ args }: { args: CliArgument[] }) {
  const { t } = useTranslation()
  if (!args.length) return <Muted>{t('developer.cliNoArguments')}</Muted>
  return (
    <Table>
      <thead><tr><th>{t('developer.cliArgument')}</th><th>{t('developer.cliValue')}</th><th>{t('developer.cliDefault')}</th><th>{t('developer.colDescription')}</th></tr></thead>
      <tbody>
        {args.map((a) => (
          <tr key={a.name}>
            <td className="mono">{a.name}</td>
            <td className="mono">{a.value ?? (a.positional ? '' : <Muted>—</Muted>)}</td>
            <td className="mono">{a.default === null || a.default === '' ? <Muted>—</Muted> : String(a.default)}</td>
            <td>
              {a.help ?? <Muted>—</Muted>}
              {(a.required || a.repeat || a.choices.length > 0) && (
                <div className="flags">
                  {a.required && <Tag $tone="orange">{t('developer.cliRequired')}</Tag>}
                  {a.repeat && <Tag>{t('developer.cliRepeat')}</Tag>}
                  {a.choices.map((c) => <Tag key={c} $tone="blue">{c}</Tag>)}
                </div>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </Table>
  )
}

export default function CliView() {
  const { t } = useTranslation()
  const [ref, setRef] = useState<CliReference | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [q, setQ] = useState('')
  const [sel, setSel] = useState<{ script: string; command: string | null }>({ script: '', command: null })

  useEffect(() => {
    getCliReference().then((r) => { setRef(r); setSel((s) => (s.script ? s : { script: r.scripts[0]?.name ?? '', command: null })) })
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)))
  }, [])
  const groups = useMemo(() => filterCommands(ref?.scripts ?? [], q), [ref, q])

  if (error) return <Banner $tone="error">{error}</Banner>
  if (!ref) return <Centered />
  const script: CliScript | undefined = ref.scripts.find((s) => s.name === sel.script)
  const command = script?.commands.find((c) => c.name === sel.command) ?? null
  return (
    <Body>
      <Side>
        <SideSearch>
          <Search size={13} />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t('developer.cliSearch')} />
        </SideSearch>
        <SideList>
          {groups.map((g) => (
            <div key={g.script.name}>
              <SideCat>{g.script.name}</SideCat>
              <SideItem type="button" $on={sel.script === g.script.name && !sel.command} onClick={() => setSel({ script: g.script.name, command: null })}>
                <span className="name">{t('developer.cliOverview')}</span>
                <span className="n">{t('developer.cliCommandsN', { count: g.script.commands.length })}</span>
              </SideItem>
              {g.commands.map((c) => (
                <SideItem key={c.name} type="button" $on={sel.script === g.script.name && sel.command === c.name}
                  onClick={() => setSel({ script: g.script.name, command: c.name })} style={{ flexWrap: 'wrap', rowGap: 2 }} title={c.help ?? c.name}>
                  <span className="name">{c.name}</span>
                  {c.help && <Sub>{c.help}</Sub>}
                </SideItem>
              ))}
            </div>
          ))}
        </SideList>
      </Side>
      <Main style={{ overflow: 'auto' }}>
        {script && (
          <>
            <Head style={{ flexDirection: 'column', alignItems: 'stretch', gap: 8 }}>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
                <span className="title">{command ? `${script.name} ${command.name}` : script.name}</span>
                {command?.aliases.map((a) => <Tag key={a}>{t('developer.cliAlias', { name: a })}</Tag>)}
                <span className="sub">{script.file}{script.line ? `:${script.line}` : ''} · {script.entry}</span>
              </div>
              {command ? (command.description || command.help) && <Doc text={command.description || command.help || ''} />
                : script.description && <Doc text={script.description} />}
              <CopyUsage text={command ? command.usage : script.usage} />
            </Head>
            {command ? (
              <Section>
                <Title>{t('developer.cliArguments')}</Title>
                <Arguments args={command.arguments} />
              </Section>
            ) : (
              <>
                {script.options.length > 0 && (
                  <Section>
                    <Title>{t('developer.cliGlobalOptions')}</Title>
                    <Arguments args={script.options} />
                  </Section>
                )}
                <Section>
                  <Title>{t('developer.cliCommands')}</Title>
                  {script.commands.length === 0 ? <Muted>{t('developer.cliNoCommands')}</Muted> : (
                    <Table>
                      <thead><tr><th>{t('developer.cliCommand')}</th><th>{t('developer.colDescription')}</th><th>{t('developer.cliArguments')}</th></tr></thead>
                      <tbody>
                        {script.commands.map((c) => (
                          <tr key={c.name}>
                            <td><Link type="button" onClick={() => setSel({ script: script.name, command: c.name })}>{c.name}</Link></td>
                            <td>{c.help ?? <Muted>—</Muted>}</td>
                            <td className="mono">{c.arguments.length || <Muted>—</Muted>}</td>
                          </tr>
                        ))}
                      </tbody>
                    </Table>
                  )}
                </Section>
              </>
            )}
          </>
        )}
      </Main>
    </Body>
  )
}
