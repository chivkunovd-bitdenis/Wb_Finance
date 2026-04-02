import { Chart, registerables } from 'chart.js';
import { Line } from 'react-chartjs-2';

Chart.register(...registerables);

function hexToRgba(hex, alpha) {
  const h = (hex || '').replace('#', '').trim();
  if (![3, 6].includes(h.length)) return `rgba(0,0,0,${alpha})`;
  const full = h.length === 3 ? h.split('').map((c) => c + c).join('') : h;
  const num = parseInt(full, 16);
  const r = (num >> 16) & 255;
  const g = (num >> 8) & 255;
  const b = num & 255;
  return `rgba(${r},${g},${b},${alpha})`;
}

const gridColor = 'rgba(0,0,0,0.06)';
const textColor = 'rgba(0,0,0,0.4)';

const chartOptions = {
  responsive: true,
  maintainAspectRatio: false,
  animation: false,
  plugins: {
    legend: { display: false },
    tooltip: {
      mode: 'index',
      intersect: false,
      backgroundColor: '#fff',
      titleColor: textColor,
      bodyColor: '#111',
      borderColor: gridColor,
      borderWidth: 1,
      padding: 8,
      callbacks: {
        label: (ctx) => ' ' + ctx.parsed.y.toLocaleString('ru') + ' ₽',
      },
    },
  },
  scales: {
    x: { grid: { color: gridColor }, ticks: { color: textColor, font: { size: 10 }, maxTicksLimit: 8, maxRotation: 0 } },
    y: { grid: { color: gridColor }, ticks: { color: textColor, font: { size: 10 }, maxTicksLimit: 5, callback: (v) => v.toLocaleString('ru') } },
  },
};

export default function ChartCard({ title, badge, labels, data, borderColor }) {
  const fill = borderColor ? hexToRgba(borderColor, 0.1) : 'rgba(0,0,0,0.08)';

  return (
    <div className="chart-card">
      <div className="chart-header">
        <span className="chart-title">{title}</span>
        <span className="chart-badge">{badge}</span>
      </div>
      <div className="chart-canvas">
        <Line
          data={{
            labels,
            datasets: [
              {
                data,
                borderColor,
                backgroundColor: fill,
                borderWidth: 1.5,
                pointRadius: 0,
                pointHoverRadius: 4,
                tension: 0.4,
                fill: 'origin',
              },
            ],
          }}
          options={chartOptions}
        />
      </div>
    </div>
  );
}

