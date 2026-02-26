/**
 * src/utils/dom.js
 * ──────────────────
 * Lightweight DOM query helpers and sanitisation utilities.
 * Keeps component code readable and DRY.
 */

/**
 * Shorthand for document.querySelector.
 * @param {string} selector
 * @param {Element} [root=document]
 * @returns {Element|null}
 */
export const $ = (selector, root = document) => root.querySelector(selector);

/**
 * Shorthand for document.querySelectorAll returning an Array.
 * @param {string} selector
 * @param {Element} [root=document]
 * @returns {Element[]}
 */
export const $$ = (selector, root = document) =>
    Array.from(root.querySelectorAll(selector));

/**
 * Escapes HTML special characters to prevent XSS.
 * Use whenever rendering user-supplied or DB-sourced text into innerHTML.
 * @param {string} str
 * @returns {string}
 */
export function escHtml(str) {
    return (str ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

/**
 * Sets the text content of an element if it exists — no-op if element is null.
 * @param {Element|null} el
 * @param {string} text
 */
export function setText(el, text) {
    if (el) el.textContent = text;
}

/**
 * Adds CSS classes to an element. Safe to call with null.
 * @param {Element|null} el
 * @param {...string} classes
 */
export function addClass(el, ...classes) {
    el?.classList.add(...classes);
}

/**
 * Removes CSS classes from an element. Safe to call with null.
 * @param {Element|null} el
 * @param {...string} classes
 */
export function removeClass(el, ...classes) {
    el?.classList.remove(...classes);
}

/**
 * Generates an avatar fallback string (up to 2 initials) from a full name.
 * @param {string} name
 * @returns {string}
 */
export function getInitials(name = '') {
    return name
        .split(' ')
        .map((w) => w[0])
        .join('')
        .slice(0, 2)
        .toUpperCase();
}
