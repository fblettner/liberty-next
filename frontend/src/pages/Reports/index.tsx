// Reports feature area — list every report visible to the caller and let them
// run any of them with a parameter form, then download the result (PDF or
// markdown). Routed at /reports (App.tsx).
//
// The backend (liberty/web/reports.py) drives the metadata:
//   GET /api/reports                 → ReportListResponse { reports[] }
//   GET /api/reports/{scope}/{id}    → ReportDef (full, including params)
//   POST /api/reports/{scope}/{id}/run  body { params, format }  → binary blob
//
// The page uses the same PageLayout shell as Nomaflow / Settings — Banner for
// errors, Card per report, Modal for the run dialog. Per-report fetch (the
// list endpoint already returns full ReportDef) so opening the dialog has no
// extra round-trip; the dialog just renders ReportParam[] as typed inputs.
import { useCallback, useEffect, useMemo, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { FileText, Play, RefreshCw, Download } from 'lucide-react'
import { api, ApiError, authHeaders } from '../../api/client'
import {
  PageLayout,
  Button,
  Banner,
  Card,
  Centered,
  Field,
  Input,
  Select,
  Checkbox,
  Tag,
  Overlay,
  Modal,
  ModalHeader,
  ModalBody,
  ModalFooter,
  SpinnerRing,
} from '../../common'
import { colors, fontSize, fonts } from '../../theme'
import type { OutputFormat, ReportDef, ReportListResponse } from '../../types/reports'

// --------------------------------------------------------------------------- //
// Styled bits — mirror nomaflow's JobsList shapes for visual consistency.
// --------------------------------------------------------------------------- //
const List = styled.div`display: flex; flex-direction: column; gap: 10px;`
const ReportCard = styled(Card)`display: flex; flex-direction: column; gap: 8px;`
const CardTop = styled.div`display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap;`
const ReportTitle = styled.span`
  font-weight: 600; color: ${colors.text.primary}; font-size: ${fontSize.md};
`
const ReportScope = styled.span`
  font-family: ${fonts.mono}; font-size: ${fontSize.sm}; color: ${colors.text.muted};
`
const ReportDesc = styled.div`color: ${colors.text.secondary}; font-size: ${fontSize.sm};`
const Actions = styled.div`display: flex; align-items: center; gap: 6px;`
const Empty = styled.div`
  color: ${colors.text.muted}; font-size: ${fontSize.sm};
  padding: 28px 4px; text-align: center;
`
const ParamRow = styled.div`display: flex; flex-direction: column; gap: 4px;`
const ParamHint = styled.span`color: ${colors.text.muted}; font-size: ${fontSize.sm};`
const Toolbar = styled.div`display: flex; align-items: center; gap: 8px; margin-bottom: 14px;`
const ToolbarSpacer = styled.div`flex: 1;`

// --------------------------------------------------------------------------- //
// Page — list every visible report, group nothing (the operator wants a flat
// catalogue), click "Run" → opens RunReportDialog.
// --------------------------------------------------------------------------- //
export default function Reports() {
  const { t } = useTranslation()
  const [reports, setReports] = useState<ReportDef[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [running, setRunning] = useState<ReportDef | null>(null)

  const reload = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const r = await api.get<ReportListResponse>('/api/reports')
      setReports(r.reports)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
      setReports([])
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void reload() }, [reload])

  return (
    <PageLayout
      icon={<FileText size={18} />}
      title={t('reports.title')}
      description={t('reports.description')}
      headerRight={
        <Button $size="sm" $variant="ghost" onClick={() => void reload()} disabled={loading}>
          <RefreshCw size={14} /> {t('common.reload')}
        </Button>
      }
    >
      <Toolbar>
        <ToolbarSpacer />
        {reports && <Tag>{t('reports.count', { count: reports.length })}</Tag>}
      </Toolbar>

      {error && <Banner $tone="error">{error}</Banner>}
      {loading && !reports && <Centered />}

      {reports && reports.length === 0 && !loading && (
        <Empty>{t('reports.empty')}</Empty>
      )}

      <List>
        {(reports ?? []).map((r) => (
          <ReportCard key={`${r.scope}:${r.id}`}>
            <CardTop>
              <ReportTitle>{r.title}</ReportTitle>
              <ReportScope>{r.scope}:{r.id}</ReportScope>
              <ToolbarSpacer />
              <Actions>
                <Button
                  $size="sm"
                  $variant="primary"
                  onClick={() => setRunning(r)}
                >
                  <Play size={14} /> {t('reports.run')}
                </Button>
              </Actions>
            </CardTop>
            {r.description && <ReportDesc>{r.description}</ReportDesc>}
          </ReportCard>
        ))}
      </List>

      {running && (
        <RunReportDialog
          report={running}
          onClose={() => setRunning(null)}
        />
      )}
    </PageLayout>
  )
}

