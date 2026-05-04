/* eslint react-hooks/set-state-in-effect: off */
import { useEffect, useMemo, useRef, useState } from 'react';
import * as api from '../api';

const CHAT_ID_KEY = 'offer_ai_chat_id';

function StatusPill({ status, version }) {
  const cfg = (() => {
    if (status === 'ready') return { bg: 'rgba(20,184,166,0.12)', border: 'rgba(20,184,166,0.35)', color: '#0f766e', text: 'Готово' };
    if (status === 'indexing') return { bg: 'rgba(124,58,237,0.10)', border: 'rgba(124,58,237,0.25)', color: '#7c3aed', text: 'Индексация' };
    if (status === 'failed') return { bg: 'rgba(239,68,68,0.10)', border: 'rgba(239,68,68,0.25)', color: '#b91c1c', text: 'Ошибка' };
    return { bg: 'rgba(100,116,196,0.08)', border: 'rgba(100,116,196,0.20)', color: '#475569', text: 'Не загружено' };
  })();
  return (
    <div
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 8,
        padding: '6px 10px',
        borderRadius: 999,
        background: cfg.bg,
        border: `1px solid ${cfg.border}`,
        color: cfg.color,
        fontWeight: 700,
        whiteSpace: 'nowrap',
      }}
      title={version ? `Версия: ${version}` : undefined}
    >
      <span style={{ width: 8, height: 8, borderRadius: '50%', background: cfg.color, display: 'inline-block', opacity: 0.8 }} />
      {cfg.text}{version ? ` • ${version}` : ''}
    </div>
  );
}

