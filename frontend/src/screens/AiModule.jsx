import { Fragment, useCallback, useEffect, useMemo, useState } from 'react';
import * as api from '../api';
import DataTable from '../components/DataTable';

const LS_SELECTED_NM_ID = 'ai_module_selected_nm_id';
const LS_HIDE_COMPARISON_CALLOUT = 'ai_module_hide_comparison_callout';
const LS_ONBOARDING_CONFIRMED = 'ai_module_onboarding_confirmed_v1';

function lsGet(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}

function lsSet(key, value) {
  try { localStorage.setItem(key, value); } catch { /* ignore */ }
}

function softCardStyle() {
  return {
    border: '1px solid rgba(2,6,23,0.08)',
    borderRadius: 12,
    background: '#fff',
  };
}

function statusBadge(status) {
  const s = String(status || '');
  const map = {
    new: { bg: 'rgba(59,130,246,0.10)', color: '#1d4ed8', label: 'Новая' },
    in_progress: { bg: 'rgba(124,58,237,0.10)', color: '#6d28d9', label: 'В работе' },
    completed: { bg: 'rgba(16,172,132,0.12)', color: '#0f766e', label: 'Готово' },
    cancelled: { bg: 'rgba(239,68,68,0.10)', color: '#b91c1c', label: 'Отменено' },
    draft: { bg: 'rgba(59,130,246,0.10)', color: '#1d4ed8', label: 'Черновик' },
    running: { bg: 'rgba(124,58,237,0.10)', color: '#6d28d9', label: 'Идёт' },
    finished: { bg: 'rgba(16,172,132,0.12)', color: '#0f766e', label: 'Готово' },
  };
  const v = map[s] || { bg: 'rgba(0,0,0,0.06)', color: 'var(--text-secondary)', label: s || '—' };
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        padding: '4px 10px',
        borderRadius: 999,
        background: v.bg,
        color: v.color,
        border: '1px solid rgba(0,0,0,0.06)',
        fontSize: 12,
        fontWeight: 700,
        whiteSpace: 'nowrap',
      }}
    >
      {v.label}
    </span>
  );
}

const VITE_PRODUCT_GEN_UI_STUB = import.meta.env.VITE_PRODUCT_GEN_UI_STUB === '1';

function parseOptionalPositiveDecimal(raw) {
  const s = String(raw ?? '').trim().replace(',', '.');
  if (!s) return { ok: false, error: 'Обязательное поле' };
  const n = Number(s);
  if (!Number.isFinite(n) || n < 0) return { ok: false, error: 'Введите неотрицательное число' };
  return { ok: true, value: n };
}

function parsePriceRubToKopeks(raw) {
  const s = String(raw ?? '').trim().replace(',', '.');
  if (!s) return { ok: false, error: 'Укажите цену' };
  const n = Number(s);
  if (!Number.isFinite(n) || n < 0) return { ok: false, error: 'Некорректная цена' };
  const kopeks = Math.round(n * 100);
  if (!Number.isFinite(kopeks) || kopeks < 0 || kopeks > 9_999_999_999) {
    return { ok: false, error: 'Слишком большая цена' };
  }
  return { ok: true, value: kopeks };
}

/** PG-2.4: ID предмета WB опционален; пустая строка — без ошибки. */
function parseOptionalWbSubjectId(raw) {
  const t = String(raw ?? '').trim();
  if (!t) return { ok: true, value: undefined };
  if (!/^\d+$/.test(t)) {
    return { ok: false, error: 'ID предмета WB: только целое число (или оставьте пустым)' };
  }
  const n = Number(t);
  if (!Number.isSafeInteger(n) || n < 1) {
    return { ok: false, error: 'Некорректный ID предмета WB' };
  }
  return { ok: true, value: n };
}

function formatPipelineIsoBrief(iso) {
  const s = String(iso || '').trim();
  if (!s) return '—';
  return s.replace('T', ' ').slice(0, 19);
}