// --------------------------------------------------------------------------- //
// Run dialog — typed input per ReportParam + format selector + Run button.
// Submits to POST /api/reports/{scope}/{id}/run, takes the binary response as
// a blob, and triggers the browser download. The endpoint sends
// Content-Disposition with a sanitised filename so we just honour it (with
// a sensible fallback if it's missing).
// --------------------------------------------------------------------------- //
function RunReportDialog({ report, onClose }: { report: ReportDef; onClose: () => void }) {
  const { t } = useTranslation()
  const initial = useMemo<Record<string, unknown>>(() => {
    const out: Record<string, unknown> = {}
    for (const p of report.params) {
      out[p.name] = p.default ?? (p.type === 'bool' ? false : '')
    }
    return out
  }, [report])

  const [values, setValues] = useState<Record<string, unknown>>(initial)
  const [format, setFormat] = useState<OutputFormat>(
    report.formats.includes('pdf') ? 'pdf' : report.formats[0],
  )
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const updateValue = (name: string, raw: unknown) => {
    setValues((v) => ({ ...v, [name]: raw }))
  }

  const run = async () => {
    setRunning(true)
    setError(null)
    // Coerce empty strings on optional params back to undefined so the backend
    // sees "absent" rather than "empty string" (the coercion layer would
    // otherwise refuse to coerce '' to int / float / bool).
    const params: Record<string, unknown> = {}
    for (const p of report.params) {
      const v = values[p.name]
      if (v === '' && !p.required) continue
      if (v === undefined || v === null) {
        if (p.required) {
          setError(t('reports.errors.missingRequired', { name: p.label }))
          setRunning(false)
          return
        }
        continue
      }
      params[p.name] = v
    }

    const path = `/api/reports/${encodeURIComponent(report.scope)}/${encodeURIComponent(report.id)}/run`
    try {
      const res = await fetch(path, {
        method: 'POST',
        headers: authHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ params, format }),
      })
      if (!res.ok) {
        // Backend errors come back as { detail: "..." } in JSON
        let detail = `HTTP ${res.status}`
        try {
          const body = await res.json()
          if (typeof body?.detail === 'string') detail = body.detail
          else if (Array.isArray(body?.detail)) {
            detail = body.detail.map((d: { msg?: string }) => d?.msg ?? JSON.stringify(d)).join('; ')
          }
        } catch { /* non-JSON body */ }
        setError(detail)
        setRunning(false)
        return
      }
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      // Prefer the server-supplied filename from Content-Disposition; fall back
      // to a sensible default (the report id + format extension).
      const cd = res.headers.get('Content-Disposition') ?? ''
      const m = /filename\*?=(?:UTF-8'')?["']?([^;"'\n]+)/i.exec(cd)
      const ext = format === 'pdf' ? 'pdf' : 'md'
      const filename = (m?.[1] ?? `${report.id}.${ext}`).replace(/^.*[\\/]/, '')
      const a = document.createElement('a')
      a.href = url
      a.download = decodeURIComponent(filename)
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setRunning(false)
    }
  }

  return (
    <Overlay onClick={running ? undefined : onClose}>
      <Modal style={{ width: 560 }} onClick={(e) => e.stopPropagation()}>
        <ModalHeader>{report.title}</ModalHeader>
        <ModalBody>
          {report.description && <ParamHint>{report.description}</ParamHint>}

          {report.params.map((p) => (
            <ParamRow key={p.name}>
              <Field label={p.required ? `${p.label} *` : p.label} htmlFor={`param-${p.name}`}>
                <ParamInput
                  id={`param-${p.name}`}
                  param={p}
                  value={values[p.name]}
                  onChange={(v) => updateValue(p.name, v)}
                />
              </Field>
              {p.description && <ParamHint>{p.description}</ParamHint>}
            </ParamRow>
          ))}

          {report.formats.length > 1 && (
            <Field label={t('reports.format')} htmlFor="report-format">
              <Select
                id="report-format"
                value={format}
                onChange={(e) => setFormat(e.target.value as OutputFormat)}
              >
                {report.formats.map((f) => (
                  <option key={f} value={f}>{f.toUpperCase()}</option>
                ))}
              </Select>
            </Field>
          )}

          {error && <Banner $tone="error">{error}</Banner>}
        </ModalBody>
        <ModalFooter>
          <Button $size="sm" $variant="ghost" onClick={onClose} disabled={running}>
            {t('common.cancel')}
          </Button>
          <Button $size="sm" $variant="primary" onClick={() => void run()} disabled={running}>
            {running ? (
              <>
                <SpinnerRing size={14} /> {t('reports.running')}
              </>
            ) : (
              <>
                <Download size={14} /> {t('reports.runAndDownload')}
              </>
            )}
          </Button>
        </ModalFooter>
      </Modal>
    </Overlay>
  )
}

// Typed input per ReportParam.type — the backend coerces the value via
// liberty.coercion before calling the report, so we just pass the JS value
// shape it expects (number for int/float, boolean for bool, string for string).
function ParamInput({
  id, param, value, onChange,
}: {
  id: string
  param: import('../../types/reports').ReportParam
  value: unknown
  onChange: (v: unknown) => void
}) {
  if (param.type === 'bool') {
    return (
      <Checkbox
        id={id}
        checked={Boolean(value)}
        onChange={(checked) => onChange(checked)}
      />
    )
  }
  if (param.type === 'int' || param.type === 'float') {
    return (
      <Input
        id={id}
        type="number"
        step={param.type === 'int' ? 1 : 'any'}
        value={value === undefined || value === null ? '' : String(value)}
        onChange={(e) => {
          const raw = e.target.value
          if (raw === '') return onChange('')
          const n = param.type === 'int' ? parseInt(raw, 10) : parseFloat(raw)
          onChange(Number.isFinite(n) ? n : raw)
        }}
      />
    )
  }
  return (
    <Input
      id={id}
      type="text"
      value={value === undefined || value === null ? '' : String(value)}
      onChange={(e) => onChange(e.target.value)}
    />
  )
}
