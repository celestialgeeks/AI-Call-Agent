/**
 * src/components/Chart.js
 * ────────────────────────
 * Canvas-based line chart component.
 * No external charting library — pure Canvas 2D API.
 */

const DEFAULT_COLOR = '#7C5CFC';

/**
 * Renders an animated bezier line chart on a <canvas> element.
 * @param {string} canvasId   — id of the canvas element
 * @param {number[]} data     — array of numeric values
 * @param {string} [color]    — hex color for line + fill
 */
export function drawLineChart(canvasId, data, color = DEFAULT_COLOR) {
    const canvas = document.getElementById(canvasId);
    if (!canvas || data.length < 2) return;

    const ctx = canvas.getContext('2d');
    const W = canvas.parentElement.offsetWidth;
    const H = 200;
    const DPR = window.devicePixelRatio || 1;

    canvas.width = W * DPR;
    canvas.height = H * DPR;
    canvas.style.width = `${W}px`;
    canvas.style.height = `${H}px`;
    ctx.scale(DPR, DPR);

    const PAD_L = 44, PAD_R = 20, PAD_T = 16, PAD_B = 28;
    const chartW = W - PAD_L - PAD_R;
    const chartH = H - PAD_T - PAD_B;
    const max = Math.max(...data) * 1.15 || 1;
    const step = chartW / (data.length - 1);

    const px = (i) => PAD_L + i * step;
    const py = (v) => PAD_T + chartH - (v / max) * chartH;

    ctx.clearRect(0, 0, W, H);

    // ── Grid lines + Y-axis labels ───────────────────────────
    ctx.strokeStyle = 'rgba(0,0,0,0.05)';
    ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i++) {
        const y = PAD_T + (chartH / 4) * i;
        const val = Math.round(max - (max / 4) * i);
        ctx.beginPath();
        ctx.moveTo(PAD_L, y);
        ctx.lineTo(W - PAD_R, y);
        ctx.stroke();

        ctx.fillStyle = 'rgba(0,0,0,0.3)';
        ctx.font = `600 9px 'Roboto Mono', monospace`;
        ctx.textAlign = 'right';
        ctx.fillText(val >= 1000 ? `${(val / 1000).toFixed(1)}k` : val, PAD_L - 6, y + 3);
    }

    // ── Gradient fill ─────────────────────────────────────────
    const grad = ctx.createLinearGradient(0, PAD_T, 0, PAD_T + chartH);
    grad.addColorStop(0, `${color}28`);
    grad.addColorStop(1, `${color}00`);

    const buildPath = () => {
        ctx.beginPath();
        ctx.moveTo(px(0), py(data[0]));
        for (let i = 1; i < data.length; i++) {
            const cpx = (px(i - 1) + px(i)) / 2;
            ctx.bezierCurveTo(cpx, py(data[i - 1]), cpx, py(data[i]), px(i), py(data[i]));
        }
    };

    buildPath();
    ctx.lineTo(px(data.length - 1), PAD_T + chartH);
    ctx.lineTo(PAD_L, PAD_T + chartH);
    ctx.closePath();
    ctx.fillStyle = grad;
    ctx.fill();

    // ── Line ─────────────────────────────────────────────────
    buildPath();
    ctx.strokeStyle = color;
    ctx.lineWidth = 2.5;
    ctx.lineJoin = 'round';
    ctx.stroke();

    // ── Highlight dots at start, mid, and end ────────────────
    [0, Math.floor(data.length / 2), data.length - 1].forEach((i) => {
        ctx.beginPath();
        ctx.arc(px(i), py(data[i]), 4, 0, Math.PI * 2);
        ctx.fillStyle = color;
        ctx.fill();
        ctx.strokeStyle = 'white';
        ctx.lineWidth = 2;
        ctx.stroke();
    });
}