/** PG-UI: человекочитаемый лог image-run (`image_pipeline.timeline` с бэка). */
function ProductGenerationPipelineLogModal({ open, jobId, onClose }) {
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState('');
  const [timeline, setTimeline] = useState([]);
  const [meta, setMeta] = useState({ remote: '', pipelineRunId: '', isLocalStub: false });

  useEffect(() => {
    if (!open) return undefined;
    const jid = String(jobId || '').trim();
    if (!jid) return undefined;
    let cancelled = false;
    setLoading(true);
    setErr('');
    setTimeline([]);
    setMeta({ remote: '', pipelineRunId: '', isLocalStub: false });
    (async () => {
      try {
        const job = await api.getProductGenerationJob(jid);
        if (cancelled) return;
        const pr = String(job?.pipeline_run_id ?? '').trim();
        const local = pr.startsWith('local-');
        setMeta({
          remote: String(job?.image_pipeline?.remote_status || ''),
          pipelineRunId: pr,
          isLocalStub: local,
        });
        if (local) {
          setTimeline([]);
          setErr('');
          return;
        }
        const tl = Array.isArray(job?.image_pipeline?.timeline) ? job.image_pipeline.timeline : [];
        setTimeline(tl);
        if (!tl.length && !job?.image_pipeline) {
          setErr(
            'Удалённый пайплайн не настроен, run не удалённый, или GET к WIP вернул пусто. Проверьте PRODUCT_GEN_IMAGE_PIPELINE_* на api и сеть до wb_image_pipeline_api.',
          );
        } else if (!tl.length) {
          setErr('WIP не вернул шаги для построения хронологии (пустой `steps` или снимок без данных).');
        } else {
          setErr('');
        }
      } catch (e) {
        if (!cancelled) setErr(e?.message || 'Не удалось загрузить задачу');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, jobId]);

  if (!open) return null;

  return (
    <ModalShell
      open={open}
      title="Лог пайплайна (image-run)"
      onClose={onClose}
      width="min(560px, 100%)"
      footer={(
        <button type="button" className="btn btn-primary" onClick={onClose}>
          Закрыть
        </button>
      )}
    >
      <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 10 }}>
        Задача <code>{String(jobId || '')}</code>
        {meta.pipelineRunId ? (
          <>
            {' · '}
            run <code>{meta.pipelineRunId}</code>
            {meta.remote ? (
              <>
                {' · '}
                статус WIP: <strong>{meta.remote}</strong>
              </>
            ) : null}
          </>
        ) : null}
      </div>
      {loading ? <div style={{ color: 'var(--text-tertiary)' }}>Загрузка…</div> : null}
      {err ? <div className="alert alert-warning" style={{ margin: 0 }}>{err}</div> : null}
      {!loading && meta.isLocalStub ? (
        <div className="alert alert-info" style={{ margin: 0 }}>
          Локальный run <code>local-*</code>: wb_image_pipeline_service не используется; пошаговый лог OpenAI здесь
          не ведётся — только Celery-заглушка монолита.
        </div>
      ) : null}
      <div style={{ display: 'grid', gap: 12, maxHeight: 'min(62vh, 520px)', overflowY: 'auto', paddingRight: 4 }}>
        {timeline.map((e, i) => {
          const level = String(e?.level || 'info');
          const border = level === 'error' ? '#dc2626' : 'rgba(124,58,237,0.45)';
          return (
            <div key={`tl-${i}`} style={{ borderLeft: `3px solid ${border}`, paddingLeft: 12 }}>
              <div style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>{formatPipelineIsoBrief(e?.time)}</div>
              <div style={{ fontWeight: 800, fontSize: 13, marginTop: 2 }}>{String(e?.title || '')}</div>
              <pre
                style={{
                  whiteSpace: 'pre-wrap',
                  fontFamily: 'inherit',
                  fontSize: 12,
                  margin: '6px 0 0',
                  color: 'var(--text-secondary)',
                }}
              >
                {String(e?.body || '')}
              </pre>
            </div>
          );
        })}
      </div>
    </ModalShell>
  );
}

/** PG-2.1 (updated): сначала запуск каскадной генерации фото, затем отдельное создание товара. */
function ProductGenerationWizardModal({ open, onClose, onCreated, resumeJobId, onOpenPipelineLog }) {
  const [stage, setStage] = useState('prepare');
  const [jobId, setJobId] = useState('');
  const [jobStatus, setJobStatus] = useState('');
  const [remoteImageRunStatus, setRemoteImageRunStatus] = useState('');
  const [referenceFiles, setReferenceFiles] = useState([]);
  const [descriptionUser, setDescriptionUser] = useState('');
  const [dimensionsLength, setDimensionsLength] = useState('');
  const [dimensionsWidth, setDimensionsWidth] = useState('');
  const [dimensionsHeight, setDimensionsHeight] = useState('');
  const [weightBrutto, setWeightBrutto] = useState('');
  const [priceRub, setPriceRub] = useState('');
  const [vendorCode, setVendorCode] = useState('');
  const [title, setTitle] = useState('');
  const [brand, setBrand] = useState('');
  const [wbSubjectId, setWbSubjectId] = useState('');
  const [sizeRows, setSizeRows] = useState([{ tech_size: '', wb_size: '' }]);
  const [fieldErrors, setFieldErrors] = useState({});
  const [submitError, setSubmitError] = useState('');
  const [infoMessage, setInfoMessage] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [downloadingPhotos, setDownloadingPhotos] = useState(false);

  const resetForm = useCallback(() => {
    setStage('prepare');
    setJobId('');
    setJobStatus('');
    setReferenceFiles([]);
    setDescriptionUser('');
    setDimensionsLength('');
    setDimensionsWidth('');
    setDimensionsHeight('');
    setWeightBrutto('');
    setPriceRub('');
    setVendorCode('');
    setTitle('');
    setBrand('');
    setWbSubjectId('');
    setSizeRows([{ tech_size: '', wb_size: '' }]);
    setFieldErrors({});
    setSubmitError('');
    setInfoMessage('');
    setSubmitting(false);
    setDownloadingPhotos(false);
    setRemoteImageRunStatus('');
  }, []);

  useEffect(() => {
    if (!open) return undefined;
    const rid = String(resumeJobId || '').trim();
    if (!rid) {
      resetForm();
      return undefined;
    }
    let cancelled = false;
    resetForm();
    setStage('afterPhotos');
    setJobId(rid);
    setSubmitting(true);
    setSubmitError('');
    setInfoMessage('');
    (async () => {
      try {
        const job = await api.getProductGenerationJob(rid);
        if (cancelled) return;
        const st = String(job?.status || '');
        const remote = String(job?.image_pipeline?.remote_status || '');
        const lastErr = String(job?.image_pipeline?.last_error || '').trim();
        setJobStatus(st);
        setDescriptionUser(String(job?.description_user || ''));
        setRemoteImageRunStatus(remote || '—');
        if (remote === 'failed' || remote === 'error' || st === 'error') {
          setSubmitError(
            lastErr
              ? `Ошибка пайплайна: ${lastErr}`
              : 'Каскад генерации фото завершился с ошибкой. Проверьте ключ OpenAI (`AI_API_KEY` / `WIP_OPENAI_API_KEY`), логи контейнера wb_image_pipeline_worker и API WIP.',
          );
          setInfoMessage('');
        } else if (!job?.pipeline_run_id && st === 'draft') {
          setSubmitError('');
          setInfoMessage(
            'Черновик: удалённый run ещё не создан. Запустите шаг 1 в «Мастер: новая генерация» или POST /start для этой задачи в /docs.',
          );
        } else {
          setSubmitError('');
          setInfoMessage('Задача открыта из списка. Нажмите «Проверить готовность», чтобы обновить статус.');
        }
      } catch (e) {
        if (!cancelled) {
          setSubmitError(e?.message || 'Не удалось загрузить задачу');
          setRemoteImageRunStatus('—');
        }
      } finally {
        if (!cancelled) setSubmitting(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, resumeJobId, resetForm]);

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => {
      if (e.key === 'Escape') onClose?.();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  const onPickReferences = (e) => {
    const files = Array.from(e.target.files || []);
    setReferenceFiles(files);
    setFieldErrors((m) => {
      const next = { ...(m || {}) };
      delete next.referenceFiles;
      return next;
    });
  };

  const clearReferences = () => {
    setReferenceFiles([]);
    setInfoMessage('');
  };

  const validatePreparation = () => {
    const err = {};
    if (!String(descriptionUser || '').trim()) err.descriptionUser = 'Опишите товар для генерации';
    if (!referenceFiles.length) err.referenceFiles = 'Выберите хотя бы один файл-референс (изображение)';
    setFieldErrors(err);
    return Object.keys(err).length === 0;
  };

  const validateProductForm = () => {
    const err = {};
    if (!String(vendorCode || '').trim()) err.vendorCode = 'Укажите артикул (vendor code)';
    if (!String(title || '').trim()) err.title = 'Укажите наименование';
    if (!String(brand || '').trim()) err.brand = 'Укажите бренд';

    const sid = parseOptionalWbSubjectId(wbSubjectId);
    if (!sid.ok) err.wbSubjectId = sid.error;

    const pr = parsePriceRubToKopeks(priceRub);
    if (!pr.ok) err.priceRub = pr.error;

    const dl = parseOptionalPositiveDecimal(dimensionsLength);
    const dw = parseOptionalPositiveDecimal(dimensionsWidth);
    const dh = parseOptionalPositiveDecimal(dimensionsHeight);
    const wgt = parseOptionalPositiveDecimal(weightBrutto);
    if (!dl.ok) err.dimensionsLength = dl.error;
    if (!dw.ok) err.dimensionsWidth = dw.error;
    if (!dh.ok) err.dimensionsHeight = dh.error;
    if (!wgt.ok) err.weightBrutto = wgt.error;

    const rows = Array.isArray(sizeRows) ? sizeRows : [];
    const filled = rows
      .map((r) => ({
        tech: String(r?.tech_size ?? '').trim(),
        wb: String(r?.wb_size ?? '').trim(),
      }))
      .filter((r) => r.tech || r.wb);
    const complete = filled.filter((r) => r.tech && r.wb);
    const partial = filled.length > complete.length;
    if (partial) err.sizes = 'Для каждой строки заполните и techSize, и wbSize';
    if (complete.length === 0) err.sizes = 'Добавьте хотя бы одну пару размеров (techSize + wbSize)';

    setFieldErrors(err);
    return Object.keys(err).length === 0;
  };

  const buildPayload = () => {
    const pr = parsePriceRubToKopeks(priceRub);
    const dl = parseOptionalPositiveDecimal(dimensionsLength);
    const dw = parseOptionalPositiveDecimal(dimensionsWidth);
    const dh = parseOptionalPositiveDecimal(dimensionsHeight);
    const wgt = parseOptionalPositiveDecimal(weightBrutto);
    const sid = parseOptionalWbSubjectId(wbSubjectId);
    if (!pr.ok || !dl.ok || !dw.ok || !dh.ok || !wgt.ok || !sid.ok) return null;
    const rows = Array.isArray(sizeRows) ? sizeRows : [];
    const sizes = rows
      .map((r) => ({
        tech_size: String(r?.tech_size ?? '').trim(),
        wb_size: String(r?.wb_size ?? '').trim(),
      }))
      .filter((r) => r.tech_size && r.wb_size);
    const body = {
      vendor_code: String(vendorCode || '').trim(),
      title: String(title || '').trim(),
      brand: String(brand || '').trim(),
      description_user: String(descriptionUser || '').trim(),
      price_kopeks: pr.value,
      dimensions_length: dl.value,
      dimensions_width: dw.value,
      dimensions_height: dh.value,
      weight_brutto: wgt.value,
      sizes,
    };
    if (sid.value !== undefined) body.wb_subject_id = sid.value;
    return body;
  };

  const runPhotoGeneration = async () => {
    if (!validatePreparation()) return;
    setSubmitting(true);
    setSubmitError('');
    setInfoMessage('');
    try {
      const job = await api.createProductGenerationJob({
        description_user: String(descriptionUser || '').trim(),
      });
      const jid = String(job?.id || '').trim();
      if (!jid) throw new Error('Не удалось создать задачу генерации');
      await api.uploadProductGenerationJobReferences(jid, referenceFiles);
      const afterUpload = await api.getProductGenerationJob(jid);
      const refRows = Array.isArray(afterUpload?.reference_paths_json)
        ? afterUpload.reference_paths_json
        : [];
      if (!refRows.length) {
        throw new Error(
          'Референсы не появились в задаче после загрузки (пустой список). Проверьте ответ сервера и логи api.',
        );
      }
      const started = await api.startProductGenerationJob(jid);
      setJobId(jid);
      setJobStatus(String(started?.status || 'in_progress'));
      const rim = String(started?.image_pipeline?.remote_status || '');
      setRemoteImageRunStatus(rim || '…');
      setStage('afterPhotos');
      setInfoMessage('Генерация фото запущена. Можно закрыть форму и вернуться позже.');
      onCreated?.();
    } catch (e) {
      setSubmitError(e?.message || 'Не удалось запустить генерацию фото');
    } finally {
      setSubmitting(false);
    }
  };

  const refreshJobStatus = async () => {
    const jid = String(jobId || '').trim();
    if (!jid) return;
    setSubmitting(true);
    setSubmitError('');
    try {
      const current = await api.getProductGenerationJob(jid);
      const st = String(current?.status || '');
      setJobStatus(st);
      const rim = String(current?.image_pipeline?.remote_status || '');
      setRemoteImageRunStatus(rim || (current?.pipeline_run_id ? '…' : '—'));
      const lastErr = String(current?.image_pipeline?.last_error || '').trim();
      if (st === 'ready_to_publish' || st === 'published') {
        setInfoMessage('Фото готовы. Теперь можно открыть форму «Создать товар».');
      } else if (rim === 'failed' || rim === 'error' || st === 'error') {
        setInfoMessage('');
        setSubmitError(
          lastErr
            ? `Ошибка пайплайна: ${lastErr}`
            : 'Каскад генерации фото завершился с ошибкой. Проверьте ключи OpenAI и логи wb_image_pipeline_worker.',
        );
      } else {
        setInfoMessage('Фото ещё генерируются. Можно закрыть форму и зайти позже.');
      }
      onCreated?.();
    } catch (e) {
      setSubmitError(e?.message || 'Не удалось обновить статус');
    } finally {
      setSubmitting(false);
    }
  };

  const isReadyForProductForm = useMemo(
    () => jobStatus === 'ready_to_publish' || jobStatus === 'published',
    [jobStatus],
  );

  const openCreateProductForm = () => {
    setSubmitError('');
    setFieldErrors({});
    setStage('createProduct');
  };

  const goBack = () => {
    setSubmitError('');
    setFieldErrors({});
    if (stage === 'createProduct') {
      setStage('afterPhotos');
    }
  };

  const submit = async () => {
    if (!validateProductForm()) return;
    const payload = buildPayload();
    if (!payload) {
      setSubmitError('Проверьте числовые поля');
      return;
    }
    const jid = String(jobId || '').trim();
    if (!jid) {
      setSubmitError('Сначала запустите генерацию фото');
      return;
    }
    setSubmitting(true);
    setSubmitError('');
    try {
      await api.updateProductGenerationJob(jid, payload);
      onCreated?.();
      onClose?.();
    } catch (e) {
      setSubmitError(e?.message || 'Не удалось сохранить товар');
    } finally {
      setSubmitting(false);
    }
  };

  const addSizeRow = () => {
    setSizeRows((prev) => [...(Array.isArray(prev) ? prev : []), { tech_size: '', wb_size: '' }]);
  };

  const removeSizeRow = (idx) => {
    setSizeRows((prev) => {
      const list = Array.isArray(prev) ? prev.slice() : [];
      if (list.length <= 1) return list;
      list.splice(idx, 1);
      return list;
    });
  };

  const updateSizeRow = (idx, key, value) => {
    setSizeRows((prev) => {
      const list = Array.isArray(prev) ? prev.slice() : [];
      if (!list[idx]) return list;
      list[idx] = { ...list[idx], [key]: value };
      return list;
    });
  };

  const downloadGeneratedPhotos = async () => {
    const jid = String(jobId || '').trim();
    if (!jid) return;
    setDownloadingPhotos(true);
    setSubmitError('');
    setInfoMessage('');
    try {
      const current = await api.getProductGenerationJob(jid);
      const refs = Array.isArray(current?.reference_paths_json) ? current.reference_paths_json : [];
      const rows = refs.filter((r) => r && r.asset_id);
      if (!rows.length) throw new Error('Нет доступных фото для скачивания');
      for (const row of rows) {
        const aid = String(row.asset_id || '').trim();
        if (!aid) continue;
        const file = await api.downloadProductGenerationReference(jid, aid);
        const href = URL.createObjectURL(file.blob);
        const a = document.createElement('a');
        a.href = href;
        a.download = file.filename || `photo-${aid}.png`;
        a.click();
        URL.revokeObjectURL(href);
      }
      setInfoMessage('Фото сохранены на компьютер.');
    } catch (e) {
      setSubmitError(e?.message || 'Не удалось скачать фото');
    } finally {
      setDownloadingPhotos(false);
    }
  };

  if (!open) return null;

  const stageLabel = (
    stage === 'prepare'
      ? 'Референсы и запуск каскадной генерации фото'
      : stage === 'afterPhotos'
        ? 'Генерация фото и действия'
        : 'Создание товара'
  );

  return (
    <ModalShell
      open={open}
      title="Полная генерация товара"
      onClose={onClose}
      width="min(760px, 100%)"
      footer={(
        <>
          <button type="button" className="btn btn-outline-secondary" onClick={onClose} disabled={submitting || downloadingPhotos}>
            Закрыть
          </button>
          {stage === 'createProduct' ? (
            <button type="button" className="btn btn-outline-secondary" onClick={goBack} disabled={submitting}>
              Назад
            </button>
          ) : null}
          {stage === 'prepare' ? (
            <button type="button" className="btn btn-primary" onClick={runPhotoGeneration} disabled={submitting}>
              {submitting ? 'Запускаю…' : 'Запустить генерацию фото'}
            </button>
          ) : null}
          {stage === 'afterPhotos' ? (
            <>
              <button
                type="button"
                className="btn btn-outline-secondary"
                onClick={() => onOpenPipelineLog?.(jobId)}
                disabled={submitting || !String(jobId || '').trim()}
              >
                Лог пайплайна
              </button>
              <button type="button" className="btn btn-outline-secondary" onClick={downloadGeneratedPhotos} disabled={submitting || downloadingPhotos}>
                {downloadingPhotos ? 'Скачиваю…' : 'Скачать фото'}
              </button>
              <button type="button" className="btn btn-outline-secondary" onClick={refreshJobStatus} disabled={submitting}>
                {submitting ? 'Обновляю…' : 'Проверить готовность'}
              </button>
              <button type="button" className="btn btn-primary" onClick={openCreateProductForm} disabled={!isReadyForProductForm || submitting}>
                Создать товар
              </button>
            </>
          ) : null}
          {stage === 'createProduct' ? (
            <button type="button" className="btn btn-primary" onClick={submit} disabled={submitting}>
              {submitting ? 'Сохраняю…' : 'Сохранить товар'}
            </button>
          ) : null}
        </>
      )}
    >
      <div style={{ display: 'grid', gap: 12 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <span
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              padding: '3px 10px',
              borderRadius: 999,
              background: 'rgba(124,58,237,0.10)',
              color: '#5b21b6',
              border: '1px solid rgba(124,58,237,0.18)',
              fontSize: 12,
              fontWeight: 900,
              whiteSpace: 'nowrap',
            }}
          >
            {stage === 'prepare' ? 'Шаг 1 из 2' : stage === 'afterPhotos' ? 'Шаг 2 из 2' : 'Форма товара'}
          </span>
          <span style={{ fontWeight: 800, color: 'var(--text-secondary)', fontSize: 13 }}>{stageLabel}</span>
        </div>

        {submitError ? <div className="alert alert-danger" style={{ margin: 0 }}>{submitError}</div> : null}
        {infoMessage ? <div className="alert alert-info" style={{ margin: 0 }}>{infoMessage}</div> : null}

        {stage === 'prepare' && (
          <div style={{ display: 'grid', gap: 14 }}>
            <div style={{ ...softCardStyle(), padding: 12, display: 'grid', gap: 8 }}>
              <div style={{ fontWeight: 900, fontSize: 13 }}>Референсы</div>
              <div style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.45 }}>
                Нужен хотя бы один файл. Несколько файлов можно выбрать за раз. После нажатия «Запустить генерацию фото» начнётся каскадный пайплайн.
              </div>
              <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
                <input
                  type="file"
                  className="form-control"
                  style={{ flex: '1 1 240px' }}
                  accept="image/*"
                  multiple
                  onChange={onPickReferences}
                />
                {referenceFiles.length > 0 ? (
                  <button type="button" className="btn btn-sm btn-outline-secondary" onClick={clearReferences}>
                    Сбросить файлы
                  </button>
                ) : null}
              </div>
              {fieldErrors.referenceFiles ? (
                <div className="alert alert-warning" style={{ margin: 0, fontSize: 12 }}>{fieldErrors.referenceFiles}</div>
              ) : null}
              {referenceFiles.length > 0 ? (
                <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12, color: 'var(--text-secondary)' }}>
                  {referenceFiles.map((f, i) => (
                    <li key={`${f.name}-${f.size}-${i}`}>{f.name}</li>
                  ))}
                </ul>
              ) : (
                <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>Нет выбранных файлов — без референсов генерация не стартует</div>
              )}
            </div>

            <div>
              <label className="form-label" style={{ fontWeight: 800, fontSize: 12 }}>Текст о товаре</label>
              <textarea
                className={`form-control${fieldErrors.descriptionUser ? ' is-invalid' : ''}`}
                rows={6}
                value={descriptionUser}
                onChange={(e) => {
                  setDescriptionUser(e.target.value);
                  setFieldErrors((m) => {
                    const next = { ...(m || {}) };
                    delete next.descriptionUser;
                    return next;
                  });
                }}
                placeholder="Опишите товар, материал, особенности, для кого — это пойдёт в генерацию контента."
              />
              {fieldErrors.descriptionUser ? (
                <div className="invalid-feedback d-block">{fieldErrors.descriptionUser}</div>
              ) : null}
            </div>
          </div>
        )}

        {stage === 'afterPhotos' && (
          <div style={{ display: 'grid', gap: 12 }}>
            <div style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.5 }}>
              На этом шаге можно скачать фото и закрыть окно. Для продолжения нажмите «Создать товар» — откроется форма размеров, описания и других полей.
            </div>
            <div style={{ ...softCardStyle(), padding: 12, display: 'grid', gap: 8, fontSize: 13 }}>
              <InfoRow label="ID задачи">{jobId || '—'}</InfoRow>
              <InfoRow label="Статус">{productGenerationStatusBadge(jobStatus || 'in_progress')}</InfoRow>
              <InfoRow label="Image run (WIP)">{remoteImageRunStatus || '—'}</InfoRow>
              <InfoRow label="Референсы">{referenceFiles.length ? `${referenceFiles.length} файл(ов)` : 'Не выбраны'}</InfoRow>
              <InfoRow label="Текст">{String(descriptionUser || '').trim() || '—'}</InfoRow>
            </div>
            {!isReadyForProductForm ? (
              <div className="alert alert-warning" style={{ margin: 0 }}>
                Кнопка «Создать товар» станет доступной после статуса «К публикации» или «Опубликовано».
              </div>
            ) : null}
          </div>
        )}

        {stage === 'createProduct' && (
          <div style={{ display: 'grid', gap: 14 }}>
            <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
              Габариты и вес — отдельные поля (длина × ширина × высота, вес). Цена в рублях; на сервер уходит в копейках.
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 10 }}>
              <div>
                <label className="form-label" style={{ fontSize: 12, fontWeight: 800 }}>Длина</label>
                <input
                  className={`form-control form-control-sm${fieldErrors.dimensionsLength ? ' is-invalid' : ''}`}
                  value={dimensionsLength}
                  onChange={(e) => setDimensionsLength(e.target.value)}
                  inputMode="decimal"
                  placeholder="см"
                />
                {fieldErrors.dimensionsLength ? <div className="invalid-feedback d-block">{fieldErrors.dimensionsLength}</div> : null}
              </div>
              <div>
                <label className="form-label" style={{ fontSize: 12, fontWeight: 800 }}>Ширина</label>
                <input
                  className={`form-control form-control-sm${fieldErrors.dimensionsWidth ? ' is-invalid' : ''}`}
                  value={dimensionsWidth}
                  onChange={(e) => setDimensionsWidth(e.target.value)}
                  inputMode="decimal"
                  placeholder="см"
                />
                {fieldErrors.dimensionsWidth ? <div className="invalid-feedback d-block">{fieldErrors.dimensionsWidth}</div> : null}
              </div>
              <div>
                <label className="form-label" style={{ fontSize: 12, fontWeight: 800 }}>Высота</label>
                <input
                  className={`form-control form-control-sm${fieldErrors.dimensionsHeight ? ' is-invalid' : ''}`}
                  value={dimensionsHeight}
                  onChange={(e) => setDimensionsHeight(e.target.value)}
                  inputMode="decimal"
                  placeholder="см"
                />
                {fieldErrors.dimensionsHeight ? <div className="invalid-feedback d-block">{fieldErrors.dimensionsHeight}</div> : null}
              </div>
              <div>
                <label className="form-label" style={{ fontSize: 12, fontWeight: 800 }}>Вес, кг</label>
                <input
                  className={`form-control form-control-sm${fieldErrors.weightBrutto ? ' is-invalid' : ''}`}
                  value={weightBrutto}
                  onChange={(e) => setWeightBrutto(e.target.value)}
                  inputMode="decimal"
                />
                {fieldErrors.weightBrutto ? <div className="invalid-feedback d-block">{fieldErrors.weightBrutto}</div> : null}
              </div>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 10 }}>
              <div>
                <label className="form-label" style={{ fontSize: 12, fontWeight: 800 }}>Цена, ₽</label>
                <input
                  className={`form-control form-control-sm${fieldErrors.priceRub ? ' is-invalid' : ''}`}
                  value={priceRub}
                  onChange={(e) => setPriceRub(e.target.value)}
                  inputMode="decimal"
                  placeholder="например 1999.00"
                />
                {fieldErrors.priceRub ? <div className="invalid-feedback d-block">{fieldErrors.priceRub}</div> : null}
              </div>
              <div>
                <label className="form-label" style={{ fontSize: 12, fontWeight: 800 }}>Артикул</label>
                <input
                  className={`form-control form-control-sm${fieldErrors.vendorCode ? ' is-invalid' : ''}`}
                  value={vendorCode}
                  onChange={(e) => setVendorCode(e.target.value)}
                />
                {fieldErrors.vendorCode ? <div className="invalid-feedback d-block">{fieldErrors.vendorCode}</div> : null}
              </div>
              <div style={{ gridColumn: '1 / -1' }}>
                <label className="form-label" style={{ fontSize: 12, fontWeight: 800 }}>Наименование</label>
                <input
                  className={`form-control form-control-sm${fieldErrors.title ? ' is-invalid' : ''}`}
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                />
                {fieldErrors.title ? <div className="invalid-feedback d-block">{fieldErrors.title}</div> : null}
              </div>
              <div style={{ gridColumn: '1 / -1' }}>
                <label className="form-label" style={{ fontSize: 12, fontWeight: 800 }}>Бренд</label>
                <input
                  className={`form-control form-control-sm${fieldErrors.brand ? ' is-invalid' : ''}`}
                  value={brand}
                  onChange={(e) => setBrand(e.target.value)}
                />
                {fieldErrors.brand ? <div className="invalid-feedback d-block">{fieldErrors.brand}</div> : null}
              </div>
              <div style={{ gridColumn: '1 / -1' }}>
                <label className="form-label" style={{ fontSize: 12, fontWeight: 800 }}>
                  Категория WB (ID предмета){' '}
                  <span style={{ fontWeight: 600, color: 'var(--text-tertiary)' }}>— необязательно</span>
                </label>
                <input
                  className={`form-control form-control-sm${fieldErrors.wbSubjectId ? ' is-invalid' : ''}`}
                  value={wbSubjectId}
                  onChange={(e) => {
                    setWbSubjectId(e.target.value);
                    setFieldErrors((m) => {
                      const next = { ...(m || {}) };
                      delete next.wbSubjectId;
                      return next;
                    });
                  }}
                  inputMode="numeric"
                  placeholder="например 105 — можно оставить пустым"
                />
                <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 4, lineHeight: 1.4 }}>
                  Поле можно заполнить позже, но для публикации в WB оно может потребоваться.
                </div>
                {fieldErrors.wbSubjectId ? (
                  <div className="invalid-feedback d-block">{fieldErrors.wbSubjectId}</div>
                ) : null}
              </div>
            </div>

            <div style={{ ...softCardStyle(), padding: 12, display: 'grid', gap: 10 }}>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
                <div style={{ fontWeight: 900, fontSize: 13 }}>Размеры (techSize + wbSize)</div>
                <button type="button" className="btn btn-sm btn-outline-primary" onClick={addSizeRow}>
                  Добавить строку
                </button>
              </div>
              {fieldErrors.sizes ? <div className="alert alert-warning" style={{ margin: 0, fontSize: 12 }}>{fieldErrors.sizes}</div> : null}
              <div className="table-wrapper" style={{ marginTop: 0 }}>
                <table className="custom-table">
                  <thead>
                    <tr>
                      <th>techSize</th>
                      <th>wbSize</th>
                      <th style={{ width: 1 }} />
                    </tr>
                  </thead>
                  <tbody>
                    {sizeRows.map((row, idx) => (
                      <tr key={`sz-${idx}`}>
                        <td>
                          <input
                            className="form-control form-control-sm"
                            value={row.tech_size}
                            onChange={(e) => updateSizeRow(idx, 'tech_size', e.target.value)}
                            placeholder="например M"
                          />
                        </td>
                        <td>
                          <input
                            className="form-control form-control-sm"
                            value={row.wb_size}
                            onChange={(e) => updateSizeRow(idx, 'wb_size', e.target.value)}
                            placeholder="например 48"
                          />
                        </td>
                        <td>
                          <button
                            type="button"
                            className="btn btn-sm btn-outline-secondary"
                            disabled={sizeRows.length <= 1}
                            onClick={() => removeSizeRow(idx)}
                          >
                            Удалить
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}
      </div>
    </ModalShell>
  );
}

