// The API connector editor's Environments tab: the pool its own server serves (``pool``) and one
// server per other pool (``targets``) — AIS per JDE environment. A call follows the environment
// picked for the screen (or a job step's ``pool``); nobody chooses the server. The environments
// that would be refused (a pool of the same SQL connectors without a server) are listed up front.
// Logic in services/apiTargets.ts.
import { useContext, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ChevronDown, ChevronRight, Plus, Trash2 } from 'lucide-react'
import { Banner, FrameworkEnumsContext, Input, SearchSelect, Stack, useModals } from '../../common'
import { colors, fonts } from '../../theme'
import { type ApiTarget, cleanTarget, missingTargets, type SqlPools } from '../../services/apiTargets'
import { EndpointBody, EndpointCard, EndpointHead, FieldRow, Hint, SectionHead } from './apiEditorAtoms'

type Value = { pool?: string | null; targets?: Record<string, ApiTarget> }

export default function ApiEnvironmentsTab({ value, patch, sqlConnectors }: {
  value: Value
  patch: (p: { pool?: string | null; targets?: Record<string, ApiTarget> }) => void
  /** Every SQL connector's pools — to tell which environments have no server. */
  sqlConnectors: SqlPools[]
}) {
  const { t } = useTranslation()
  const modals = useModals()
  const enums = useContext(FrameworkEnumsContext)
  const poolOpts = (enums?.POOL_NAMES?.values ?? []) as Array<{ value: string; label: string; mono?: string }>
  const targets = value.targets ?? {}
  const names = Object.keys(targets)
  const [open, setOpen] = useState<string | null>(null)
  const missing = missingTargets(value.pool, targets, sqlConnectors)

  const setTargets = (next: Record<string, ApiTarget>) => patch({ targets: Object.keys(next).length ? next : undefined })
  const setTarget = (pool: string, tgt: ApiTarget) => setTargets({ ...targets, [pool]: cleanTarget(tgt) })
  const add = (pool: string) => {
    if (!pool || pool === value.pool || pool in targets) return
    setTargets({ ...targets, [pool]: {} })
    setOpen(pool)
  }
  const remove = async (pool: string) => {
    const ok = await modals.confirm({
      title: t('settings.api.env.remove'),
      message: t('settings.api.env.confirmRemove', { pool }),
      variant: 'danger',
    })
    if (!ok) return
    const { [pool]: _gone, ...rest } = targets
    void _gone
    setTargets(rest)
  }
  const addable = poolOpts.filter((o) => o.value !== value.pool && !(o.value in targets))

  return (
    <Stack gap={4}>
      <Hint>{t('settings.api.env.intro')}</Hint>
      <SectionHead>{t('settings.api.env.ownSection')}</SectionHead>
      <FieldRow>
        <label>{t('settings.api.env.pool')}</label>
        <SearchSelect value={value.pool ?? ''} options={poolOpts} placeholder={t('settings.api.env.poolNone')}
          onChange={(v) => patch({ pool: v || null })} />
      </FieldRow>
      {names.length > 0 && !value.pool && <Banner $tone="error">{t('settings.api.env.poolRequired')}</Banner>}
      {missing.length > 0 && <Banner $tone="warning">{t('settings.api.env.missing', { pools: missing.join(', ') })}</Banner>}

      <SectionHead>{t('settings.api.env.targetsSection')}</SectionHead>
      {names.length === 0 && <Hint>{t('settings.api.env.empty')}</Hint>}
      {names.map((pool) => {
        const tgt = targets[pool]
        const isOpen = open === pool
        return (
          <EndpointCard key={pool}>
            <EndpointHead $open={isOpen} type="button" onClick={() => setOpen(isOpen ? null : pool)}>
              {isOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
              <span className="name">{pool}</span>
              {tgt.base_url && <span style={{ color: colors.text.muted, fontFamily: fonts.sans }}>· {tgt.base_url}</span>}
              <span className="grow" />
              <Trash2 size={13} style={{ color: colors.text.muted }} aria-label={t('settings.api.env.remove')}
                onClick={(e) => { e.stopPropagation(); void remove(pool) }} />
            </EndpointHead>
            {isOpen && (
              <EndpointBody>
                <FieldRow>
                  <label>{t('settings.api.connection.baseUrl')}</label>
                  <Input value={tgt.base_url ?? ''} placeholder={t('settings.api.env.inherited')}
                    onChange={(e) => setTarget(pool, { ...tgt, base_url: e.target.value })} />
                </FieldRow>
                <FieldRow>
                  <label>{t('settings.api.auth.username')}</label>
                  <Input value={tgt.auth_username ?? ''} placeholder={t('settings.api.env.inherited')}
                    onChange={(e) => setTarget(pool, { ...tgt, auth_username: e.target.value })} />
                </FieldRow>
                <FieldRow>
                  <label>{t('settings.api.auth.password')}</label>
                  <Input type="password" value={tgt.auth_password ?? ''} placeholder={t('settings.api.env.inherited')}
                    onChange={(e) => setTarget(pool, { ...tgt, auth_password: e.target.value })} />
                </FieldRow>
                <FieldRow>
                  <label>{t('settings.api.auth.token')}</label>
                  <Input type="password" value={tgt.auth_token ?? ''} placeholder={t('settings.api.env.inherited')}
                    onChange={(e) => setTarget(pool, { ...tgt, auth_token: e.target.value })} />
                </FieldRow>
                <FieldRow>
                  <label>{t('settings.api.connection.sslVerify')}</label>
                  <SearchSelect
                    value={tgt.verify_ssl === true ? 'true' : tgt.verify_ssl === false ? 'false' : ''}
                    options={[
                      { value: '', label: t('settings.api.env.inherited') },
                      { value: 'true', label: t('settings.api.connection.sslOn') },
                      { value: 'false', label: t('settings.api.connection.sslOff') },
                    ]}
                    onChange={(v) => setTarget(pool, { ...tgt, verify_ssl: v === 'true' ? true : v === 'false' ? false : undefined })} />
                </FieldRow>
              </EndpointBody>
            )}
          </EndpointCard>
        )
      })}
      <FieldRow>
        <label><Plus size={13} /> {t('settings.api.env.add')}</label>
        <SearchSelect value="" options={addable} placeholder={t('settings.api.env.addPick')} onChange={add} />
      </FieldRow>
    </Stack>
  )
}
