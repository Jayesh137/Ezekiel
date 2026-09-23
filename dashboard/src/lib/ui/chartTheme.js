// One Chart.js theme for every page, taken from the tokens in app.css.
//
// Chart.js draws to a canvas and cannot read CSS variables, so the values are
// mirrored here. Keep them in step with :root in app.css — the pairing is the
// whole point: a chart in a different palette from the card around it is what
// made the old dashboard look assembled rather than designed.

export const C = {
	accent: '#8b93ff',
	green: '#34d399',
	red: '#f87171',
	blue: '#7c9cff',
	purple: '#b596f8',
	amber: '#f5b544',
	cyan: '#5cc8d7',
	grey: '#9a9aa6',
	text: '#ededf0',
	textSecondary: '#a1a1aa',
	tick: '#86868f',
	grid: 'rgba(255, 255, 255, 0.05)',
	border: '#26262c',
	surface: '#111114',
	elevated: '#16161a'
};

/** `#rrggbb` → `rgba(r, g, b, a)`. */
export function alpha(hex, a) {
	const n = parseInt(hex.slice(1), 16);
	return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
}

/** Categorical order for pies and multi-series: distinct in hue and lightness. */
export const SERIES = [C.accent, C.cyan, C.amber, C.purple, C.green, C.red, C.blue, C.grey];

export const MONO = "'JetBrains Mono', ui-monospace, monospace";
export const SANS = "'Inter', -apple-system, sans-serif";

/** Vertical gradient fill under a line, fading to nothing at the axis. */
export function areaFill(hex, top = 0.22) {
	return (ctx) => {
		const { chart } = ctx;
		const area = chart.chartArea;
		if (!area) return alpha(hex, top / 2);
		const g = chart.ctx.createLinearGradient(0, area.top, 0, area.bottom);
		g.addColorStop(0, alpha(hex, top));
		g.addColorStop(1, alpha(hex, 0));
		return g;
	};
}

export const tooltip = {
	backgroundColor: 'rgba(22, 22, 26, 0.96)',
	borderColor: C.border,
	borderWidth: 1,
	titleColor: C.text,
	bodyColor: C.textSecondary,
	titleFont: { family: SANS, size: 12, weight: '600' },
	bodyFont: { family: SANS, size: 12 },
	padding: 10,
	cornerRadius: 8,
	boxPadding: 4,
	usePointStyle: true
};

export const tickFont = { family: SANS, size: 11 };

/** Standard x/y scales: hairline grid, no axis border, muted ticks. */
export function scale({ ticks, grid, ...rest } = {}) {
	return {
		grid: { color: C.grid, drawTicks: false, ...grid },
		border: { display: false },
		ticks: { color: C.tick, font: tickFont, padding: 8, ...ticks },
		...rest
	};
}

/** Radar scale for the fingerprint/scanner comparisons. */
export function radialScale({ ticks, pointLabels, ...rest } = {}) {
	return {
		grid: { color: C.grid },
		angleLines: { color: C.grid },
		pointLabels: { color: C.textSecondary, font: { family: SANS, size: 11, weight: '500' }, ...pointLabels },
		ticks: { color: C.tick, backdropColor: 'transparent', font: { family: SANS, size: 9 }, ...ticks },
		...rest
	};
}

/** Apply the theme's defaults once, before any chart is constructed. */
export function applyChartDefaults(Chart) {
	Chart.defaults.color = C.tick;
	Chart.defaults.borderColor = C.grid;
	Chart.defaults.font.family = SANS;
	Chart.defaults.font.size = 11;
	Chart.defaults.plugins.tooltip = { ...Chart.defaults.plugins.tooltip, ...tooltip };
	Chart.defaults.plugins.legend.labels.usePointStyle = true;
	Chart.defaults.plugins.legend.labels.boxWidth = 8;
	Chart.defaults.plugins.legend.labels.boxHeight = 8;
	Chart.defaults.elements.line.borderWidth = 2;
	Chart.defaults.elements.line.tension = 0.35;
	Chart.defaults.elements.point.radius = 0;
	Chart.defaults.elements.point.hoverRadius = 4;
	Chart.defaults.elements.bar.borderRadius = 4;
	Chart.defaults.elements.arc.borderWidth = 2;
	Chart.defaults.elements.arc.borderColor = C.surface;
}