function productGenerationStatusBadge(status) {
  const s = String(status || '');
  const map = {
    draft: { bg: 'rgba(59,130,246,0.10)', color: '#1d4ed8', label: 'Черновик' },
    in_progress: { bg: 'rgba(124,58,237,0.10)', color: '#6d28d9', label: 'В процессе' },
    error: { bg: 'rgba(239,68,68,0.10)', color: '#b91c1c', label: 'Ошибка' },
    ready_to_publish: { bg: 'rgba(245,158,11,0.14)', color: '#b45309', label: 'К публикации' },
    published: { bg: 'rgba(16,172,132,0.12)', color: '#0f766e', label: 'Опубликовано' },
  };
  const v = map[s] || { bg: 'rgba(0,0,0,0.06)', color: 'var(--text-secondary)', label: s || '—' };
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        padding: '4px 10px',
        borderRadius: 999,
        background: v.bg,
        color: v.color,
        border: '1px solid rgba(0,0,0,0.06)',
        fontSize: 12,
        fontWeight: 700,
        whiteSpace: 'nowrap',
      }}
    >
      {v.label}
    </span>
  );
}

/** В таблице: если WIP-пайплайн уже failed, не показываем «В процессе» по полю job.status (оно не синкается автоматически). */
function effectiveProductGenerationListStatus(row) {
  const st = String(row?.status || '');
  const rs = String(row?.image_pipeline?.remote_status || '').toLowerCase();
  if (st === 'in_progress' && (rs === 'failed' || rs === 'error')) return 'error';
  return st;
}

