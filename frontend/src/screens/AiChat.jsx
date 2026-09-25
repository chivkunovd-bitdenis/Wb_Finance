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

function renderAssistantMarkup(raw) {
  const escaped = escapeHtml(raw);
  const lines = escaped.split('\n');
  const html = [];
  let inList = false;
  for (const line of lines) {
    const trimmed = line.trim();
    const isListItem = /^[-*]\s+/.test(trimmed);
    if (isListItem) {
      if (!inList) {
        html.push('<ul>');
        inList = true;
      }
      html.push(`<li>${inlineFormat(trimmed.replace(/^[-*]\s+/, ''))}</li>`);
      continue;
    }
    if (inList) {
      html.push('</ul>');
      inList = false;
    }
    html.push(trimmed === '' ? '<br/>' : `<div>${inlineFormat(line)}</div>`);
  }
  if (inList) html.push('</ul>');
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

function TypingIndicator() {
  return (
    <div className="ai-chat-row ai-chat-row-assistant">
      <div className="ai-chat-bubble ai-chat-bubble-assistant ai-chat-typing">
        <span className="ai-chat-typing-dot" />
        <span className="ai-chat-typing-dot" />
        <span className="ai-chat-typing-dot" />
      </div>
    </div>
  );
}

export default function AiChat() {
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
      const reply = await api.runCfoAnalysis();
      setMessages((prev) => [...prev, reply]);
    } catch (e) {
      setCfoError(e?.message || 'Не удалось получить анализ AI CFO');
    } finally {
      setCfoLoading(false);
    }
  }, [cfoLoading]);

  return (
    <div className="ai-chat-screen">
      <div className="ai-chat-header">
        <div className="ai-chat-header-title">AI CFO</div>
        <button type="button" className="btn-primary" onClick={onCfoAnalysis} disabled={cfoLoading}>
          {cfoLoading ? 'Анализирую…' : 'Анализ AI CFO'}
        </button>
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

        {(sending || cfoLoading) && <TypingIndicator />}

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
