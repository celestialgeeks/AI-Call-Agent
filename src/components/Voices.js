/**
 * src/components/Voices.js
 * ────────────────────────
 * Voices Library page (design spec §2).
 * Renders the 9-speaker Sarvam catalog with per-voice TTS preview.
 * Preview calls the backend's /tts/preview endpoint; failures degrade to a
 * toast + reverted play button — cards stay browsable (spec §2 error state).
 *
 * ADR-0001: this is a static known catalog — no fabricated usage numbers,
 * no empty state needed.
 */

import { $, escHtml } from '@/utils/dom.js';
import { showToast } from '@/utils/toast.js';
import env from '@/config/env.js';

/** The full Sarvam Bulbul speaker list (docs.sarvam.ai text-to-speech speakers). */
export const SARVAM_VOICES = [
    { slug: 'anushka',  name: 'Anushka',  gender: 'Female' },
    { slug: 'abhilash', name: 'Abhilash', gender: 'Male' },
    { slug: 'manisha',  name: 'Manisha',  gender: 'Female' },
    { slug: 'vidya',    name: 'Vidya',    gender: 'Female' },
    { slug: 'arjun',    name: 'Arjun',    gender: 'Male' },
    { slug: 'maya',     name: 'Maya',     gender: 'Female' },
    { slug: 'neel',     name: 'Neel',     gender: 'Male' },
    { slug: 'maitreyi', name: 'Maitreyi', gender: 'Female' },
    { slug: 'amartya',  name: 'Amartya',  gender: 'Male' },
];

/**
 * Deterministic avatar gradient per voice (spec §2):
 * female voices rotate through the purple→teal accent family,
 * male voices through dark greens — matching the landing palette.
 */
const GRADIENTS = {
    Female: [
        'linear-gradient(135deg,#7C5CFC,#00C4A1)',
        'linear-gradient(135deg,#a78bfa,#34d399)',
        'linear-gradient(135deg,#8b5cf6,#2dd4bf)',
        'linear-gradient(135deg,#c4b5fd,#5eead4)',
        'linear-gradient(135deg,#6d28d9,#0d9488)',
    ],
    Male: [
        'linear-gradient(135deg,#14532d,#4ade80)',
        'linear-gradient(135deg,#166534,#86efac)',
        'linear-gradient(135deg,#052e16,#22c55e)',
        'linear-gradient(135deg,#1a4d2e,#6ee7b7)',
    ],
};
const genderCounters = { Female: 0, Male: 0 };

function gradientFor(voice) {
    const pool = GRADIENTS[voice.gender];
    const g = pool[genderCounters[voice.gender] % pool.length];
    genderCounters[voice.gender] += 1;
    return g;
}

// ── Module-level preview player state: ONE preview at a time ──
let _currentAudio = null;
let _currentSlug = null;

function stopCurrentPreview() {
    if (_currentAudio) {
        _currentAudio.pause();
        _currentAudio = null;
    }
    if (_currentSlug) {
        _setPlayingUI(_currentSlug, false);
        _currentSlug = null;
    }
}

/** Flips play/pause icon + waveform animation on a card. */
function _setPlayingUI(slug, playing) {
    const card = document.querySelector(`[data-voice="${slug}"]`);
    if (!card) return;
    card.classList.toggle('voice-card--playing', playing);
    const btn = card.querySelector('.voice-card__play');
    if (btn) btn.textContent = playing ? '\u23F8' : '\u25B6'; // ⏸ / ▶
}

/**
 * Fetches a TTS preview for one voice and plays it.
 * Any failure ⇒ toast + UI revert (spec §2 error state) — never throws.
 * @param {string} slug — Sarvam speaker identifier
 */
async function previewVoice(slug) {
    // Re-click on the playing voice toggles it off.
    if (_currentSlug === slug) { stopCurrentPreview(); return; }

    stopCurrentPreview();
    _currentSlug = slug;
    _setPlayingUI(slug, true);

    try {
        const res = await fetch(`${env.backendUrl}/tts/preview`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: `Hello! This is ${slug}.`, speaker: slug }),
        });
        if (!res.ok) throw new Error(`TTS preview failed (${res.status})`);

        const blob = await res.blob();
        if (!blob.type.startsWith('audio')) throw new Error('Unexpected TTS response');

        const audio = new Audio(URL.createObjectURL(blob));
        _currentAudio = audio;
        audio.onended = () => { if (_currentAudio === audio) stopCurrentPreview(); };
        await audio.play();
    } catch (err) {
        console.error('[Voices.previewVoice]', err);
        showToast('\u26A0\uFE0F Voice preview unavailable right now', 'error');
        stopCurrentPreview();
    }
}

// ── Rendering ────────────────────────────────────────────────

let _filterGender = 'All';
let _filterQuery = '';

function _visibleVoices() {
    return SARVAM_VOICES.filter((v) => {
        const genderOk = _filterGender === 'All' || v.gender === _filterGender;
        const q = _filterQuery.trim().toLowerCase();
        const queryOk = !q || v.name.toLowerCase().includes(q) || v.slug.includes(q);
        return genderOk && queryOk;
    });
}

export function renderVoiceGrid() {
    const grid = $('#voices-grid');
    if (!grid) return;

    grid.innerHTML = _visibleVoices()
        .map((v) => {
            const initial = escHtml(v.name.charAt(0));
            return `
      <div class="voice-card" data-voice="${v.slug}" tabindex="0" role="button"
           aria-label="Preview voice ${escHtml(v.name)}">
        <div class="voice-card__avatar" style="background:${gradientFor(v)}">${initial}</div>
        <div class="voice-card__info">
          <div class="voice-card__name">${escHtml(v.name)}</div>
          <div class="voice-card__slug">${v.slug}</div>
          <div class="voice-card__lang">Hindi \u00B7 English</div>
        </div>
        <button class="voice-card__play" aria-label="Play ${escHtml(v.name)} preview">\u25B6</button>
      </div>`;
        })
        .join('');

    grid.querySelectorAll('.voice-card').forEach((card) => {
        const slug = card.dataset.voice;
        card.addEventListener('click', () => previewVoice(slug));
        card.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); previewVoice(slug); }
        });
    });
}

/** Wires the filter bar once (search input + gender chips). */
export function initVoiceFilters() {
    const search = $('#voice-search');
    search?.addEventListener('input', () => {
        _filterQuery = search.value ?? '';
        renderVoiceGrid();
    });

    document.querySelectorAll('.voice-gender-chip').forEach((chip) => {
        chip.addEventListener('click', () => {
            document.querySelectorAll('.voice-gender-chip').forEach((c) => c.classList.remove('active'));
            chip.classList.add('active');
            _filterGender = chip.dataset.gender ?? 'All';
            renderVoiceGrid();
        });
    });
}