export default function OfferAiBlock() {
  const [isAdmin, setIsAdmin] = useState(false);
  const [adminChecked, setAdminChecked] = useState(false);

  const [status, setStatus] = useState({ status: 'idle', active_version: null, indexed_at: null, error_message: null });
  const [statusLoading, setStatusLoading] = useState(false);
  const [statusError, setStatusError] = useState('');

  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState('');
  const fileRef = useRef(null);

  const [question, setQuestion] = useState('');
  const [asking, setAsking] = useState(false);
  const [askError, setAskError] = useState('');
  const [answer, setAnswer] = useState('');
  const [sources, setSources] = useState([]);
  const [showSources, setShowSources] = useState(false);

  const canAsk = useMemo(() => (status?.status === 'ready' && status?.active_version), [status]);

  const [chatId, setChatId] = useState(() => {
    try { return (localStorage.getItem(CHAT_ID_KEY) || '').trim(); } catch { return ''; }
  });
  const [history, setHistory] = useState([]);

  async function refreshStatus() {
    setStatusLoading(true);
    setStatusError('');
    try {
      const s = await api.getOfferAiStatus();
      setStatus(s || { status: 'idle' });
    } catch (e) {
      setStatusError(e?.message || 'Не удалось загрузить статус');
    } finally {
      setStatusLoading(false);
    }
  }

  useEffect(() => {
    // admin gate
    api.getMe()
      .then((me) => {
        setIsAdmin(Boolean(me?.is_admin));
        setAdminChecked(true);
      })
      .catch(() => setAdminChecked(true));
  }, []);

  useEffect(() => {
    if (!isAdmin) return;
    refreshStatus();
  }, [isAdmin]);

  // Polling while indexing
  useEffect(() => {
    if (status?.status !== 'indexing') return;
    const tid = setInterval(() => refreshStatus(), 4000);
    return () => clearInterval(tid);
  }, [status?.status]);

  async function loadChatHistory(cid) {
    const id = (cid || '').trim();
    if (!id) { setHistory([]); return; }
    try {
      const res = await api.getOfferAiChatHistory(id);
      setHistory(Array.isArray(res?.messages) ? res.messages : []);
    } catch {
      // ignore: if chat not found, just clear
      setHistory([]);
    }
  }

  // Restore history after refresh (when offer is ready)
  useEffect(() => {
    if (!isAdmin) return;
    if (!canAsk) return;
    if (!chatId) return;
    loadChatHistory(chatId);
  }, [isAdmin, canAsk, chatId]);

  async function onUploadClick() {
    setUploadError('');
    const file = fileRef.current?.files?.[0];
    if (!file) {
      setUploadError('Выберите файл (.pdf/.txt/.html)');
      return;
    }
    setUploading(true);
    try {
      await api.uploadOfferAiFile(file);
      await refreshStatus();
    } catch (e) {
      setUploadError(e?.message || 'Не удалось загрузить оферту');
    } finally {
      setUploading(false);
    }
  }

  async function ensureChat() {
    let id = (chatId || '').trim();
    if (!id) {
      id = (crypto?.randomUUID ? crypto.randomUUID() : String(Date.now()));
      setChatId(id);
      try { localStorage.setItem(CHAT_ID_KEY, id); } catch { /* ignore */ }
    }
    await api.startOfferAiChat(id);
    return id;
  }

  async function onNewChat() {
    setAskError('');
    setAnswer('');
    setSources([]);
    setShowSources(false);
    const id = (crypto?.randomUUID ? crypto.randomUUID() : String(Date.now()));
    setChatId(id);
    try { localStorage.setItem(CHAT_ID_KEY, id); } catch { /* ignore */ }
    setHistory([]);
    try { await api.startOfferAiChat(id); } catch (e) { setAskError(e?.message || 'Не удалось создать чат'); }
  }

  async function onAsk() {
    setAskError('');
    setAnswer('');
    setSources([]);
    setShowSources(false);
    const q = (question || '').trim();
    if (!q) {
      setAskError('Введите вопрос');
      return;
    }
    setAsking(true);
    try {
      const cid = await ensureChat();
      const res = await api.askOfferAiChat(cid, q);
      setAnswer(res?.answer || '');
      setSources(Array.isArray(res?.sources) ? res.sources : []);
      setShowSources(false);
      // append to history locally (UI snappy), then sync on next refresh
      setHistory((prev) => ([
        ...(Array.isArray(prev) ? prev : []),
        { role: 'user', content: q, created_at: new Date().toISOString() },
        { role: 'assistant', content: res?.answer || '', created_at: new Date().toISOString() },
      ]));
    } catch (e) {
      setAskError(e?.message || 'Не удалось получить ответ');
    } finally {
      setAsking(false);
    }
  }

  if (!adminChecked) return null;
  if (!isAdmin) return null;

  return (
    <div className="ai-card" style={{ flexDirection: 'column', alignItems: 'stretch' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <div className="ai-icon">✦</div>
          <div className="ai-body">
            <h3>AI по оферте WB</h3>
            <p>Загрузите оферту и задавайте вопросы. Ответы строятся только по тексту оферты.</p>
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <StatusPill status={status?.status} version={status?.active_version} />
          <button
            type="button"
            className="btn btn-sm btn-outline-secondary"
            onClick={refreshStatus}
            disabled={statusLoading}
            title="Обновить статус"
          >
            Обновить
          </button>
        </div>
      </div>

      {statusError && <div className="alert alert-danger" style={{ marginTop: 12 }}>{statusError}</div>}
      {status?.status === 'failed' && status?.error_message && (
        <div className="alert alert-danger" style={{ marginTop: 12 }}>
          Индексация завершилась ошибкой: {status.error_message}
        </div>
      )}

      <div style={{ marginTop: 14, display: 'grid', gridTemplateColumns: '1fr auto', gap: 10, alignItems: 'end' }}>
        <div>
          <label style={{ fontWeight: 700, fontSize: '0.85rem', marginBottom: 6, display: 'block' }}>Файл оферты (.pdf/.txt/.html)</label>
          <input ref={fileRef} type="file" className="form-control" accept=".pdf,.txt,.html" />
          {uploadError && <div style={{ color: 'var(--red)', marginTop: 6 }}>{uploadError}</div>}
        </div>
        <button type="button" className="btn btn-primary" onClick={onUploadClick} disabled={uploading}>
          {uploading ? 'Загрузка…' : 'Загрузить и проиндексировать'}
        </button>
      </div>

      <div style={{ marginTop: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
          <label style={{ fontWeight: 700, fontSize: '0.85rem', marginBottom: 6, display: 'block' }}>Чат</label>
          <button type="button" className="btn btn-sm btn-outline-secondary" onClick={onNewChat} disabled={!canAsk || asking}>
            Новый чат
          </button>
        </div>

        {history?.length > 0 && (
          <div style={{ marginBottom: 10, border: '1px solid rgba(2,6,23,0.08)', borderRadius: 12, padding: 10, background: '#fff' }}>
            <div style={{ fontSize: '0.8rem', color: 'var(--text-tertiary)', marginBottom: 8 }}>
              История ({history.length}): chat_id {chatId ? `${chatId.slice(0, 8)}…` : '—'}
            </div>
            <div style={{ display: 'grid', gap: 8 }}>
              {history.slice(-10).map((m, idx) => (
                <div key={idx} style={{ whiteSpace: 'pre-wrap' }}>
                  <b>{m.role === 'user' ? 'Вы' : 'AI'}:</b> {m.content}
                </div>
              ))}
            </div>
          </div>
        )}

        <label style={{ fontWeight: 700, fontSize: '0.85rem', marginBottom: 6, display: 'block' }}>Сообщение</label>
        <textarea
          className="form-control"
          rows={3}
          placeholder={canAsk ? 'Например: Какие условия возврата товара?' : 'Сначала проиндексируйте оферту'}
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          disabled={!canAsk || asking}
        />
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginTop: 10 }}>
          <button type="button" className="btn btn-outline-primary" onClick={onAsk} disabled={!canAsk || asking}>
            {asking ? 'Думаю…' : 'Спросить'}
          </button>
          {!canAsk && (
            <div style={{ color: 'var(--text-tertiary)', fontSize: '0.85rem' }}>
              Доступно после статуса <b>Готово</b>.
            </div>
          )}
          {askError && <div style={{ color: 'var(--red)' }}>{askError}</div>}
        </div>
      </div>

      {(answer || sources.length > 0) && (
        <div style={{ marginTop: 16 }}>
          <div style={{ fontWeight: 800, marginBottom: 8 }}>Ответ</div>
          <div style={{ whiteSpace: 'pre-wrap', background: 'rgba(2,6,23,0.03)', border: '1px solid rgba(2,6,23,0.08)', borderRadius: 12, padding: 12 }}>
            {answer || '—'}
          </div>

          {sources.length > 0 && (
            <div style={{ marginTop: 12 }}>
              <button type="button" className="btn btn-sm btn-outline-secondary" onClick={() => setShowSources((v) => !v)}>
                {showSources ? 'Скрыть основания' : 'Показать основания'}
              </button>
              {showSources && (
                <div style={{ display: 'grid', gap: 10, marginTop: 10 }}>
                  {sources.slice(0, 6).map((s, idx) => (
                    <div key={idx} style={{ border: '1px solid rgba(2,6,23,0.08)', borderRadius: 12, padding: 10, background: '#fff' }}>
                      <div style={{ fontSize: '0.8rem', color: 'var(--text-tertiary)', marginBottom: 6 }}>
                        chunk #{s.chunk_id} • score {Number(s.score || 0).toFixed(3)}
                      </div>
                      <div style={{ whiteSpace: 'pre-wrap' }}>{s.text}</div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