function ProductGenerationAdminCard() {
  const [meChecked, setMeChecked] = useState(false);
  const [isAdmin, setIsAdmin] = useState(false);
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [creating, setCreating] = useState(false);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [wizardResumeJobId, setWizardResumeJobId] = useState(null);
  const [pipelineLogOpen, setPipelineLogOpen] = useState(false);
  const [pipelineLogJobId, setPipelineLogJobId] = useState(null);

  const openPipelineLog = (jid) => {
    const id = String(jid || '').trim();
    if (!id) return;
    setPipelineLogJobId(id);
    setPipelineLogOpen(true);
  };

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const me = await api.getMe();
        if (cancelled) return;
        setIsAdmin(Boolean(me?.is_admin));
      } catch {
        if (cancelled) return;
        setIsAdmin(false);
      } finally {
        if (!cancelled) setMeChecked(true);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const data = await api.listProductGenerationJobs();
      setItems(Array.isArray(data?.items) ? data.items : []);
    } catch (e) {
      setError(e?.message || 'Не удалось загрузить задачи');
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!meChecked || !isAdmin || VITE_PRODUCT_GEN_UI_STUB) return undefined;
    load();
    return undefined;
  }, [meChecked, isAdmin, load]);

  const needsImagePipelinePoll = useMemo(
    () =>
      items.some(
        (r) =>
          r?.status === 'in_progress' &&
          r?.pipeline_run_id &&
          !String(r.pipeline_run_id).startsWith('local-'),
      ),
    [items],
  );

  useEffect(() => {
    if (!meChecked || !isAdmin || VITE_PRODUCT_GEN_UI_STUB) return undefined;
    if (!needsImagePipelinePoll) return undefined;
    const timer = setInterval(() => {
      load();
    }, 4000);
    return () => clearInterval(timer);
  }, [meChecked, isAdmin, needsImagePipelinePoll, load]);

  if (!meChecked || !isAdmin) return null;

  if (VITE_PRODUCT_GEN_UI_STUB) {
    return (
      <div style={{ ...softCardStyle(), padding: 14 }}>
        <div style={{ fontWeight: 900, marginBottom: 6 }}>Полная генерация товара</div>
        <div style={{ color: 'var(--text-secondary)', fontSize: 13 }}>
          Скоро: мастер создания карточки с ИИ (фаза 2). Доступ только для администратора.
        </div>
      </div>
    );
  }

  const onCreateDraft = async () => {
    setCreating(true);
    setError('');
    try {
      await api.createProductGenerationJob({});
      await load();
    } catch (e) {
      setError(e?.message || 'Не удалось создать черновик');
    } finally {
      setCreating(false);
    }
  };

  return (
    <div style={{ ...softCardStyle(), padding: 14, display: 'grid', gap: 10 }}>
      <ProductGenerationPipelineLogModal
        open={pipelineLogOpen}
        jobId={pipelineLogJobId}
        onClose={() => {
          setPipelineLogOpen(false);
          setPipelineLogJobId(null);
        }}
      />
      <ProductGenerationWizardModal
        open={wizardOpen}
        resumeJobId={wizardResumeJobId}
        onClose={() => {
          setWizardOpen(false);
          setWizardResumeJobId(null);
        }}
        onCreated={load}
        onOpenPipelineLog={openPipelineLog}
      />
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
        <div style={{ fontWeight: 900, fontSize: 16 }}>Полная генерация товара</div>
        <button
          type="button"
          className="btn btn-primary btn-sm"
          onClick={() => {
            setWizardResumeJobId(null);
            setWizardOpen(true);
          }}
          disabled={loading}
        >
          Мастер: новая генерация
        </button>
        <button type="button" className="btn btn-outline-secondary btn-sm" onClick={onCreateDraft} disabled={creating || loading}>
          {creating ? 'Создаю…' : 'Пустой черновик'}
        </button>
        <button type="button" className="btn btn-outline-secondary btn-sm" onClick={load} disabled={loading}>
          {loading ? 'Обновление…' : 'Обновить'}
        </button>
      </div>
      <div style={{ color: 'var(--text-secondary)', fontSize: 13 }}>
        Мастер: референсы на диск API, затем POST /start. Если заданы{' '}
        <code style={{ fontSize: 12 }}>PRODUCT_GEN_IMAGE_PIPELINE_*</code>
        {' '}на бэке — создаётся run в wb_image_pipeline_service; иначе локальный{' '}
        <code style={{ fontSize: 12 }}>local-*</code> и Celery-заглушка. Таблица раз в 4 с опрашивает статус image-run. Строку можно снова открыть кнопкой «Открыть».
      </div>
      {error && <div className="alert alert-danger" style={{ margin: 0 }}>{error}</div>}
      {loading && items.length === 0 ? (
        <div style={{ color: 'var(--text-tertiary)' }}>Загрузка…</div>
      ) : items.length === 0 ? (
        <div style={{ color: 'var(--text-tertiary)' }}>Пока нет задач. Нажмите «Создать черновик», чтобы проверить API.</div>
      ) : (
        <div className="table-wrapper" style={{ marginTop: 0 }}>
          <table className="custom-table">
            <thead>
              <tr>
                <th>Статус</th>
                <th>Image run</th>
                <th>Название</th>
                <th>Артикул</th>
                <th>Создана</th>
                <th style={{ width: 1 }}>Действия</th>
              </tr>
            </thead>
            <tbody>
              {items.map((row) => (
                <tr key={String(row?.id)}>
                  <td style={{ whiteSpace: 'nowrap' }}>
                    {productGenerationStatusBadge(effectiveProductGenerationListStatus(row))}
                  </td>
                  <td style={{ whiteSpace: 'nowrap', fontSize: 12, color: 'var(--text-secondary)' }}>
                    {row?.image_pipeline?.remote_status
                      ? String(row.image_pipeline.remote_status)
                      : row?.pipeline_run_id && String(row.pipeline_run_id).startsWith('local-')
                        ? 'локально'
                        : row?.pipeline_run_id
                          ? '…'
                          : '—'}
                  </td>
                  <td>{row?.title || '—'}</td>
                  <td>{row?.vendor_code || '—'}</td>
                  <td style={{ whiteSpace: 'nowrap', fontSize: 12, color: 'var(--text-tertiary)' }}>
                    {row?.created_at ? String(row.created_at).replace('T', ' ').slice(0, 19) : '—'}
                  </td>
                  <td style={{ whiteSpace: 'nowrap' }}>
                    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                      <button
                        type="button"
                        className="btn btn-sm btn-outline-primary"
                        disabled={loading}
                        onClick={() => {
                          const id = String(row?.id || '').trim();
                          if (!id) return;
                          setWizardResumeJobId(id);
                          setWizardOpen(true);
                        }}
                      >
                        Открыть
                      </button>
                      <button
                        type="button"
                        className="btn btn-sm btn-outline-secondary"
                        disabled={loading}
                        onClick={() => openPipelineLog(row?.id)}
                      >
                        Лог
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function InfoRow({ label, children }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '180px 1fr', gap: 12, padding: '8px 0', borderBottom: '1px solid rgba(2,6,23,0.06)' }}>
      <div style={{ fontSize: 12, color: 'var(--text-tertiary)', fontWeight: 800, letterSpacing: '0.02em', textTransform: 'uppercase' }}>
        {label}
      </div>
      <div style={{ color: 'var(--text-secondary)', fontSize: 13, whiteSpace: 'pre-wrap' }}>
        {children}
      </div>
    </div>
  );
}

function ModalShell({ open, title, onClose, children, footer, width }) {
  if (!open) return null;
  const w = width || 'min(860px, 100%)';
  return (
    <div
      role="dialog"
      aria-modal="true"
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(2,6,23,0.55)',
        zIndex: 1000,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 16,
      }}
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose?.();
      }}
    >
      <div
        style={{
          width: w,
          maxWidth: '100%',
          background: '#fff',
          borderRadius: 12,
          border: '1px solid rgba(2,6,23,0.08)',
          boxShadow: '0 20px 60px rgba(2,6,23,0.25)',
          overflow: 'hidden',
        }}
      >
        <div style={{ padding: 14, borderBottom: '1px solid rgba(2,6,23,0.08)', display: 'flex', gap: 10, alignItems: 'center' }}>
          <div style={{ fontWeight: 900 }}>{title}</div>
          <button type="button" className="btn btn-sm btn-outline-secondary" onClick={onClose} style={{ marginLeft: 'auto' }}>
            Закрыть
          </button>
        </div>
        <div style={{ padding: 14 }}>
          {children}
        </div>
        {footer && (
          <div style={{ padding: 14, borderTop: '1px solid rgba(2,6,23,0.08)', display: 'flex', gap: 10, justifyContent: 'flex-end', flexWrap: 'wrap' }}>
            {footer}
          </div>
        )}
      </div>
    </div>
  );
}

function FirstRunBanner({
  step,
  selectedNmId,
  needsWbAccess,
  onPickProduct,
  onConfirmProduct,
  onGrantAccess,
  busy,
  errorText,
}) {
  if (!step) return null;
  const step1 = step === 1;
  const step2 = step === 2;

  return (
    <div
      style={{
        ...softCardStyle(),
        borderColor: 'rgba(124,58,237,0.22)',
        background: 'rgba(124,58,237,0.06)',
        padding: 14,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: 14,
        flexWrap: 'wrap',
      }}
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 260 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <span
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              padding: '3px 10px',
              borderRadius: 999,
              background: 'rgba(91,79,212,0.12)',
              color: '#4c42b8',
              border: '1px solid rgba(91,79,212,0.20)',
              fontSize: 12,
              fontWeight: 900,
              whiteSpace: 'nowrap',
            }}
          >
            Шаг {step}
          </span>
          <div style={{ fontWeight: 900 }}>
            {step1 ? 'Выберите товар' : 'Дайте доступ к кабинету WB'}
          </div>
        </div>
        <div style={{ color: 'var(--text-secondary)', fontSize: 13, maxWidth: 860 }}>
          {step1
            ? 'Выберите товар, с которым хотите работать, и нажмите OK.'
            : 'Нажмите “Выдать доступ”. После успешной авторизации плашка исчезнет.'}
        </div>
        {errorText && (
          <div className="alert alert-danger" style={{ margin: '6px 0 0 0' }}>
            {errorText}
          </div>
        )}
      </div>

      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        {step1 && (
          <>
            <button type="button" className="btn btn-outline-secondary btn-sm" onClick={onPickProduct} disabled={busy}>
              {selectedNmId ? `Сменить (сейчас ${selectedNmId})` : 'Выбрать товар'}
            </button>
            <button
              type="button"
              className="btn btn-primary btn-sm"
              onClick={onConfirmProduct}
              disabled={!selectedNmId || busy}
            >
              OK
            </button>
          </>
        )}
        {step2 && (
          <button
            type="button"
            className="btn btn-primary btn-sm"
            onClick={onGrantAccess}
            disabled={busy || !needsWbAccess}
            title={!needsWbAccess ? 'Доступ уже выдан' : undefined}
          >
            Выдать доступ
          </button>
        )}
      </div>
    </div>
  );
}

