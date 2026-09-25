/* eslint react-hooks/set-state-in-effect: off */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import * as api from '../api';
import { isStaleStoreResponse, useActiveStoreId, useResetOnStoreChange } from '../storeDataGuard';

const EXAMPLE_QUESTIONS = [
  'Почему упала маржа на прошлой неделе?',
  'Что оферта говорит о штрафах за недовложение?',
  'Какие сейчас тарифы логистики WB?',
];

// ── Крохотный безопасный рендерер: HTML экранируем ПЕРВЫМ делом, поддерживаем
// **жирный текст**, маркированные списки ("- пункт") и ссылки (markdown- и голые URL). ──

function escapeHtml(raw) {
  return String(raw ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function inlineFormat(escaped) {
  let out = escaped.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  out = out.replace(
    /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
    '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>',
  );
  out = out.replace(
    /(^|[\s(])(https?:\/\/[^\s<]+)/g,
    '$1<a href="$2" target="_blank" rel="noopener noreferrer">$2</a>',
  );
  return out;
}

// ── Разбор построчно: маркированные ("- п." / "* п.") и нумерованные ("1. п.") списки,
// горизонтальные линии ("---") как <hr/>, остальное — абзацы. AI CFO активно использует
// все эти элементы (см. cfo_audit_service.build_cfo_audit_prompt), поэтому рендерер должен
// понимать их, а не показывать сырые дефисы/цифры. ──

function renderAssistantMarkup(raw) {
  const escaped = escapeHtml(raw);
  const lines = escaped.split('\n');
  const html = [];
  let listType = null; // 'ul' | 'ol' | null

  const closeList = () => {
    if (listType) {
      html.push(`</${listType}>`);
      listType = null;
    }
  };

  for (const line of lines) {
    const trimmed = line.trim();

    if (/^-{3,}$/.test(trimmed)) {
      closeList();
      html.push('<hr/>');
      continue;
    }

    const bulletMatch = /^[-*]\s+(.*)$/.exec(trimmed);
    if (bulletMatch) {
      if (listType !== 'ul') {
        closeList();
        html.push('<ul>');
        listType = 'ul';
      }
      html.push(`<li>${inlineFormat(bulletMatch[1])}</li>`);
      continue;
    }

    const numberedMatch = /^\d+[.)]\s+(.*)$/.exec(trimmed);
    if (numberedMatch) {
      if (listType !== 'ol') {
        closeList();
        html.push('<ol>');
        listType = 'ol';
      }
      html.push(`<li>${inlineFormat(numberedMatch[1])}</li>`);
      continue;
    }

    closeList();
    html.push(trimmed === '' ? '<br/>' : `<div>${inlineFormat(line)}</div>`);
  }
  closeList();
  return html.join('');
}

function oneLineSnippet(text, maxLen = 160) {
  const flat = String(text || '').replace(/\s+/g, ' ').trim();
  return flat.length > maxLen ? `${flat.slice(0, maxLen)}…` : flat;
}

function formatTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleTimeString('ru', { hour: '2-digit', minute: '2-digit' });
}

function MessageSources({ sources }) {
  if (!Array.isArray(sources) || sources.length === 0) return null;
  const offerSources = sources.filter((s) => s?.type === 'offer' && s.text);
  const webSources = sources.filter((s) => s?.type === 'web' && s.url);
  if (offerSources.length === 0 && webSources.length === 0) return null;
  return (
    <div className="ai-chat-sources">
      {offerSources.length > 0 && (
        <div className="ai-chat-source-line">
          Оферта: {offerSources.map((s) => oneLineSnippet(s.text)).join(' · ')}
        </div>
      )}
      {webSources.length > 0 && (
        <div className="ai-chat-source-line">
          Источники:{' '}
          {webSources.map((s, idx) => (
            <span key={`${s.url}-${idx}`}>
              {idx > 0 ? ', ' : ''}
              <a href={s.url} target="_blank" rel="noopener noreferrer">
                {s.title || s.url}
              </a>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function ChatMessage({ message }) {
  const isUser = message.role === 'user';
  return (
    <div className={`ai-chat-row ${isUser ? 'ai-chat-row-user' : 'ai-chat-row-assistant'}`}>
      <div className={`ai-chat-bubble ${isUser ? 'ai-chat-bubble-user' : 'ai-chat-bubble-assistant'}`}>
        {!isUser && message.kind === 'cfo' && <div className="ai-chat-cfo-label">Анализ AI CFO</div>}
        {isUser ? (
          <div className="ai-chat-text">{message.content}</div>
        ) : (
          <div className="ai-chat-text" dangerouslySetInnerHTML={{ __html: renderAssistantMarkup(message.content) }} />
        )}
        <MessageSources sources={message.sources} />
        {message.created_at && <div className="ai-chat-time">{formatTime(message.created_at)}</div>}
      </div>
    </div>
  );
}

function TypingIndicator({ label }) {
  return (
    <div className="ai-chat-row ai-chat-row-assistant">
      <div className="ai-chat-bubble ai-chat-bubble-assistant ai-chat-typing">
        {label && <span className="ai-chat-typing-label">{label}</span>}
        <span className="ai-chat-typing-dot" />
        <span className="ai-chat-typing-dot" />
        <span className="ai-chat-typing-dot" />
      </div>
    </div>
  );
}

function formatShortDate(iso) {
  if (!iso) return '';
  const parts = String(iso).split('-');
  if (parts.length !== 3) return '';
  const [, m, d] = parts;
  return `${d}.${m}`;
}

export default function AiChat({ range }) {
  const storeId = useActiveStoreId();

  const [messages, setMessages] = useState([]);
  const [loadingHistory, setLoadingHistory] = useState(true);
  const [historyError, setHistoryError] = useState('');

  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState('');

  const [cfoLoading, setCfoLoading] = useState(false);
  const [cfoError, setCfoError] = useState('');

  const listEndRef = useRef(null);
  const textareaRef = useRef(null);

  const resetForStore = useCallback(() => {
    setMessages([]);
    setLoadingHistory(true);
    setHistoryError('');
    setInput('');
    setSending(false);
    setSendError('');
    setCfoLoading(false);
    setCfoError('');
  }, []);

  useResetOnStoreChange(storeId, resetForStore);

  useEffect(() => {
    const reqStore = storeId;
    setLoadingHistory(true);
    setHistoryError('');
    api
      .getAssistantHistory()
      .then((data) => {
        if (isStaleStoreResponse(reqStore, storeId)) return;
        setMessages(Array.isArray(data?.messages) ? data.messages : []);
      })
      .catch((e) => {
        if (isStaleStoreResponse(reqStore, storeId)) return;
        setHistoryError(e?.message || 'Не удалось загрузить историю чата');
      })
      .finally(() => {
        if (!isStaleStoreResponse(reqStore, storeId)) setLoadingHistory(false);
      });
  }, [storeId]);

  useEffect(() => {
    listEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [messages, sending, cfoLoading]);

  const isEmpty = useMemo(
    () => !loadingHistory && messages.length === 0 && !sending && !cfoLoading,
    [loadingHistory, messages.length, sending, cfoLoading],
  );

  const sendMessage = useCallback(
    async (text) => {
      const q = (text || '').trim();
      if (!q || sending) return;
      setSendError('');
      const optimisticUser = { id: `local-${Date.now()}`, role: 'user', content: q, created_at: new Date().toISOString(), kind: 'chat' };
      setMessages((prev) => [...prev, optimisticUser]);
      setInput('');
      setSending(true);
      try {
        const reply = await api.askAssistant(q);
        setMessages((prev) => [...prev, reply]);
      } catch (e) {
        setSendError(e?.message || 'Не удалось получить ответ');
      } finally {
        setSending(false);
      }
    },
    [sending],
  );

  const onSendClick = useCallback(() => {
    sendMessage(input);
  }, [sendMessage, input]);

  const onTextareaKeyDown = useCallback(
    (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage(input);
      }
    },
    [sendMessage, input],
  );

  const onExampleClick = useCallback(
    (q) => {
      sendMessage(q);
    },
    [sendMessage],
  );

  const onCfoAnalysis = useCallback(async () => {
    if (cfoLoading) return;
    setCfoError('');
    setCfoLoading(true);
    try {
      const reply = await api.runCfoAnalysis(range?.dateFrom, range?.dateTo);
      setMessages((prev) => [...prev, reply]);
    } catch (e) {
      setCfoError(e?.message || 'Не удалось получить анализ AI CFO');
    } finally {
      setCfoLoading(false);
    }
  }, [cfoLoading, range]);

  const periodLabel = range?.dateFrom && range?.dateTo
    ? `за ${formatShortDate(range.dateFrom)}–${formatShortDate(range.dateTo)}`
    : '';

  return (
    <div className="ai-chat-screen">
      <div className="ai-chat-header">
        <div className="ai-chat-header-title">AI CFO</div>
        <div className="ai-chat-header-cfo">
          <button type="button" className="btn-primary" onClick={onCfoAnalysis} disabled={cfoLoading}>
            {cfoLoading ? 'Анализирую…' : 'Анализ AI CFO'}
          </button>
          {periodLabel && <div className="ai-chat-header-cfo-period">{periodLabel}</div>}
        </div>
      </div>
      {cfoError && <div className="ai-chat-inline-error">{cfoError}</div>}

      <div className="ai-chat-messages">
        {loadingHistory && (
          <div className="loader-center" style={{ padding: '32px 0' }}>
            <div className="loader-spinner" />
          </div>
        )}

        {!loadingHistory && historyError && <div className="ai-chat-inline-error">{historyError}</div>}

        {isEmpty && (
          <div className="ai-chat-empty">
            <div className="ai-chat-empty-title">Задайте вопрос о своём магазине или оферте WB</div>
            <div className="ai-chat-empty-hint">
              Ассистент видит данные вашего P&amp;L, умеет искать по тексту оферты и, если нужно, ищет свежую информацию в интернете.
            </div>
            <div className="ai-chat-examples">
              {EXAMPLE_QUESTIONS.map((q) => (
                <button key={q} type="button" className="ai-chat-example-chip" onClick={() => onExampleClick(q)}>
                  {q}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((m) => (
          <ChatMessage key={m.id} message={m} />
        ))}

        {(sending || cfoLoading) && (
          <TypingIndicator label={cfoLoading ? 'AI CFO анализирует динамику по артикулам…' : undefined} />
        )}

        <div ref={listEndRef} />
      </div>

      {sendError && <div className="ai-chat-inline-error">{sendError}</div>}

      <div className="ai-chat-input-row">
        <textarea
          ref={textareaRef}
          className="ai-chat-textarea"
          placeholder="Спросите про прибыль, рекламу, SKU или оферту WB… (Enter — отправить, Shift+Enter — новая строка)"
          rows={2}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onTextareaKeyDown}
          disabled={sending}
        />
        <button
          type="button"
          className="ai-chat-send-btn"
          onClick={onSendClick}
          disabled={sending || !input.trim()}
          aria-label="Отправить"
          title="Отправить"
        >
          ➤
        </button>
      </div>
    </div>
  );
}
