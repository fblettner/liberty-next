// nomaflow Job editor. Increment 2 ships a read-only view — it loads the job's full
// config from GET /admin/config/jobs/parsed and shows it. Increment 3 replaces the
// read-only body with the job-level form; increment 4 adds the step-pipeline editor.
import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { ArrowLeft, Workflow } from 'lucide-react'
import { api, ApiError } from '../../api/client'
import { PageLayout, Button, Banner, Centered, Pre } from '../../common'
import type { JobConfig, JobsParsedResponse } from './types'

export default function JobEditor() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { id } = useParams()           // undefined on /nomaflow/jobs/new
  const isNew = id === undefined
  const [job, setJob] = useState<JobConfig | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    if (isNew) { setLoaded(true); return }
    setError(null)
    api.get<JobsParsedResponse>('/admin/config/jobs/parsed')
      .then((r) => {
        const found = r.jobs.find((j) => j.id === id)
        if (!found) setError(t('nomaflow.editor.notFound', { id }))
        else setJob(found)
      })
      .catch((e) => setError(e instanceof ApiError
        ? (e.status === 403 ? t('nomaflow.superuserRequired') : e.message)
        : String(e)))
      .finally(() => setLoaded(true))
  }, [id, isNew, t])

  return (
    <PageLayout
      icon={<Workflow size={18} />}
      title={isNew ? t('nomaflow.editor.newTitle') : t('nomaflow.editor.editTitle', { id })}
      description={t('nomaflow.editor.subtitle')}
    >
      <Button $variant="ghost" $size="sm" onClick={() => navigate('/nomaflow')} style={{ marginBottom: 14, alignSelf: 'flex-start' }}>
        <ArrowLeft size={14} /> {t('nomaflow.editor.backToJobs')}
      </Button>
      {error && <Banner $tone="error">{error}</Banner>}
      {!loaded && !error && <Centered />}
      {loaded && !error && (
        <>
          <Banner $tone="info" style={{ marginBottom: 12 }}>
            {t('nomaflow.editor.readOnlyNotice')}
          </Banner>
          <Pre>{isNew ? t('nomaflow.editor.newPlaceholder') : JSON.stringify(job, null, 2)}</Pre>
        </>
      )}
    </PageLayout>
  )
}
