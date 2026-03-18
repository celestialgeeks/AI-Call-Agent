/**
 * src/pages/auth.js
 * ──────────────────
 * Entry point for auth.html.
 * Orchestrates auth flows — no business logic, only UI + service calls.
 *
 * All Supabase calls are delegated to authService.
 * All DOM manipulation is local to this file; no globals on window.
 */

import { getSession, signInWithGoogle, signInWithGitHub, signInWithEmail, signUpWithEmail, resetPassword } from '@/services/authService.js';
import { isConfigured } from '@/services/supabaseClient.js';
import { showToast } from '@/utils/toast.js';
import { $ } from '@/utils/dom.js';

// ── On load: redirect to dashboard if already authenticated ─────
(async () => {
    const session = await getSession();
    if (session) window.location.replace('/app.html');
})();

// ── Show setup warning when Supabase credentials are not configured ──
if (!isConfigured) {
    document.addEventListener('DOMContentLoaded', () => {
        _showError(
            '⚙️ Supabase is not configured. ' +
            'Copy .env.example → .env and fill in your project URL and anon key.'
        );
    });
}

// ── URL param: ?mode=login → auto-switch to login tab ───────────
if (new URLSearchParams(window.location.search).get('mode') === 'login') {
    switchTab('login');
}

// ═══════════════════════════════════════════════════
//  Exported handlers — attached via onclick in HTML
// ═══════════════════════════════════════════════════

/** Switches between Sign In and Create Account tabs. */
export function switchTab(tab) {
    _clearFeedback();
    const isLogin = tab === 'login';

    $('#form-signup').style.display = isLogin ? 'none' : 'flex';
    $('#form-login').style.display = isLogin ? 'flex' : 'none';
    $('#tab-login').classList.toggle('active', isLogin);
    $('#tab-signup').classList.toggle('active', !isLogin);
    $('#nav-toggle-text').innerHTML = isLogin
        ? `Don't have an account? <a onclick="switchTab('signup')">Sign up free</a>`
        : `Already have an account? <a onclick="switchTab('login')">Sign in</a>`;
}

/** Initiates Google OAuth flow. */
export async function handleGoogle() {
    if (!_requireConfigured()) return;
    const btn = $('#google-btn');
    _setButtonLoading(btn, 'Connecting to Google…');
    const { error } = await signInWithGoogle();
    if (error) { _resetButton(btn, _googleBtnHtml()); _showError(error.message); }
}

/** Initiates GitHub OAuth flow. */
export async function handleGitHub() {
    if (!_requireConfigured()) return;
    const btn = $('#github-btn');
    _setButtonLoading(btn, 'Connecting to GitHub…');
    const { error } = await signInWithGitHub();
    if (error) { _resetButton(btn, 'Continue with GitHub'); _showError(error.message); }
}

/** Handles the email/password sign-up form submission. */
export async function handleSignup(e) {
    e.preventDefault();
    if (!_requireConfigured()) return;
    const btn = $('#signup-btn');
    const email = $('#signup-email')?.value.trim() ?? '';
    const first = $('#signup-firstname')?.value.trim() ?? '';
    const last = $('#signup-lastname')?.value.trim() ?? '';
    const pass = $('#signup-password')?.value ?? '';

    if (pass.length < 8) { _showError('Password must be at least 8 characters.'); return; }

    _setButtonLoading(btn, 'Creating account…');

    const { data, error } = await signUpWithEmail(email, pass, {
        full_name: `${first} ${last}`.trim(),
        name: `${first} ${last}`.trim(),
    });

    if (error) {
        _resetButton(btn, 'Create free account →');
        _showError(error.message);
        return;
    }

    if (data?.session) {
        window.location.replace('/app.html');
    } else {
        _resetButton(btn, '✓ Check your email');
        _showSuccess(`Check your inbox! We sent a confirmation link to ${email}`);
    }
}

