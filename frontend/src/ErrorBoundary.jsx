import { Component } from 'react';

/** Ловит падение React и не оставляет пустой серый экран без объяснения. */
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { err: null };
  }

  static getDerivedStateFromError(err) {
    return { err };
  }

  componentDidCatch(err, info) {
    console.error('[WB FINANCE] UI error', err, info);
  }

  render() {
    if (this.state.err) {
      return (
        <div
          style={{
            minHeight: '100vh',
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            padding: 24,
            fontFamily: 'system-ui, sans-serif',
            background: '#f4f6f8',
            color: '#344767',
          }}
        >
          <p style={{ fontWeight: 700, marginBottom: 8 }}>Не удалось открыть приложение</p>
          <p style={{ maxWidth: 480, textAlign: 'center', fontSize: 14, marginBottom: 16 }}>
            Обновите страницу. Если так и остаётся — откройте консоль (F12) и пришлите текст ошибки в поддержку.
          </p>
          <pre
            style={{
              fontSize: 12,
              overflow: 'auto',
              maxWidth: '90vw',
              padding: 12,
              background: '#fff',
              borderRadius: 8,
              border: '1px solid #ddd',
            }}
          >
            {String(this.state.err?.message || this.state.err)}
          </pre>
        </div>
      );
    }
    return this.props.children;
  }
}
