/**
 * DailyBriefBlock — ежедневная AI-оперативная сводка.
 *
 * Поведение:
 *   pending    → автоматически вызываем POST /generate (без кнопки), показываем inline-лоадер
 *   generating → inline shimmer-лоадер внутри карточки + polling каждые 5 сек
 *   ready      → текст сводки
 *   error      → сообщение об ошибке + кнопка "Попробовать снова"
 *
 * Догоняющий баннер: если пользователь был на странице когда сводка стала ready —
 * показывается sticky-плашка в правом нижнем углу.
 */
// компактный заглушечный блок без логики/запросов

function BriefShimmer() {
  return (
    <div
      style={{
        borderRadius: '10px',
        overflow: 'hidden',
        background: 'linear-gradient(90deg, #f0f2f8 25%, #e8eaf5 50%, #f0f2f8 75%)',
        backgroundSize: '800px 100%',
        animation: 'brief-shimmer 1.6s ease-in-out infinite',
        padding: '20px 16px',
        display: 'flex',
        flexDirection: 'column',
        gap: '10px',
        minHeight: '80px',
      }}
    >
      {/* Анимированные строки — имитируют будущий текст сводки */}
      <div style={{ height: '13px', borderRadius: '6px', background: 'rgba(100,116,196,0.15)', width: '60%' }} />
      <div style={{ height: '13px', borderRadius: '6px', background: 'rgba(100,116,196,0.1)', width: '85%' }} />
      <div style={{ height: '13px', borderRadius: '6px', background: 'rgba(100,116,196,0.1)', width: '75%' }} />
      <div style={{ height: '1px', background: 'rgba(100,116,196,0.1)', margin: '4px 0' }} />
      <div style={{ height: '13px', borderRadius: '6px', background: 'rgba(100,116,196,0.12)', width: '50%' }} />
      <div style={{ height: '13px', borderRadius: '6px', background: 'rgba(100,116,196,0.08)', width: '40%' }} />
      {/* Подпись снизу */}
      <div style={{
        marginTop: '6px',
        fontSize: '0.75rem',
        color: '#9b72cb',
        opacity: 0.7,
        letterSpacing: '0.02em',
        display: 'flex',
        alignItems: 'center',
        gap: '6px',
      }}>
        <span style={{ fontSize: '0.9rem' }}>✦</span>
        Нейросеть анализирует вчерашний день...
      </div>
      <style>{`
        @keyframes brief-shimmer {
          0%   { background-position: -400px 0; }
          100% { background-position: 400px 0; }
        }
      `}</style>
    </div>
  );
}

/** Догоняющий баннер «Свежая сводка готова» */
function BriefReadyBanner({ dateFor, onView, onDismiss }) {
  const formatDate = (isoDate) => {
    if (!isoDate) return '';
    const [y, m, d] = isoDate.split('-');
    return `${d}.${m}.${y}`;
  };
  return (
    <div
      style={{
        position: 'fixed',
        bottom: '24px',
        right: '24px',
        zIndex: 9999,
        background: 'linear-gradient(135deg, #4285f4, #9b72cb)',
        color: '#fff',
        borderRadius: '12px',
        padding: '14px 20px',
        boxShadow: '0 4px 20px rgba(66,133,244,0.35)',
        display: 'flex',
        alignItems: 'center',
        gap: '14px',
        maxWidth: '380px',
        animation: 'brief-banner-in 0.3s ease',
      }}
    >
      <span style={{ fontSize: '1.3rem' }}>✦</span>
      <div style={{ flex: 1 }}>
        <div style={{ fontWeight: 700, fontSize: '0.9rem' }}>
          Оперативная сводка за {formatDate(dateFor)} готова
        </div>
        <div style={{ fontSize: '0.78rem', opacity: 0.85, marginTop: '2px' }}>
          AI проанализировал вчерашний день
        </div>
      </div>
      <button
        onClick={onView}
        style={{
          background: 'rgba(255,255,255,0.2)',
          border: '1px solid rgba(255,255,255,0.4)',
          color: '#fff',
          borderRadius: '8px',
          padding: '6px 12px',
          cursor: 'pointer',
          fontWeight: 700,
          fontSize: '0.8rem',
          whiteSpace: 'nowrap',
        }}
      >
        Посмотреть →
      </button>
      <button
        onClick={onDismiss}
        style={{
          background: 'transparent',
          border: 'none',
          color: 'rgba(255,255,255,0.7)',
          cursor: 'pointer',
          fontSize: '1.1rem',
          padding: '0 4px',
          lineHeight: 1,
        }}
        aria-label="Закрыть"
      >
        ×
      </button>
      <style>{`
        @keyframes brief-banner-in {
          from { opacity: 0; transform: translateY(12px); }
          to   { opacity: 1; transform: translateY(0); }
        }
      `}</style>
    </div>
  );
}

export default function DailyBriefBlock() {
  return (
    <div className="ai-card" style={{ flexDirection: 'column', alignItems: 'stretch' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
          <div className="ai-icon">✦</div>
          <div className="ai-body">
            <h3>AI CFO</h3>
            <p>Функционал в разработке.</p>
          </div>
        </div>
        <div
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 8,
            padding: '6px 10px',
            borderRadius: 999,
            background: 'rgba(155,114,203,0.10)',
            border: '1px solid rgba(155,114,203,0.25)',
            color: '#7c3aed',
            fontWeight: 600,
            whiteSpace: 'nowrap',
          }}
          title="Функционал в разработке"
        >
          <span
            style={{
              width: 10,
              height: 10,
              borderRadius: '50%',
              border: '2px solid rgba(124,58,237,0.25)',
              borderTopColor: 'rgba(124,58,237,0.95)',
              animation: 'ai-dev-spin 0.9s linear infinite',
              display: 'inline-block',
            }}
          />
          В разработке
        </div>
      </div>
      <style>{`
        @keyframes ai-dev-spin {
          from { transform: rotate(0deg); }
          to   { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  );
}