/** Handles the email/password sign-in form submission. */
export async function handleLogin(e) {
    e.preventDefault();
    if (!_requireConfigured()) return;
    const btn = $('#login-btn');
    const email = $('#login-email')?.value.trim() ?? '';
    const pass = $('#login-password')?.value ?? '';

    _setButtonLoading(btn, 'Signing in…');

    const { error } = await signInWithEmail(email, pass);
    if (error) {
        _resetButton(btn, 'Sign in →');
        _showError('Incorrect email or password.');
        return;
    }
    window.location.replace('/app.html');
}

/** Handles "Forgot password?" link. */
export async function handleForgotPassword(e) {
    e.preventDefault();
    const email = $('#login-email')?.value.trim();
    if (!email) { _showError('Enter your email address first.'); return; }
    await resetPassword(email);
    _showSuccess(`Password reset link sent to ${email}`);
}

/** Updates the password strength indicator bar. */
export function onPasswordInput(val) {
    const indicator = $('#strength-indicator');
    if (!indicator) return;
    indicator.style.display = val.length > 0 ? 'block' : 'none';

    let score = 0;
    if (val.length >= 8) score++;
    if (/[A-Z]/.test(val)) score++;
    if (/[0-9]/.test(val)) score++;
    if (/[^A-Za-z0-9]/.test(val)) score++;

    const colors = ['#ef4444', '#f59e0b', '#22c55e', '#7C5CFC'];
    const labels = ['Weak', 'Fair', 'Good', 'Strong'];
    ['s1', 's2', 's3', 's4'].forEach((id, i) => {
        const el = $(`#${id}`);
        if (el) el.style.background = i < score ? colors[score - 1] : 'var(--border)';
    });
    const labelEl = $('#strength-text');
    if (labelEl) {
        labelEl.textContent = score > 0 ? labels[score - 1] : 'Too short';
        labelEl.style.color = score > 0 ? colors[score - 1] : 'var(--text-muted)';
    }
}

// ═══════════════════════════════════════════════════
//  Expose to HTML onclick attributes (Vite keeps modules scoped)
// ═══════════════════════════════════════════════════
Object.assign(window, {
    switchTab,
    handleGoogle,
    handleGitHub,
    handleSignup,
    handleLogin,
    handleForgotPassword,
    onPasswordInput,
});

// ═══════════════════════════════════════════════════
//  Private helpers
// ═══════════════════════════════════════════════════

/**
 * Shows a setup error and returns false when Supabase is not configured.
 * Use as an early-return guard in every auth action handler.
 * @returns {boolean} true if configured, false otherwise
 */
function _requireConfigured() {
    if (isConfigured) return true;
    _showError(
        '⚙️ Supabase is not configured. ' +
        'Copy .env.example → .env and fill in your project URL and anon key.'
    );
    return false;
}

function _showError(msg) {
    const el = $('#auth-error');
    if (!el) return;
    el.textContent = msg;
    el.classList.add('show');
    $('#auth-success')?.classList.remove('show');
    setTimeout(() => el.classList.remove('show'), 6_000);
}

function _showSuccess(msg) {
    const el = $('#auth-success');
    if (!el) return;
    el.textContent = msg;
    el.classList.add('show');
    $('#auth-error')?.classList.remove('show');
}

function _clearFeedback() {
    $('#auth-error')?.classList.remove('show');
    $('#auth-success')?.classList.remove('show');
}

function _setButtonLoading(btn, text) {
    if (!btn) return;
    btn.textContent = text;
    btn.classList.add('loading');
    btn.disabled = true;
}

function _resetButton(btn, html) {
    if (!btn) return;
    btn.innerHTML = html;
    btn.classList.remove('loading');
    btn.disabled = false;
}

function _googleBtnHtml() {
    return `<svg class="social-btn-icon" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4"/><path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/><path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05"/><path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/></svg>Continue with Google`;
}