function ProductPickerModal({ open, onClose, onSelectNmId }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [q, setQ] = useState('');
  const [selected, setSelected] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const data = await api.getArticles();
      setItems(Array.isArray(data) ? data : []);
    } catch (e) {
      setError(e?.message || 'Не удалось загрузить товары');
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!open) return;
    setSelected(null);
    setQ('');
    load();
  }, [open, load]);

  const filtered = useMemo(() => {
    const query = (q || '').trim().toLowerCase();
    const list = Array.isArray(items) ? items : [];
    if (!query) return list.slice(0, 200);
    return list
      .filter((x) => {
        const nm = String(x?.nm_id ?? '').toLowerCase();
        const name = String(x?.name ?? '').toLowerCase();
        const vendor = String(x?.vendor_code ?? '').toLowerCase();
        return nm.includes(query) || name.includes(query) || vendor.includes(query);
      })
      .slice(0, 200);
  }, [items, q]);

  return (
    <ModalShell
      open={open}
      title="Выбор товара"
      onClose={onClose}
      footer={(
        <>
          <button type="button" className="btn btn-outline-secondary" onClick={onClose}>Отмена</button>
          <button
            type="button"
            className="btn btn-primary"
            disabled={!selected}
            onClick={() => {
              if (!selected) return;
              onSelectNmId?.(Number(selected));
              onClose?.();
            }}
          >
            ОК / Выбрать
          </button>
        </>
      )}
    >
      {error && <div className="alert alert-danger" style={{ marginTop: 0 }}>{error}</div>}
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', marginBottom: 10 }}>
        <input
          className="form-control"
          value={q}
          placeholder="Поиск по артикулу или названию"
          onChange={(e) => setQ(e.target.value)}
          style={{ flex: '1 1 320px' }}
        />
        <button type="button" className="btn btn-outline-secondary" onClick={load} disabled={loading}>
          {loading ? 'Загрузка…' : 'Обновить'}
        </button>
      </div>

      {loading ? (
        <div style={{ color: 'var(--text-tertiary)' }}>Загрузка…</div>
      ) : filtered.length === 0 ? (
        <div style={{ color: 'var(--text-tertiary)' }}>Товары не найдены</div>
      ) : (
        <div className="table-wrapper" style={{ marginTop: 0 }}>
          <table className="custom-table">
            <thead>
              <tr>
                <th />
                <th>Артикул</th>
                <th>Название</th>
                <th style={{ width: 220 }}>Код</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((x) => {
                const nm = Number(x?.nm_id);
                const isSel = selected === nm;
                return (
                  <tr
                    key={String(x?.nm_id)}
                    onClick={() => setSelected(nm)}
                    style={{ cursor: 'pointer', background: isSel ? 'rgba(124,58,237,0.06)' : undefined }}
                  >
                    <td style={{ width: 1 }}>
                      <input type="radio" checked={isSel} onChange={() => setSelected(nm)} />
                    </td>
                    <td style={{ fontWeight: 800 }}>{x?.nm_id ?? '—'}</td>
                    <td style={{ color: 'var(--text-secondary)' }}>{x?.name || '—'}</td>
                    <td style={{ color: 'var(--text-tertiary)', fontSize: 12 }}>{x?.vendor_code || '—'}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </ModalShell>
  );
}

function WbAccessModal({ open, onClose, onGranted }) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [file, setFile] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [remoteOpen, setRemoteOpen] = useState(false);
  const [remoteBusy, setRemoteBusy] = useState(false);
  const [remoteIframeNonce, setRemoteIframeNonce] = useState(0);
  const [remoteSessionEnsured, setRemoteSessionEnsured] = useState(false);
  const [checkedOnce, setCheckedOnce] = useState(false);

  useEffect(() => {
    if (!open) return;
    setError('');
    setSaving(false);
    setFile(null);
    setUploading(false);
    setRemoteOpen(false);
    setRemoteSessionEnsured(false);
    setCheckedOnce(false);
    setRemoteBusy(false);
    setRemoteIframeNonce(0);
  }, [open]);

  const ensureRemote = async () => {
    setRemoteBusy(true);
    setError('');
    try {
      const st = await api.getAiWbRemoteAuthStatus();
      if (st?.active) {
        setRemoteOpen(true);
        setRemoteIframeNonce((x) => x + 1);
        return;
      }
      await api.startAiWbRemoteAuth({ force: false });
      setRemoteOpen(true);
      setRemoteSessionEnsured(true);
      setRemoteIframeNonce((x) => x + 1);
    } catch (e) {
      // During rolling deploys the backend may not have /remote/status yet (404).
      // In that case, fall back to "start" without surfacing a scary error.
      const msg = String(e?.message || '');
      const looksLikeNotFound = msg.toLowerCase().includes('not found') || msg.includes('404');
      if (looksLikeNotFound) {
        try {
          await api.startAiWbRemoteAuth({ force: false });
          setRemoteOpen(true);
          setRemoteSessionEnsured(true);
          setRemoteIframeNonce((x) => x + 1);
          return;
        } catch (e2) {
          setError(e2?.message || 'Не удалось открыть окно авторизации');
          return;
        }
      }
      setError(msg || 'Не удалось открыть окно авторизации');
    } finally {
      setRemoteBusy(false);
      setCheckedOnce(true);
    }
  };

  const restartRemote = async () => {
    setRemoteBusy(true);
    setError('');
    try {
      await api.startAiWbRemoteAuth({ force: true });
      setRemoteOpen(true);
      setRemoteSessionEnsured(true);
      setRemoteIframeNonce((x) => x + 1);
    } catch (e) {
      setError(e?.message || 'Не удалось открыть окно авторизации');
    } finally {
      setRemoteBusy(false);
      setCheckedOnce(true);
    }
  };

  const finishRemote = async () => {
    setRemoteBusy(true);
    setError('');
    try {
      await api.saveAiWbRemoteAuth();
      onGranted?.();
      onClose?.();
    } catch (e) {
      setError(e?.message || 'Не удалось сохранить доступ');
    } finally {
      setRemoteBusy(false);
    }
  };

  const upload = async () => {
    if (!file) return;
    setUploading(true);
    setError('');
    try {
      await api.uploadAiWbAccessFile(file);
      onGranted?.();
      onClose?.();
    } catch (e) {
      setError(e?.message || 'Не удалось загрузить файл доступа');
    } finally {
      setUploading(false);
    }
  };

  const showUpload = String(error || '').toLowerCase().includes('no display') || String(error || '').toLowerCase().includes('storage_state');

  useEffect(() => {
    if (!open) return;
    if (showUpload) return;
    if (checkedOnce) return;
    ensureRemote();
  }, [open, showUpload, checkedOnce]);

  return (
    <ModalShell
      open={open}
      title="Выдать доступ к кабинету WB"
      onClose={onClose}
      footer={(
        <>
          <button type="button" className="btn btn-outline-secondary" onClick={onClose} disabled={saving}>Отмена</button>
          <button type="button" className="btn btn-outline-primary" onClick={restartRemote} disabled={saving || uploading || remoteBusy}>
            {remoteBusy ? 'Открываю…' : remoteSessionEnsured ? 'Переоткрыть окно' : 'Открыть окно'}
          </button>
          <button type="button" className="btn btn-primary" onClick={finishRemote} disabled={!remoteOpen || saving || uploading || remoteBusy}>
            {remoteBusy ? 'Сохраняю…' : 'Я вошёл'}
          </button>
        </>
      )}
    >
      <div style={{ color: 'var(--text-secondary)', fontSize: 13, marginBottom: 12 }}>
        Если сессия уже открыта, окно появится сразу. Если сессии нет — мы запустим её автоматически. Если окно “залипло”, нажмите “Открыть окно” для перезапуска.
        После успешного входа нажмите “Я вошёл”, чтобы сохранить доступ.
      </div>
      {error && <div className="alert alert-danger" style={{ marginTop: 0 }}>{error}</div>}

      {remoteOpen && (
        <div style={{ border: '1px solid rgba(2,6,23,0.10)', borderRadius: 12, overflow: 'hidden', height: 520 }}>
          <iframe
            title="WB remote login"
            key={`wb-remote-${remoteIframeNonce}`}
            src="/wb-auth/vnc.html?autoconnect=1&resize=scale"
            style={{ width: '100%', height: '100%', border: 0 }}
          />
        </div>
      )}

      {showUpload && (
        <div style={{ marginTop: 10, display: 'grid', gap: 10 }}>
          <div style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
            В локальном Docker окно браузера открыть нельзя. Загрузите “файл доступа” (JSON), который создаётся после входа в кабинет WB.
          </div>
          <input
            type="file"
            accept=".json,application/json"
            className="form-control"
            onChange={(e) => setFile(e.target.files?.[0] || null)}
          />
          <div>
            <button type="button" className="btn btn-outline-primary" disabled={!file || uploading || saving} onClick={upload}>
              {uploading ? 'Загружаю…' : 'Загрузить файл доступа'}
            </button>
          </div>
        </div>
      )}
    </ModalShell>
  );
}

function aiDetailsForTask(t) {
  const type = String(t?.task_type || '');
  const title = String(t?.title || 'Задача');
  const desc = String(t?.description || '').trim();
  const reason = String(t?.reason || '').trim();
  const humanBody = [desc, reason].filter(Boolean).join('\n\n');

  const base = {
    title,
    humanBody: humanBody || 'Задача от AI-модуля для улучшения карточки товара.',
    userAction: 'Выполните задачу и нажмите “Готово” (или “Отменить”, если задача неактуальна).',
  };

  if (type === 'wb_access_grant') {
    return {
      ...base,
      title: 'Дать доступ к кабинету WB',
      humanBody: 'Нужно выдать доступ к кабинету WB для получения отчётов и данных сравнения.',
      userAction: 'Нажмите “Выдать доступ” в шаге 2 и авторизуйтесь.',
    };
  }

  if (type === 'competitor_report_refresh') {
    return {
      ...base,
      title: title || 'Обновить отчёт сравнения',
      humanBody: humanBody || 'Нужно обновить отчёт сравнения карточек с конкурентами.',
      userAction: 'Создайте/обновите сравнение в кабинете WB (ваш товар + 4 конкурента), затем нажмите “Я создал сравнение”.',
    };
  }

  return base;
}

function aiDetailsForHypothesis(h) {
  const title = String(h?.title || 'Гипотеза');
  const trigger = String(h?.trigger_reason || '').trim();
  const desc = String(h?.description || '').trim();
  const humanBody = [desc, trigger].filter(Boolean).join('\n\n');
  return {
    title,
    humanBody: humanBody || 'Если выполнить действия по гипотезе, метрики карточки улучшатся.',
    userAction: 'Запустите гипотезу, выполняйте действия, фиксируйте результат и завершите её.',
  };
}

function ReviewRepliesApproval({ open, onClose }) {
  const [loading, setLoading] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState('');
  const [items, setItems] = useState([]);
  const [drafts, setDrafts] = useState({});
  const [busyId, setBusyId] = useState('');
  const [publishState, setPublishState] = useState({});

  const formatDateCell = (isoOrDate) => {
    const s = String(isoOrDate || '').trim();
    if (!s) return '—';
    return s.length >= 10 ? s.slice(0, 10) : s;
  };

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const data = await api.getAiPendingReviewReplies();
      const list = Array.isArray(data?.items) ? data.items : [];
      setItems(list);
      const init = {};
      for (const x of list) {
        const fid = String(x?.feedback_id || '');
        if (!fid) continue;
        init[fid] = String(x?.edited_reply || x?.suggested_reply || '');
      }
      setDrafts(init);
    } catch (e) {
      setItems([]);
      setError(e?.message || 'Не удалось загрузить отзывы');
    } finally {
      setLoading(false);
    }
  }, []);

  const sync = useCallback(async () => {
    setSyncing(true);
    setError('');
    try {
      await api.syncAiReviewReplies({ take: 20 });
      await load();
    } catch (e) {
      setError(e?.message || 'Не удалось синхронизировать отзывы');
    } finally {
      setSyncing(false);
    }
  }, [load]);

  useEffect(() => {
    if (!open) return;
    setItems([]);
    setDrafts({});
    setPublishState({});
    setError('');
    load();
  }, [open, load]);

  const publish = async (fid) => {
    const feedbackId = String(fid || '');
    if (!feedbackId) return;
    setBusyId(feedbackId);
    setError('');
    setPublishState((m) => ({ ...(m || {}), [feedbackId]: { status: 'publishing' } }));
    try {
      const text = String(drafts?.[feedbackId] || '').trim();
      await api.publishAiReviewReply(feedbackId, { text });
      setPublishState((m) => ({ ...(m || {}), [feedbackId]: { status: 'ok' } }));
      setItems((prev) => (Array.isArray(prev) ? prev.map((x) => (
        String(x?.feedback_id || '') === feedbackId
          ? { ...(x || {}), status: 'published', published_at: new Date().toISOString() }
          : x
      )) : prev));
    } catch (e) {
      setPublishState((m) => ({ ...(m || {}), [feedbackId]: { status: 'error' } }));
      setError(e?.message || 'Не удалось опубликовать ответ');
    } finally {
      setBusyId('');
    }
  };

  const rows = Array.isArray(items) ? items : [];

  return (
    <ModalShell
      open={open}
      title="Ответить на отзывы"
      onClose={onClose}
      width="min(860px, 100%)"
      footer={(
        <>
          <button type="button" className="btn btn-outline-secondary" onClick={onClose}>Закрыть</button>
          <button type="button" className="btn btn-outline-secondary" onClick={load} disabled={loading || syncing}>
            {loading ? 'Загрузка…' : 'Обновить'}
          </button>
          <button type="button" className="btn btn-primary" onClick={sync} disabled={loading || syncing}>
            {syncing ? 'Синхронизация…' : 'Синхронизировать из WB'}
          </button>
        </>
      )}
    >
      <div style={{ display: 'grid', gap: 10, maxHeight: 'min(78vh, 720px)' }}>
        <div style={{ ...softCardStyle(), padding: 12, background: 'linear-gradient(180deg, rgba(124,58,237,0.06), rgba(124,58,237,0.02))', borderColor: 'rgba(124,58,237,0.16)' }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
            <div style={{ fontWeight: 950, fontSize: 15, color: 'var(--text-primary)' }}>Неотвеченные отзывы</div>
            <div style={{ color: 'var(--text-tertiary)', fontSize: 12 }}>
              {rows.length ? `Найдено: ${rows.length}` : ' '}
            </div>
          </div>
          <div style={{ marginTop: 6, color: 'var(--text-secondary)', fontSize: 13, lineHeight: 1.5 }}>
            Можно отредактировать текст ответа и нажать “Опубликовать”. После ответа WB в строке появится статус “Опубликовано” или “Ошибка публикации”.
          </div>
        </div>
        {error && <div className="alert alert-danger" style={{ margin: 0 }}>{error}</div>}

        {loading ? (
          <div style={{ color: 'var(--text-tertiary)' }}>Загрузка…</div>
        ) : rows.length === 0 ? (
          <div style={{ color: 'var(--text-tertiary)' }}>Неотвеченных отзывов нет</div>
        ) : (
          <div style={{ display: 'grid', gap: 10, maxHeight: 'min(60vh, 520px)', overflow: 'auto', paddingRight: 2 }}>
            {rows.map((x) => {
              const fid = String(x?.feedback_id || '');
              const disabled = Boolean(busyId) && busyId !== fid;
              const busy = busyId === fid;
              const st = String(x?.status || 'pending');
              const ps = publishState?.[fid]?.status || '';
              const published = st === 'published' || ps === 'ok';
              const publishErr = st === 'error' || ps === 'error';
              const publishing = ps === 'publishing' || busy;
              const dateText = formatDateCell(x?.review_created_at || x?.first_seen_date);
              const ratingText = String(x?.rating || '—');

              return (
                <div
                  key={fid}
                  style={{
                    ...softCardStyle(),
                    padding: 12,
                    borderRadius: 14,
                    background: '#fff',
                  }}
                >
                  <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
                    <div style={{ display: 'inline-flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                      <span style={{ fontSize: 12, color: 'var(--text-tertiary)', fontWeight: 900 }}>
                        {dateText}
                      </span>
                      <span style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>•</span>
                      <span style={{ fontSize: 12, color: 'var(--text-secondary)', fontWeight: 900 }}>
                        Оценка: {ratingText}
                      </span>
                    </div>
                    <div style={{ marginLeft: 'auto' }}>
                      {published ? (
                        <span
                          style={{
                            display: 'inline-flex',
                            alignItems: 'center',
                            padding: '4px 10px',
                            borderRadius: 999,
                            background: 'rgba(16,185,129,0.12)',
                            border: '1px solid rgba(16,185,129,0.20)',
                            color: '#047857',
                            fontWeight: 900,
                            fontSize: 12,
                            whiteSpace: 'nowrap',
                          }}
                        >
                          Опубликовано
                        </span>
                      ) : publishErr ? (
                        <span
                          style={{
                            display: 'inline-flex',
                            alignItems: 'center',
                            padding: '4px 10px',
                            borderRadius: 999,
                            background: 'rgba(239,68,68,0.10)',
                            border: '1px solid rgba(239,68,68,0.20)',
                            color: '#b91c1c',
                            fontWeight: 900,
                            fontSize: 12,
                            whiteSpace: 'nowrap',
                          }}
                        >
                          Ошибка публикации
                        </span>
                      ) : publishing ? (
                        <span
                          style={{
                            display: 'inline-flex',
                            alignItems: 'center',
                            padding: '4px 10px',
                            borderRadius: 999,
                            background: 'rgba(59,130,246,0.10)',
                            border: '1px solid rgba(59,130,246,0.18)',
                            color: '#1d4ed8',
                            fontWeight: 900,
                            fontSize: 12,
                            whiteSpace: 'nowrap',
                          }}
                        >
                          Публикуем…
                        </span>
                      ) : null}
                    </div>
                  </div>

                  <div style={{ marginTop: 8, fontWeight: 950, fontSize: 13, color: 'var(--text-primary)', lineHeight: 1.25 }}>
                    {x?.product_name || '—'}
                  </div>

                  <div style={{ display: 'grid', gap: 10, marginTop: 10 }}>
                    <div style={{ ...softCardStyle(), padding: 10, background: 'rgba(2,6,23,0.02)' }}>
                      <div style={{ fontSize: 11, fontWeight: 900, letterSpacing: '0.02em', textTransform: 'uppercase', color: 'var(--text-tertiary)', marginBottom: 6 }}>
                        Отзыв
                      </div>
                      <div style={{ fontSize: 13, color: 'var(--text-secondary)', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', lineHeight: 1.4 }}>
                        {x?.review_text || '—'}
                      </div>
                      {x?.last_error ? (
                        <div style={{ marginTop: 8, color: '#b91c1c', fontSize: 12 }}>
                          Ошибка AI/WB: {String(x.last_error).slice(0, 200)}
                        </div>
                      ) : null}
                    </div>

                    <div>
                      <div style={{ fontSize: 11, fontWeight: 900, letterSpacing: '0.02em', textTransform: 'uppercase', color: 'var(--text-tertiary)', marginBottom: 6 }}>
                        Ответ (можно править)
                      </div>
                      <textarea
                        className="form-control form-control-sm"
                        rows={4}
                        value={String(drafts?.[fid] ?? '')}
                        onChange={(e) => setDrafts((m) => ({ ...(m || {}), [fid]: e.target.value }))}
                        placeholder="Ответ…"
                        disabled={busy || disabled}
                        style={{ resize: 'vertical', minHeight: 92 }}
                      />
                    </div>
                  </div>

                  <div style={{ display: 'flex', gap: 10, alignItems: 'center', justifyContent: 'flex-end', marginTop: 10, flexWrap: 'wrap' }}>
                    {publishErr ? (
                      <button
                        type="button"
                        className="btn btn-sm btn-outline-secondary"
                        onClick={() => publish(fid)}
                        disabled={publishing || disabled || !String(drafts?.[fid] || '').trim()}
                      >
                        {publishing ? 'Публикую…' : 'Повторить'}
                      </button>
                    ) : null}
                    {!published && !publishErr ? (
                      <button
                        type="button"
                        className="btn btn-sm btn-primary"
                        onClick={() => publish(fid)}
                        disabled={publishing || disabled || !String(drafts?.[fid] || '').trim()}
                      >
                        {publishing ? 'Публикую…' : 'Опубликовать'}
                      </button>
                    ) : null}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </ModalShell>
  );
}

function AiItemDetailsModal({ openItem, onClose, onPrimaryAction, primaryActionLabel, busy }) {
  if (!openItem) return null;
  const isTask = openItem.kind === 'task';
  const isHyp = openItem.kind === 'hypothesis';
  const details = isTask ? aiDetailsForTask(openItem.data) : aiDetailsForHypothesis(openItem.data);
  const status = openItem?.data?.status;
  const taskType = String(openItem?.data?.task_type || '');
  const isWbAccessTask = isTask && taskType === 'wb_access_grant';
  const isReviewTask = isTask && taskType === 'review_replies_daily';
  if (isReviewTask) {
    return <ReviewRepliesApproval open={Boolean(openItem)} onClose={onClose} />;
  }
  return (
    <ModalShell
      open={Boolean(openItem)}
      title={details.title}
      onClose={onClose}
      footer={(
        <>
          <button type="button" className="btn btn-outline-secondary" onClick={onClose} disabled={busy}>Закрыть</button>
          {primaryActionLabel && (
            <button type="button" className="btn btn-primary" onClick={onPrimaryAction} disabled={busy}>
              {busy ? '...' : primaryActionLabel}
            </button>
          )}
          {(isTask && !isWbAccessTask && (status === 'new' || status === 'in_progress')) && (
            <>
              <button type="button" className="btn" style={{ background: 'rgba(22,163,74,0.12)', border: '1px solid rgba(22,163,74,0.18)', color: '#166534', fontWeight: 800 }} onClick={() => openItem.onSetStatus?.('completed')} disabled={busy}>
                Готово
              </button>
              <button type="button" className="btn" style={{ background: 'rgba(220,38,38,0.08)', border: '1px solid rgba(220,38,38,0.18)', color: '#991b1b', fontWeight: 800 }} onClick={() => openItem.onSetStatus?.('cancelled')} disabled={busy}>
                Отменить
              </button>
            </>
          )}
          {isHyp && openItem.actions?.length ? openItem.actions.map((a) => (
            <button key={a.key} type="button" className={a.className} onClick={a.onClick} disabled={busy || a.disabled}>
              {a.label}
            </button>
          )) : null}
        </>
      )}
    >
      <div style={{ display: 'grid', gap: 10 }}>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
          <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>
            Статус: {statusBadge(status)}
          </div>
          {openItem?.data?.nm_id != null && (
            <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>
              Артикул: <span style={{ fontWeight: 800, color: 'var(--text-secondary)' }}>{openItem.data.nm_id}</span>
            </div>
          )}
        </div>
        <div style={{ ...softCardStyle(), padding: 12 }}>
          <div style={{ color: 'var(--text-secondary)', fontSize: 14, lineHeight: 1.5, whiteSpace: 'pre-wrap' }}>
            {details.humanBody}
          </div>
          <div style={{ marginTop: 14, paddingTop: 12, borderTop: '1px solid rgba(2,6,23,0.06)' }}>
            <InfoRow label="Что сделать">{details.userAction}</InfoRow>
          </div>
        </div>
      </div>
    </ModalShell>
  );
}

function ComparisonCallout({ visible, onConfirmCreated, onLater, onCreateTechnicalTask, busy, errorText }) {
  if (!visible) return null;
  return (
    <div
      style={{
        border: '1px solid rgba(124,58,237,0.22)',
        borderRadius: 12,
        background: 'rgba(124,58,237,0.06)',
        padding: 14,
      }}
    >
      <div style={{ fontWeight: 900, marginBottom: 6 }}>Чтобы начать анализ, создайте сравнение с конкурентами</div>
      <div style={{ color: 'var(--text-secondary)', fontSize: 13, marginBottom: 10, maxWidth: 880 }}>
        Для работы AI-модуля нужно сравнить вашу карточку с четырьмя конкурентами.
        Откройте сравнение карточек в кабинете WB, добавьте ваш товар и 4 товара конкурентов, затем нажмите “Готово”.
      </div>
      {errorText && (
        <div className="alert alert-danger" style={{ margin: '0 0 10px 0' }}>
          {errorText}
        </div>
      )}
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
        <button type="button" className="btn btn-primary btn-sm" onClick={onConfirmCreated} disabled={busy}>
          {busy ? 'Проверяю…' : 'Я создал сравнение'}
        </button>
        <button type="button" className="btn btn-outline-secondary btn-sm" onClick={onLater} disabled={busy}>
          Позже
        </button>
        <button type="button" className="btn btn-warning btn-sm" onClick={onCreateTechnicalTask} disabled={busy}>
          Запросить обновление отчёта (требует подтверждения)
        </button>
      </div>
    </div>
  );
}

function ActionsLogModal({ open, onClose }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const data = await api.getAiCompetitorReportActions(50);
      setItems(Array.isArray(data?.items) ? data.items : []);
    } catch (e) {
      setItems([]);
      setError(e?.message || 'Не удалось загрузить журнал');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!open) return;
    load();
  }, [open, load]);

  const rows = Array.isArray(items) ? items : [];

  return (
    <ModalShell
      open={open}
      title="Журнал обновлений отчёта"
      onClose={onClose}
      footer={(
        <>
          <button type="button" className="btn btn-outline-secondary" onClick={onClose}>Закрыть</button>
          <button type="button" className="btn btn-outline-secondary" onClick={load} disabled={loading}>
            {loading ? 'Обновляю…' : 'Обновить'}
          </button>
        </>
      )}
    >
      <div style={{ color: 'var(--text-secondary)', fontSize: 13, marginBottom: 10 }}>
        Здесь видно результат последней попытки обновить отчёт сравнения (ok/error) и текст ошибки, если она была.
      </div>
      {error && <div className="alert alert-danger" style={{ marginTop: 0 }}>{error}</div>}
      {loading ? (
        <div style={{ color: 'var(--text-tertiary)' }}>Загрузка…</div>
      ) : rows.length === 0 ? (
        <div style={{ color: 'var(--text-tertiary)' }}>Пока нет записей</div>
      ) : (
        <div className="table-wrapper" style={{ marginTop: 0 }}>
          <table className="custom-table">
            <thead>
              <tr>
                <th style={{ width: 200 }}>Время</th>
                <th style={{ width: 120 }}>Действие</th>
                <th style={{ width: 120 }}>Результат</th>
                <th>Сообщение</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((x) => {
                const res = String(x?.result || '');
                const isOk = res === 'ok';
                const isErr = res === 'error';
                return (
                  <tr key={x.id}>
                    <td style={{ fontSize: 12, color: 'var(--text-tertiary)', whiteSpace: 'nowrap' }}>
                      {x.requested_at ? String(x.requested_at).replace('T', ' ').slice(0, 19) : '—'}
                    </td>
                    <td style={{ fontWeight: 800 }}>{x.action || '—'}</td>
                    <td>
                      <span
                        style={{
                          display: 'inline-flex',
                          alignItems: 'center',
                          padding: '3px 8px',
                          borderRadius: 999,
                          fontSize: 12,
                          fontWeight: 800,
                          background: isOk ? 'rgba(16,185,129,0.12)' : isErr ? 'rgba(239,68,68,0.10)' : 'rgba(0,0,0,0.06)',
                          color: isOk ? '#047857' : isErr ? '#b91c1c' : 'var(--text-secondary)',
                          border: '1px solid rgba(0,0,0,0.06)',
                        }}
                      >
                        {res || '—'}
                      </span>
                    </td>
                    <td style={{ fontSize: 13, color: isErr ? '#b91c1c' : 'var(--text-secondary)' }}>
                      {x.error_message || '—'}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </ModalShell>
  );
}

function TasksTab({ selectedNmId, onGrantAccess }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busyId, setBusyId] = useState('');
  const [openItem, setOpenItem] = useState(null);
  const [archiveOpen, setArchiveOpen] = useState(false);

  const reload = async () => {
    setLoading(true);
    setError('');
    try {
      const fetchTasks = async () => {
        const data = await api.getAiTasks();
        setItems(Array.isArray(data?.items) ? data.items : []);
      };

      // Best-effort: auto-sync unanswered reviews so the daily approval task appears
      // without manual actions. IMPORTANT: never block rendering tasks list on this call.
      // We do a background refresh after sync completes (also best-effort).
      api
        .syncAiReviewReplies({ take: 20 })
        .then(fetchTasks)
        .catch(() => {});

      await fetchTasks();
    } catch (e) {
      setError(e?.message || 'Ошибка загрузки');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    reload();
  }, []);

  const visibleItems = useMemo(() => {
    const list = Array.isArray(items) ? items : [];
    const sel = selectedNmId == null ? null : Number(selectedNmId);
    return list.filter((t) => {
      const nm = t?.nm_id == null ? null : Number(t.nm_id);
      if (sel == null) return nm == null; // until product selected show only global tasks
      return nm == null || nm === sel;
    });
  }, [items, selectedNmId]);

  const sorted = useMemo(() => {
    const list = Array.isArray(visibleItems) ? visibleItems.slice() : [];
    list.sort((a, b) => String(b?.created_at || '').localeCompare(String(a?.created_at || '')));
    return list;
  }, [visibleItems]);

  const { openItems, archivedItems } = useMemo(() => {
    const list = Array.isArray(sorted) ? sorted : [];
    const open = [];
    const archived = [];
    for (const t of list) {
      const st = String(t?.status || '').toLowerCase();
      if (st === 'completed' || st === 'cancelled') archived.push(t);
      else open.push(t);
    }
    return { openItems: open, archivedItems: archived };
  }, [sorted]);

  const setStatus = async (taskId, status) => {
    setBusyId(taskId);
    try {
      await api.updateAiTaskStatus(taskId, status);
      await reload();
    } catch (e) {
      setError(e?.message || 'Ошибка');
    } finally {
      setBusyId('');
    }
  };

  return (
    <DataTable title="Задачи" tag="ИИ модуль">
      {loading ? (
        <div style={{ padding: 12, color: 'var(--text-tertiary)' }}>Загрузка…</div>
      ) : error ? (
        <div className="alert alert-danger" style={{ margin: 12 }}>{error}</div>
      ) : openItems.length === 0 && archivedItems.length === 0 ? (
        <div style={{ padding: 12, color: 'var(--text-tertiary)' }}>Пока нет задач</div>
      ) : (
        <div style={{ display: 'grid', gap: 10, padding: 12 }}>
          {archivedItems.length > 0 && (
            <div style={{ ...softCardStyle(), padding: 12 }}>
              <button
                type="button"
                className="btn btn-sm btn-outline-secondary"
                onClick={() => setArchiveOpen((v) => !v)}
                style={{ display: 'inline-flex', alignItems: 'center', gap: 10 }}
              >
                <span style={{ fontWeight: 900 }}>Архив</span>
                <span style={{ color: 'var(--text-tertiary)', fontWeight: 800 }}>({archivedItems.length})</span>
                <span style={{ marginLeft: 6, color: 'var(--text-tertiary)' }}>
                  {archiveOpen ? 'Свернуть' : 'Развернуть'}
                </span>
              </button>

              {archiveOpen && (
                <div style={{ display: 'grid', gap: 10, marginTop: 12 }}>
                  {archivedItems.map((t) => (
                    <button
                      key={t.id}
                      type="button"
                      className="btn"
                      onClick={() => setOpenItem({
                        kind: 'task',
                        data: t,
                        onSetStatus: (st) => setStatus(t.id, st),
                      })}
                      style={{
                        ...softCardStyle(),
                        padding: 12,
                        textAlign: 'left',
                        display: 'grid',
                        gap: 6,
                        cursor: 'pointer',
                        opacity: 0.92,
                      }}
                    >
                      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                        <div style={{ fontWeight: 900, fontSize: 13, color: 'var(--text-primary)' }}>{t.title}</div>
                        <div style={{ marginLeft: 'auto' }}>{statusBadge(t.status)}</div>
                      </div>
                      {t.description && (
                        <div style={{ fontSize: 12, color: 'var(--text-secondary)', whiteSpace: 'pre-wrap', lineHeight: 1.45 }}>
                          {t.description}
                        </div>
                      )}
                      {t.reason && (
                        <div style={{ fontSize: 12, color: 'var(--text-secondary)', whiteSpace: 'pre-wrap', lineHeight: 1.45 }}>
                          {t.reason}
                        </div>
                      )}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}

          {openItems.map((t) => (
            <button
              key={t.id}
              type="button"
              className="btn"
              onClick={() => setOpenItem({
                kind: 'task',
                data: t,
                onSetStatus: (st) => setStatus(t.id, st),
              })}
              style={{
                ...softCardStyle(),
                padding: 12,
                textAlign: 'left',
                display: 'grid',
                gap: 6,
                cursor: 'pointer',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                <div style={{ fontWeight: 900, fontSize: 13, color: 'var(--text-primary)' }}>{t.title}</div>
                <div style={{ marginLeft: 'auto' }}>{statusBadge(t.status)}</div>
              </div>
              {t.description && (
                <div style={{ fontSize: 12, color: 'var(--text-secondary)', whiteSpace: 'pre-wrap', lineHeight: 1.45 }}>
                  {t.description}
                </div>
              )}
              {t.reason && (
                <div style={{ fontSize: 12, color: 'var(--text-secondary)', whiteSpace: 'pre-wrap', lineHeight: 1.45 }}>
                  {t.reason}
                </div>
              )}
            </button>
          ))}
        </div>
      )}
      <AiItemDetailsModal
        openItem={openItem}
        onClose={() => setOpenItem(null)}
        primaryActionLabel={openItem?.kind === 'task' && String(openItem?.data?.task_type || '') === 'wb_access_grant' ? 'Выдать доступ' : ''}
        onPrimaryAction={() => {
          if (openItem?.kind === 'task' && String(openItem?.data?.task_type || '') === 'wb_access_grant') {
            onGrantAccess?.();
          }
        }}
        busy={Boolean(busyId)}
      />
    </DataTable>
  );
}

const COMPETITOR_METRIC_LABELS = {
  ctr: 'CTR (% п.п.; в ячейке как в WB, доля 0–1 → ×100)',
  traffic: 'Показы (абсолют)',
  funnel_cart: 'Конверсия в корзину (% п.п., в Excel без «%»)',
  funnel_order: 'Конверсия в заказ (% п.п., в Excel без «%»)',
};

function formatMetricCell(v) {
  if (v == null || v === '') return '—';
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v);
  if (Math.abs(n - Math.round(n)) < 1e-6) return String(Math.round(n));
  return n.toFixed(2);
}

function HypothesesTab({ selectedNmId }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busyId, setBusyId] = useState('');
  const [resultSummary, setResultSummary] = useState({});
  const [openItem, setOpenItem] = useState(null);
  const [sourceModalOpen, setSourceModalOpen] = useState(false);
  const [sourceLoading, setSourceLoading] = useState(false);
  const [sourceError, setSourceError] = useState('');
  const [sourceDetail, setSourceDetail] = useState(null);

  useEffect(() => {
    if (!sourceModalOpen) return undefined;
    let cancelled = false;
    (async () => {
      setSourceLoading(true);
      setSourceError('');
      setSourceDetail(null);
      try {
        const st = await api.getAiCompetitorReportStatus('week');
        const rid = st?.report_id;
        if (!rid) {
          throw new Error('Отчёт сравнения ещё не загружен. Сначала получите выгрузку из кабинета WB.');
        }
        const detail = await api.getAiCompetitorReportDetail(rid);
        if (!cancelled) setSourceDetail(detail);
      } catch (e) {
        if (!cancelled) setSourceError(e?.message || 'Ошибка загрузки');
      } finally {
        if (!cancelled) setSourceLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sourceModalOpen]);

  const sourceRows = useMemo(() => {
    const metrics = Array.isArray(sourceDetail?.metrics) ? sourceDetail.metrics : [];
    const sel = selectedNmId == null ? null : Number(selectedNmId);
    const filtered = sel == null ? metrics : metrics.filter((m) => Number(m?.nm_id) === sel);
    const copy = filtered.slice();
    copy.sort((a, b) => Number(a?.nm_id) - Number(b?.nm_id) || String(a?.metric_code).localeCompare(String(b?.metric_code)));
    return copy;
  }, [sourceDetail, selectedNmId]);

  const reload = async () => {
    setLoading(true);
    setError('');
    try {
      const data = await api.getAiHypotheses();
      setItems(Array.isArray(data?.items) ? data.items : []);
    } catch (e) {
      setError(e?.message || 'Ошибка загрузки');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    reload();
  }, []);

  const visibleItems = useMemo(() => {
    const list = Array.isArray(items) ? items : [];
    const sel = selectedNmId == null ? null : Number(selectedNmId);
    if (sel == null) return [];
    return list.filter((h) => Number(h?.nm_id) === sel);
  }, [items, selectedNmId]);

  const sorted = useMemo(() => {
    const list = Array.isArray(visibleItems) ? visibleItems.slice() : [];
    list.sort((a, b) => String(b?.created_at || '').localeCompare(String(a?.created_at || '')));
    return list;
  }, [visibleItems]);

  const start = async (id) => {
    setBusyId(id);
    try {
      await api.startAiHypothesis(id);
      await reload();
    } catch (e) {
      setError(e?.message || 'Ошибка');
    } finally {
      setBusyId('');
    }
  };

  const finish = async (id) => {
    setBusyId(id);
    try {
      await api.finishAiHypothesis(id, resultSummary[id] || null);
      await reload();
    } catch (e) {
      setError(e?.message || 'Ошибка');
    } finally {
      setBusyId('');
    }
  };

  return (
    <DataTable
      title="Гипотезы"
      tag="ИИ модуль"
      headRight={(
        <button
          type="button"
          className="btn btn-outline-secondary btn-sm"
          onClick={() => setSourceModalOpen(true)}
        >
          Данные сравнения (Excel)
        </button>
      )}
    >
      <ModalShell
        open={sourceModalOpen}
        title="Последний импорт: сравнение с конкурентами"
        width="min(960px, 100%)"
        onClose={() => setSourceModalOpen(false)}
      >
        {sourceLoading ? (
          <div style={{ padding: 8, color: 'var(--text-tertiary)' }}>Загрузка…</div>
        ) : sourceError ? (
          <div className="alert alert-danger" style={{ margin: 0 }}>{sourceError}</div>
        ) : sourceDetail ? (
          <div style={{ display: 'grid', gap: 14 }}>
            <div style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.5 }}>
              Данные из последней выгрузки Excel «Сравнение карточек» WB
              (батч <code style={{ fontSize: 12 }}>{String(sourceDetail?.report?.latest_import_batch_id || '').slice(0, 8)}…</code>
              {sourceDetail?.report?.report_date ? `, дата отчёта ${sourceDetail.report.report_date}` : ''}
              {sourceDetail?.report?.period ? `, период ${sourceDetail.report.period}` : ''}).
              {' '}
              <strong>Трафик («Показы»)</strong> — абсолют; по другим карточкам в сравнении считается <strong>среднее</strong> показов.
              {' '}
              <strong>CTR</strong> — строка ровно «CTR»; в БД храним процентные пункты: если в ячейке доля от 0 до 1 (не включая границы 0 и 1), умножаем на 100, иначе берём число как уже п.п.
              {' '}
              <strong>Конверсии</strong> — строки «Конверсия в корзину, %» и «Конверсия в заказ, %»: в ячейке без «%», смысл — процентные пункты.
              По конкурентам для CTR и конверсий — <strong>медиана</strong>. Нули у конкурентов не участвуют.
              В сравнении одна из карточек — ваш товар: значения по колонке артикула — «наши».
              {' '}
              <strong>Логистика и затраты</strong> из этого файла не берутся — они считаются из наших финансовых данных (например <code>sku_daily</code>).
            </div>
            {selectedNmId == null ? (
              <div className="alert alert-warning" style={{ margin: 0, fontSize: 13 }}>
                Товар не выбран — показаны все артикулы из последнего импорта. Выберите товар сверху, чтобы сузить таблицу.
              </div>
            ) : null}
            <div style={{ overflow: 'auto', maxHeight: 'min(50vh, 420px)', border: '1px solid rgba(2,6,23,0.08)', borderRadius: 8 }}>
              <table className="table table-sm table-striped" style={{ margin: 0, fontSize: 12, whiteSpace: 'nowrap' }}>
                <thead>
                  <tr>
                    <th style={{ position: 'sticky', top: 0, background: 'var(--bg-secondary)' }}>nm_id</th>
                    <th style={{ position: 'sticky', top: 0, background: 'var(--bg-secondary)' }}>Показатель</th>
                    <th style={{ position: 'sticky', top: 0, background: 'var(--bg-secondary)' }}>Наши</th>
                    <th style={{ position: 'sticky', top: 0, background: 'var(--bg-secondary)' }}>Медиана конкурентов</th>
                    <th style={{ position: 'sticky', top: 0, background: 'var(--bg-secondary)' }}>Ед.</th>
                  </tr>
                </thead>
                <tbody>
                  {sourceRows.length === 0 ? (
                    <tr>
                      <td colSpan={5} style={{ padding: 12, color: 'var(--text-tertiary)' }}>
                        Нет строк для выбранного артикула в последнем батче импорта.
                      </td>
                    </tr>
                  ) : (
                    sourceRows.map((m) => (
                      <tr key={`${m.nm_id}-${m.metric_code}-${m.import_batch_id}`}>
                        <td>{m.nm_id}</td>
                        <td>
                          {COMPETITOR_METRIC_LABELS[m.metric_code] || m.metric_code}
                        </td>
                        <td>{formatMetricCell(m.our_value)}</td>
                        <td>{formatMetricCell(m.competitor_median_value)}</td>
                        <td>{m.unit || '—'}</td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
            <details>
              <summary style={{ cursor: 'pointer', fontWeight: 700, fontSize: 13 }}>
                Служебный JSON импорта (raw_payload)
              </summary>
              <pre
                style={{
                  marginTop: 10,
                  maxHeight: 220,
                  overflow: 'auto',
                  fontSize: 11,
                  padding: 10,
                  background: 'rgba(2,6,23,0.04)',
                  borderRadius: 8,
                  border: '1px solid rgba(2,6,23,0.08)',
                }}
              >
                {JSON.stringify(sourceDetail.raw_payload ?? null, null, 2)}
              </pre>
            </details>
          </div>
        ) : null}
      </ModalShell>
      {loading ? (
        <div style={{ padding: 12, color: 'var(--text-tertiary)' }}>Загрузка…</div>
      ) : error ? (
        <div className="alert alert-danger" style={{ margin: 12 }}>{error}</div>
      ) : sorted.length === 0 ? (
        <div style={{ padding: 12, color: 'var(--text-tertiary)' }}>Пока нет гипотез</div>
      ) : (
        <div style={{ display: 'grid', gap: 10, padding: 12 }}>
          {sorted.map((h) => {
            const actions = [];
            if (h.status === 'draft') {
              actions.push({
                key: 'start',
                label: 'Запустить',
                className: 'btn btn-primary',
                onClick: () => start(h.id),
                disabled: false,
              });
            }
            if (h.status === 'running') {
              actions.push({
                key: 'finish',
                label: 'Завершить',
                className: 'btn btn-primary',
                onClick: () => finish(h.id),
                disabled: false,
              });
            }

            return (
              <button
                key={h.id}
                type="button"
                className="btn"
                onClick={() => setOpenItem({
                  kind: 'hypothesis',
                  data: h,
                  actions,
                })}
                style={{
                  ...softCardStyle(),
                  padding: 12,
                  textAlign: 'left',
                  display: 'grid',
                  gap: 6,
                  cursor: 'pointer',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                  <div style={{ fontWeight: 900, fontSize: 13, color: 'var(--text-primary)' }}>{h.title}</div>
                  <div style={{ marginLeft: 'auto' }}>{statusBadge(h.status)}</div>
                </div>
                {h.description && (
                  <div style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.45 }}>
                    {h.description}
                  </div>
                )}
                {h.trigger_reason && (
                  <div style={{ fontSize: 12, color: 'var(--text-secondary)', whiteSpace: 'pre-wrap', lineHeight: 1.45 }}>
                    {h.trigger_reason}
                  </div>
                )}
                {h.status === 'running' && (
                  <div style={{ marginTop: 4 }} onClick={(e) => e.stopPropagation()}>
                    <input
                      className="form-control form-control-sm"
                      style={{ width: '100%', maxWidth: 320 }}
                      value={resultSummary[h.id] || ''}
                      placeholder="Коротко: что сделали и какой эффект"
                      onChange={(e) => setResultSummary((m) => ({ ...m, [h.id]: e.target.value }))}
                    />
                  </div>
                )}
              </button>
            );
          })}
        </div>
      )}
      <AiItemDetailsModal
        openItem={openItem}
        onClose={() => setOpenItem(null)}
        busy={Boolean(busyId)}
      />
    </DataTable>
  );
}

export default function AiModule() {
  const [selectedNmId, setSelectedNmId] = useState(() => {
    const v = (lsGet(LS_SELECTED_NM_ID) || '').trim();
    const n = Number(v);
    return Number.isFinite(n) && n > 0 ? n : null;
  });
  const [pickerOpen, setPickerOpen] = useState(false);
  const [wbModalOpen, setWbModalOpen] = useState(false);
  const [onboardingConfirmed, setOnboardingConfirmed] = useState(() => (lsGet(LS_ONBOARDING_CONFIRMED) || '') === '1');

  const [_credsStatus, setCredsStatus] = useState(null);
  const [remoteStatus, setRemoteStatus] = useState(null);
  const [accessStatus, setAccessStatus] = useState(null);
  const [reportStatus, setReportStatus] = useState(null);
  const [comparisonBusy, setComparisonBusy] = useState(false);
  const [comparisonError, setComparisonError] = useState('');
  const [actionsOpen, setActionsOpen] = useState(false);

  const loadReport = useCallback(async () => {
    setComparisonError('');
    try {
      const st = await api.getAiCompetitorReportStatus('week');
      setReportStatus(st);
    } catch (e) {
      setComparisonError(e?.message || 'Ошибка загрузки статуса');
    }
  }, []);

  const loadCreds = useCallback(async () => {
    try {
      const st = await api.getAiWbCredentialsStatus();
      setCredsStatus(st);
    } catch {
      // ignore; screen still works
    }
  }, []);

  const loadRemoteStatus = useCallback(async () => {
    try {
      const st = await api.getAiWbRemoteAuthStatus();
      setRemoteStatus(st);
    } catch {
      // ignore; fallback to creds status only
    }
  }, []);

  const loadAccessStatus = useCallback(async () => {
    try {
      const st = await api.getAiWbAccessStatus();
      setAccessStatus(st);
    } catch {
      // ignore; screen still works
    }
  }, []);

  useEffect(() => {
    loadReport();
    loadCreds();
    loadRemoteStatus();
    loadAccessStatus();
  }, [loadReport, loadCreds, loadRemoteStatus, loadAccessStatus]);

  useEffect(() => {
    // If product is missing, onboarding can't be confirmed.
    if (!selectedNmId && onboardingConfirmed) {
      setOnboardingConfirmed(false);
      lsSet(LS_ONBOARDING_CONFIRMED, '');
    }
  }, [selectedNmId, onboardingConfirmed]);

  const calloutHidden = useMemo(() => (lsGet(LS_HIDE_COMPARISON_CALLOUT) || '') === '1', []);
  const showComparisonCallout = useMemo(() => {
    if (!selectedNmId) return false;
    if (calloutHidden) return false;
    const st = (reportStatus?.status || '').toLowerCase();
    return st === 'missing' || st === 'stale';
  }, [selectedNmId, reportStatus, calloutHidden]);

  const remoteSessionActive = useMemo(() => Boolean(remoteStatus?.active), [remoteStatus]);
  const hasSavedAccess = useMemo(() => {
    if (!accessStatus?.has_storage_state) return false;
    if (accessStatus?.reconnect_required) return false;
    return true;
  }, [accessStatus]);
  const needsWbAccess = useMemo(() => {
    // Blocking rule: only block when access is not saved and remote session is not active.
    // Credentials presence is not a reliable signal (storage_state is).
    if (hasSavedAccess) return false;
    if (remoteSessionActive) return false;
    return true;
  }, [remoteSessionActive, hasSavedAccess]);
  const onboardingStep = useMemo(() => {
    if (!onboardingConfirmed) return 1;
    if (needsWbAccess) return 2;
    return 0;
  }, [onboardingConfirmed, needsWbAccess]);
  const onboardingDone = onboardingStep === 0;

  const onConfirmCreated = async () => {
    setComparisonBusy(true);
    setComparisonError('');
    try {
      const st = await api.getAiCompetitorReportStatus('week');
      setReportStatus(st);
      const statusTxt = (st?.status || '').toLowerCase();
      if (statusTxt === 'missing') {
        setComparisonError('Отчёт пока не найден. Проверьте, что вы добавили ваш товар и 4 конкурента в сравнение, затем попробуйте ещё раз.');
      }
    } catch (e) {
      setComparisonError(e?.message || 'Не удалось проверить отчёт');
    } finally {
      setComparisonBusy(false);
    }
  };

  const onLater = () => {
    lsSet(LS_HIDE_COMPARISON_CALLOUT, '1');
    setComparisonError('');
    // force rerender for memoized flag
    setReportStatus((x) => ({ ...(x || {}) }));
  };

  const onCreateTechnicalTask = async () => {
    setComparisonBusy(true);
    setComparisonError('');
    try {
      await api.requestAiCompetitorReportRefresh('week');
      await loadReport();
    } catch (e) {
      setComparisonError(e?.message || 'Не удалось создать задачу');
    } finally {
      setComparisonBusy(false);
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <FirstRunBanner
        step={onboardingStep || null}
        selectedNmId={selectedNmId}
        needsWbAccess={needsWbAccess}
        onPickProduct={() => setPickerOpen(true)}
        onConfirmProduct={() => {
          setOnboardingConfirmed(true);
          lsSet(LS_ONBOARDING_CONFIRMED, '1');
          setComparisonError('');
        }}
        onGrantAccess={() => setWbModalOpen(true)}
        busy={comparisonBusy}
        errorText={comparisonError}
      />

      <ProductGenerationAdminCard />

      {onboardingDone && (
        <>
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
            <div style={{ fontWeight: 900, fontSize: 16 }}>Задачи и гипотезы</div>
            <button type="button" className="btn btn-outline-secondary btn-sm" onClick={() => setPickerOpen(true)}>
              Сменить товар
            </button>
            <button type="button" className="btn btn-outline-secondary btn-sm" onClick={() => setActionsOpen(true)} style={{ marginLeft: 'auto' }}>
              Журнал обновлений
            </button>
          </div>

          <ComparisonCallout
            visible={showComparisonCallout}
            onConfirmCreated={onConfirmCreated}
            onLater={onLater}
            onCreateTechnicalTask={onCreateTechnicalTask}
            busy={comparisonBusy}
            errorText={comparisonError}
          />

          <div style={{ display: 'grid', gap: 12 }}>
            <TasksTab selectedNmId={selectedNmId} onGrantAccess={() => setWbModalOpen(true)} />
            <HypothesesTab selectedNmId={selectedNmId} />
          </div>
        </>
      )}

      <ProductPickerModal
        open={pickerOpen}
        onClose={() => setPickerOpen(false)}
        onSelectNmId={(nm) => {
          setSelectedNmId(nm);
          lsSet(LS_SELECTED_NM_ID, String(nm));
          lsSet(LS_HIDE_COMPARISON_CALLOUT, '');
          setOnboardingConfirmed(false);
          lsSet(LS_ONBOARDING_CONFIRMED, '');
          setComparisonError('');
        }}
      />
      <WbAccessModal
        open={wbModalOpen}
        onClose={() => setWbModalOpen(false)}
        onGranted={() => {
          // Optimistic: hide onboarding immediately after successful save/upload,
          // then refresh status from server.
          setCredsStatus({ status: 'ok' });
          setAccessStatus({ status: 'ok', has_storage_state: true, reconnect_required: false });
          loadCreds();
          loadRemoteStatus();
          loadAccessStatus();
          setComparisonError('');
        }}
      />
      <ActionsLogModal
        open={actionsOpen}
        onClose={() => setActionsOpen(false)}
      />
    </div>
  );
}

