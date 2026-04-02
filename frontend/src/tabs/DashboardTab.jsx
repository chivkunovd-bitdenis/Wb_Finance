/* eslint react-hooks/set-state-in-effect: off */
import { useState, useEffect } from 'react';
import { Chart, registerables } from 'chart.js';
import { Line } from 'react-chartjs-2';
import * as api from '../api';

Chart.register(...registerables);

function formatDate(iso) {
  if (!iso) return '';
  const parts = iso.split('-');
  return parts.length >= 2 ? `${parts[2]}.${parts[1]}` : iso;
}

function formatNum(n) {
  if (n == null || n === '') return '—';
  return Math.round(Number(n)).toLocaleString();
}

const chartOptions = (isPercent) => ({
  responsive: true,
  maintainAspectRatio: false,
  plugins: { legend: { display: false } },
  scales: {
    y: {
      ticks: {
        font: { size: 10 },
        callback: (v) => (isPercent ? v + '%' : Number(v).toLocaleString()),
      },
    },
  },
});

export default function DashboardTab({ range, refreshTrigger, cache, updateCache }) {
  const [pnl, setPnl] = useState(() => (cache?.pnl && Array.isArray(cache.pnl) ? cache.pnl : []));
  const [loading, setLoading] = useState(() => !(cache?.pnl?.length));
  const [error, setError] = useState('');

  const { dateFrom, dateTo } = range || {};

  useEffect(() => {
    if (!dateFrom || !dateTo) return;
    setLoading(true);
    setError('');
    api
      .getPnl(dateFrom, dateTo)
      .then((data) => {
        const list = Array.isArray(data) ? data : [];
        setPnl(list);
        if (typeof updateCache === 'function') updateCache('pnl', list);
      })
      .catch((e) => setError(e.message || 'Ошибка загрузки'))
      .finally(() => setLoading(false));
  }, [dateFrom, dateTo, refreshTrigger, updateCache]);

  // Не показывать полноэкранный лоадер при смене дат, если данные уже есть — только фоновое обновление (как в GAS, без мигания)
  const showFullLoader = loading && pnl.length === 0;

  const filtered = (pnl || [])
    .filter((r) => r.date >= dateFrom && r.date <= dateTo)
    .sort((a, b) => a.date.localeCompare(b.date));

  const totals = filtered.reduce(
    (acc, r) => {
      const rev = Number(r.revenue) || 0;
      const comm = Number(r.commission) || 0;
      const log = Number(r.logistics) || 0;
      const pen = Number(r.penalties) || 0;
      const stor = Number(r.storage) || 0;
      const ads = Number(r.ads_spend) || 0;
      const cogs = Number(r.cogs) || 0;
      const margin = Number(r.margin) || 0;
      acc.revenue += rev;
      acc.commission += comm;
      acc.logistics += log;
      acc.penalties += pen;
      acc.storage += stor;
      acc.ads += ads;
      acc.cogs += cogs;
      acc.margin += margin;
      return acc;
    },
    { revenue: 0, commission: 0, logistics: 0, penalties: 0, storage: 0, ads: 0, cogs: 0, margin: 0 }
  );

  const labels = filtered.map((r) => formatDate(r.date));
  const revData = filtered.map((r) => Number(r.revenue) || 0);
  const logPct = filtered.map((r) => {
    const s = Number(r.revenue) || 0;
    return s > 0 ? ((Number(r.logistics) || 0) / s) * 100 : 0;
  });
  const adsPct = filtered.map((r) => {
    const s = Number(r.revenue) || 0;
    return s > 0 ? ((Number(r.ads_spend) || 0) / s) * 100 : 0;
  });
  const storPct = filtered.map((r) => {
    const s = Number(r.revenue) || 0;
    return s > 0 ? ((Number(r.storage) || 0) / s) * 100 : 0;
  });
  const marginPct = filtered.map((r) => {
    const s = Number(r.revenue) || 0;
    return s > 0 ? ((Number(r.margin) || 0) / s) * 100 : 0;
  });

  if (showFullLoader) {
    return (
      <div className="text-center py-5">
        <div className="spinner-border text-primary" />
        <p className="mt-2">Загрузка данных...</p>
      </div>
    );
  }

  if (error) {
    return <div className="alert alert-danger">{error}</div>;
  }

  return (
    <>
      {/* AI CFO block — компактный (в разработке) */}
      <div className="cyb-ai-container shadow-sm">
        <div className="cyb-glass-card">
          <div className="d-flex justify-content-between align-items-center mb-3">
            <div>
              <h5 className="fw-bold mb-0">
                <span style={{ background: 'linear-gradient(45deg, #4285f4, #9b72cb, #d96570, #f4b400)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>✦</span> AI CFO
              </h5>
            </div>
            <div
              className="small"
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
          <div className="cyb-ai-text">Функционал в разработке.</div>
          <style>{`
            @keyframes ai-dev-spin {
              from { transform: rotate(0deg); }
              to   { transform: rotate(360deg); }
            }
          `}</style>
        </div>
      </div>

      {/* KPI cards */}
      <div className="row g-3 mb-3">
        <div className="col-md-4">
          <div className="dashboard-card text-center">
            <div className="stat-lbl">Выручка</div>
            <div className="stat-val">{formatNum(totals.revenue)} ₽</div>
          </div>
        </div>
        <div className="col-md-4">
          <div className="dashboard-card text-center">
            <div className="stat-lbl">Маржа</div>
            <div className="stat-val" style={{ color: totals.margin >= 0 ? '#10ac84' : '#ee5253' }}>
              {formatNum(totals.margin)} ₽
            </div>
          </div>
        </div>
        <div className="col-md-4">
          <div className="dashboard-card text-center">
            <div className="stat-lbl">ROI</div>
            <div className="stat-val">
              {totals.cogs > 0 ? Math.round((totals.margin / totals.cogs) * 100) : 0}%
            </div>
          </div>
        </div>
      </div>

      {/* Charts */}
      <div className="row g-3 mb-4">
        <div className="col-12">
          <div className="dashboard-card">
            <div className="stat-lbl">Динамика выручки</div>
            <div className="main-chart-container">
              <Line
                data={{
                  labels,
                  datasets: [
                    {
                      data: revData,
                      borderColor: '#5e35b1',
                      backgroundColor: '#5e35b115',
                      fill: true,
                      tension: 0.3,
                      pointRadius: 2,
                    },
                  ],
                }}
                options={chartOptions(false)}
              />
            </div>
          </div>
        </div>
        <div className="col-md-6">
          <div className="dashboard-card">
            <div className="stat-lbl">Доля логистики %</div>
            <div className="mini-chart-container">
              <Line
                data={{
                  labels,
                  datasets: [{ data: logPct, borderColor: '#ff9f43', backgroundColor: '#ff9f4315', fill: true, tension: 0.3, pointRadius: 2 }],
                }}
                options={chartOptions(true)}
              />
            </div>
          </div>
        </div>
        <div className="col-md-6">
          <div className="dashboard-card">
            <div className="stat-lbl">Доля рекламы %</div>
            <div className="mini-chart-container">
              <Line
                data={{
                  labels,
                  datasets: [{ data: adsPct, borderColor: '#ee5253', backgroundColor: '#ee525315', fill: true, tension: 0.3, pointRadius: 2 }],
                }}
                options={chartOptions(true)}
              />
            </div>
          </div>
        </div>
        <div className="col-md-6">
          <div className="dashboard-card">
            <div className="stat-lbl">Доля хранения %</div>
            <div className="mini-chart-container">
              <Line
                data={{
                  labels,
                  datasets: [{ data: storPct, borderColor: '#0abde3', backgroundColor: '#0abde315', fill: true, tension: 0.3, pointRadius: 2 }],
                }}
                options={chartOptions(true)}
              />
            </div>
          </div>
        </div>
        <div className="col-md-6">
          <div className="dashboard-card">
            <div className="stat-lbl">Маржинальность %</div>
            <div className="mini-chart-container">
              <Line
                data={{
                  labels,
                  datasets: [{ data: marginPct, borderColor: '#10ac84', backgroundColor: '#10ac8415', fill: true, tension: 0.3, pointRadius: 2 }],
                }}
                options={chartOptions(true)}
              />
            </div>
          </div>
        </div>
      </div>

      {/* Table */}
      <div className="table-wrapper shadow-sm">
        <table className="custom-table">
          <thead>
            <tr>
              <th>Дата</th>
              <th>Выручка</th>
              <th>Заказы ₽</th>
              <th>Ком</th>
              <th>% комиссии</th>
              <th>Лог</th>
              <th>% логистики</th>
              <th>Штрафы</th>
              <th>Себес</th>
              <th>% себеса</th>
              <th>Рекл</th>
              <th>% рекламы</th>
              <th>Хран</th>
              <th>% хранения</th>
              <th>Маржа</th>
              <th>% маржи</th>
              <th>ROI</th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 ? (
              <tr>
                <td colSpan={17} className="text-center py-4">Нет данных</td>
              </tr>
            ) : (
              filtered.map((r) => {
                const rev = Number(r.revenue) || 0;
                const comm = Number(r.commission) || 0;
                const log = Number(r.logistics) || 0;
                const pen = Number(r.penalties) || 0;
                const stor = Number(r.storage) || 0;
                const ads = Number(r.ads_spend) || 0;
                const cogs = Number(r.cogs) || 0;
                const margin = Number(r.margin) || 0;
                return (
                  <tr key={r.date}>
                    <td>{formatDate(r.date)}</td>
                    <td>{formatNum(r.revenue)}</td>
                    <td>—</td>
                    <td>{formatNum(comm)}</td>
                    <td>{rev > 0 ? (comm / rev * 100).toFixed(1) : 0}%</td>
                    <td>{formatNum(log)}</td>
                    <td>{rev > 0 ? (log / rev * 100).toFixed(1) : 0}%</td>
                    <td>{formatNum(pen)}</td>
                    <td>{formatNum(cogs)}</td>
                    <td>{rev > 0 ? (cogs / rev * 100).toFixed(1) : 0}%</td>
                    <td>{formatNum(ads)}</td>
                    <td>{rev > 0 ? (ads / rev * 100).toFixed(1) : 0}%</td>
                    <td>{formatNum(stor)}</td>
                    <td>{rev > 0 ? (stor / rev * 100).toFixed(1) : 0}%</td>
                    <td style={{ color: margin >= 0 ? '#10ac84' : '#ee5253', fontWeight: 700 }}>{formatNum(margin)}</td>
                    <td>{rev > 0 ? (margin / rev * 100).toFixed(1) : 0}%</td>
                    <td>{cogs > 0 ? Math.round(margin / cogs * 100) : 0}%</td>
                  </tr>
                );
              })
            )}
          </tbody>
          <tfoot>
            {filtered.length > 0 && (
              <tr>
                <td>ИТОГО</td>
                <td>{formatNum(totals.revenue)}</td>
                <td>—</td>
                <td>{formatNum(totals.commission)}</td>
                <td>{totals.revenue > 0 ? (totals.commission / totals.revenue * 100).toFixed(1) : 0}%</td>
                <td>{formatNum(totals.logistics)}</td>
                <td>{totals.revenue > 0 ? (totals.logistics / totals.revenue * 100).toFixed(1) : 0}%</td>
                <td>{formatNum(totals.penalties)}</td>
                <td>{formatNum(totals.cogs)}</td>
                <td>{totals.revenue > 0 ? (totals.cogs / totals.revenue * 100).toFixed(1) : 0}%</td>
                <td>{formatNum(totals.ads)}</td>
                <td>{totals.revenue > 0 ? (totals.ads / totals.revenue * 100).toFixed(1) : 0}%</td>
                <td>{formatNum(totals.storage)}</td>
                <td>{totals.revenue > 0 ? (totals.storage / totals.revenue * 100).toFixed(1) : 0}%</td>
                <td style={{ color: totals.margin >= 0 ? '#10ac84' : '#ee5253' }}>{formatNum(totals.margin)}</td>
                <td>{totals.revenue > 0 ? (totals.margin / totals.revenue * 100).toFixed(1) : 0}%</td>
                <td>{totals.cogs > 0 ? (totals.margin / totals.cogs * 100).toFixed(0) : 0}%</td>
              </tr>
            )}
          </tfoot>
        </table>
      </div>
    </>
  );
}
