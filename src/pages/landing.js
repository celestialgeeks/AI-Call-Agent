/**
 * src/pages/landing.js
 * ─────────────────────
 * Entry point for index.html (landing page).
 * Handles navbar scroll effects, animations, and demo orb interaction.
 */

// ── Navbar scroll effect ─────────────────────────────────────
const navbar = document.getElementById('navbar');
if (navbar) {
    window.addEventListener('scroll', () => {
        navbar.classList.toggle('scrolled', window.scrollY > 40);
    }, { passive: true });
}

// ── Intersection observer: fade-up animations ────────────────
const fadeObserver = new IntersectionObserver(
    (entries) => {
        entries.forEach((entry) => {
            if (entry.isIntersecting) {
                entry.target.classList.add('visible');
                fadeObserver.unobserve(entry.target);
            }
        });
    },
    { threshold: 0.1, rootMargin: '0px 0px -48px 0px' }
);

document.querySelectorAll('.fade-up').forEach((el) => fadeObserver.observe(el));

// ── Demo orb interaction → simulated call widget (issue #6) ──
import { startDemo } from '@/widgets/demoCallWidget.js';

/** Global handler for the demo orb + START CALL button — called from HTML */
window.startDemo = () => startDemo();

// ── Smooth scroll for anchor links ──────────────────────────
document.querySelectorAll('a[href^="#"]').forEach((link) => {
    link.addEventListener('click', (e) => {
        const target = document.querySelector(link.getAttribute('href'));
        if (target) {
            e.preventDefault();
            target.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
    });
});

// ── Toast helper for CTA copy confirmation ───────────────────
import { showToast } from '@/utils/toast.js';

/** Global handler for the pricing CTA — can be called from HTML */
window.copyEmbedCode = () => {
    navigator.clipboard.writeText('<sahaiy-widget agent-id="ag_demo"></sahaiy-widget>').then(() => {
        showToast('✅ Embed code copied!', 'success');
    });
};
