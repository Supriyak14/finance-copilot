// Chart.js chart setup
//
// Exposes window.FinanceCharts with a render function per chart. Each one creates the
// chart on first call and updates the existing instance afterwards — rebuilding
// on every refresh leaks canvases and makes the tooltips flicker.

(function () {
  "use strict";

  // Distinct enough to stay readable next to each other, and colourblind-safe
  // in order (blue / teal / amber / red first).
  const PALETTE = [
    "#6c8cff", "#4ec9a5", "#f0b849", "#f2777a", "#b48ce8",
    "#4cb5d9", "#e88ab5", "#8fbf6a", "#d99b5c", "#8d94a8",
  ];

  const charts = {};

  // Read theme colours off the document so the charts follow the CSS variables
  // instead of hardcoding a second, drifting palette.
  function themeColor(name, fallback) {
    const value = getComputedStyle(document.documentElement)
      .getPropertyValue(name)
      .trim();
    return value || fallback;
  }

  function baseOptions() {
    const text = themeColor("--text", "#e8eaed");
    const muted = themeColor("--muted", "#949cab");
    const border = themeColor("--border", "#2a3038");
    return { text, muted, border };
  }

  function destroyIfEmpty(key, hasData) {
    if (!hasData && charts[key]) {
      charts[key].destroy();
      delete charts[key];
    }
    return hasData;
  }

  /** Doughnut of expense totals per category. */
  function renderCategoryChart(canvas, rows) {
    if (!destroyIfEmpty("category", rows.length > 0)) return;

    const { text, muted, border } = baseOptions();
    const labels = rows.map((r) => r.category);
    const data = rows.map((r) => Number(r.total));
    const colors = rows.map((_, i) => PALETTE[i % PALETTE.length]);

    if (charts.category) {
      const chart = charts.category;
      chart.data.labels = labels;
      chart.data.datasets[0].data = data;
      chart.data.datasets[0].backgroundColor = colors;
      chart.$rows = rows;
      chart.update();
      return;
    }

    charts.category = new Chart(canvas, {
      type: "doughnut",
      data: {
        labels,
        datasets: [
          { data, backgroundColor: colors, borderColor: border, borderWidth: 2 },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: "58%",
        plugins: {
          legend: {
            position: "right",
            labels: { color: muted, boxWidth: 10, boxHeight: 10, padding: 10, font: { size: 12 } },
          },
          tooltip: {
            callbacks: {
              label(ctx) {
                // Read from the chart, not the closure — `rows` would be stale
                // after the first refresh.
                const row = (ctx.chart.$rows || [])[ctx.dataIndex];
                const share = row ? ` (${row.share}%)` : "";
                return ` ${ctx.label}: ${formatNumber(ctx.parsed)}${share}`;
              },
            },
          },
        },
      },
    });
    charts.category.$rows = rows;
  }

  /** Grouped bars: income vs expense per month. */
  function renderMonthlyChart(canvas, points) {
    if (!destroyIfEmpty("monthly", points.length > 0)) return;

    const { muted, border } = baseOptions();
    const labels = points.map((p) => monthLabel(p.month));
    const income = points.map((p) => Number(p.income));
    const expense = points.map((p) => Number(p.expense));

    if (charts.monthly) {
      const chart = charts.monthly;
      chart.data.labels = labels;
      chart.data.datasets[0].data = income;
      chart.data.datasets[1].data = expense;
      chart.update();
      return;
    }

    charts.monthly = new Chart(canvas, {
      type: "bar",
      data: {
        labels,
        datasets: [
          { label: "Income", data: income, backgroundColor: "#4ec9a5", borderRadius: 5, maxBarThickness: 34 },
          { label: "Expenses", data: expense, backgroundColor: "#f2777a", borderRadius: 5, maxBarThickness: 34 },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: { grid: { display: false }, ticks: { color: muted, font: { size: 12 } } },
          y: {
            beginAtZero: true,
            grid: { color: border },
            ticks: { color: muted, font: { size: 12 }, callback: (v) => formatNumber(v) },
          },
        },
        plugins: {
          legend: { labels: { color: muted, boxWidth: 10, boxHeight: 10, font: { size: 12 } } },
          tooltip: { callbacks: { label: (ctx) => ` ${ctx.dataset.label}: ${formatNumber(ctx.parsed.y)}` } },
        },
      },
    });
  }

  /** Expenses per month: what actually happened, then where it is heading.
   *
   * Points look like {month, actual, projected}; either value may be null. The
   * two series are deliberately separate datasets rather than one — a forecast
   * drawn in the same solid bar as a recorded month reads as fact. */
  function renderForecastChart(canvas, points) {
    if (!destroyIfEmpty("forecast", points.length > 0)) return;

    const { muted, border } = baseOptions();
    const labels = points.map((p) => monthLabel(p.month));
    const actual = points.map((p) => p.actual);
    const projected = points.map((p) => p.projected);

    if (charts.forecast) {
      const chart = charts.forecast;
      chart.data.labels = labels;
      chart.data.datasets[0].data = actual;
      chart.data.datasets[1].data = projected;
      chart.update();
      return;
    }

    charts.forecast = new Chart(canvas, {
      type: "bar",
      data: {
        labels,
        datasets: [
          {
            label: "Spent",
            data: actual,
            backgroundColor: "#f2777a",
            borderRadius: 5,
            maxBarThickness: 30,
          },
          {
            label: "Projected",
            data: projected,
            // Hollow and dashed, so a projection never passes for a fact.
            backgroundColor: "rgba(242, 119, 122, 0.22)",
            borderColor: "#f2777a",
            borderWidth: 1.5,
            borderDash: [4, 3],
            borderRadius: 5,
            maxBarThickness: 30,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: { grid: { display: false }, ticks: { color: muted, font: { size: 11 } } },
          y: {
            beginAtZero: true,
            grid: { color: border },
            ticks: { color: muted, font: { size: 12 }, callback: (v) => formatNumber(v) },
          },
        },
        plugins: {
          legend: { labels: { color: muted, boxWidth: 10, boxHeight: 10, font: { size: 12 } } },
          tooltip: {
            callbacks: {
              // Skip the empty half of each month rather than printing "null".
              label: (ctx) =>
                ctx.parsed.y === null
                  ? null
                  : ` ${ctx.dataset.label}: ${formatNumber(ctx.parsed.y)}`,
            },
          },
        },
      },
    });
  }

  // Compact axis/tooltip numbers: 1.2k rather than 1200.
  function formatNumber(value) {
    const n = Number(value) || 0;
    if (Math.abs(n) >= 1000) {
      return (n / 1000).toFixed(n % 1000 === 0 ? 0 : 1).replace(/\.0$/, "") + "k";
    }
    return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
  }

  // "2026-08" -> "Aug 2026". Built from parts so it stays in UTC and doesn't
  // slip a month for users behind GMT.
  function monthLabel(ym) {
    const [year, month] = ym.split("-").map(Number);
    const name = new Date(Date.UTC(year, month - 1, 1)).toLocaleString(undefined, {
      month: "short",
      timeZone: "UTC",
    });
    return `${name} ${year}`;
  }

  window.FinanceCharts = {
    renderCategoryChart,
    renderMonthlyChart,
    renderForecastChart,
    monthLabel,
  };
})();
