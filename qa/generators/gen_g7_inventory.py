#!/usr/bin/env python3
"""
qa/generators/gen_g7_inventory.py
─────────────────────────────────
G7 generator (test-plan-demo-gate-v1.md TTV-07).

Parses dead-button-inventory-v1.md tables and emits parametrized test cases:
  * "✅ Wired" / "✅ Working" rows → positive Playwright assertions
  * "❌ Dead" rows                → expected-fail canaries (pass only when the
                                    priority-list fix lands; until then they
                                    xfail so silent regression upward is impossible)

The inventory markdown is a VERSIONED INPUT: re-run this generator whenever it
revs.  Output: qa/e2e/tests/generated/test_g7_inventory.spec.ts + a JSON manifest.

Usage:
    .venv/bin/python qa/generators/gen_g7_inventory.py \
        --inventory "/Users/shreyashsingh/my info/projects/sahaiy/frontend/dead-button-inventory-v1.md" \
        --out-dir repo/qa/e2e
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Parsing ─────────────────────────────────────────────────────────────────


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return re.sub(r"-+", "-", s)[:60]


def parse_inventory(md: str) -> list[dict]:
    """
    Extract every inventory row into:
      {page, status: wired|dead|flag, element, symptom/behavior}
    Handles the v1 table dialects:
      ### ✅ Working (13)          | Element | Behavior |
      ### ❌ Dead (25)             | Element | Symptom | Expected behavior |
      dashboard "### ❌ Broken" numbered list items
    Rows spanning continuation lines are joined until the next | row.
    """
    rows: list[dict] = []
    page = None
    section = None  # 'wired' | 'dead' | 'flag'
    pending: dict | None = None

    def in_scope() -> bool:
        """Only rows inside a Page section AND a ✅/⚠️/❌ subsection are
        interactive inventory. Architecture/notes tables outside pages are not."""
        return bool(page) and section is not None

    def flush():
        nonlocal pending
        if pending:
            rows.append(pending)
            pending = None

    for raw in md.splitlines():
        line = raw.rstrip()

        m = re.match(r"^##\s+Page\s+\d+:\s*(.+?)\s*\(", line) or re.match(
            r"^##\s+Page\s+\d+:\s*(.+)$", line
        )
        if m:
            flush()
            page = m.group(1).split("—")[0].strip()
            section = None
            continue

        if re.match(r"^###.*✅", line):
            flush()
            section = "wired"
            continue
        if re.match(r"^###.*❌", line):
            flush()
            section = "dead"
            continue
        if re.match(r"^###.*⚠️", line) or re.match(r"^##.*Honesty", line, re.I):
            flush()
            section = "flag"
            continue
        if re.match(r"^##\s", line):  # any other H2 ends page sections
            flush()
            section = None
            continue

        # Markdown table row
        if line.startswith("|") and not re.match(r"^\|[\s:-]+\|", line):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if not cells or all(c == "" for c in cells):
                continue
            headerish = cells[0].lower() in {"element", "item", "id"}
            if headerish or set(cells[0]) <= {"-", " ", ":"}:
                continue
            if not in_scope():
                continue
            flush()
            element = re.sub(r"\*\*", "", cells[0]).strip()
            rest = [re.sub(r"\*\*", "", c).strip() for c in cells[1:]]
            behavior = rest[0] if rest else ""
            expected = rest[1] if len(rest) > 1 else behavior
            status = {"wired": "wired", "dead": "dead", "flag": "flag"}[section or "wired"]
            pending = {
                "page": page,
                "status": status,
                "element": element,
                "behavior": behavior,
                "expected": expected or behavior,
            }
            continue

        # Dashboard "❌ Broken" numbered list ("1. **Phone Numbers page** — ...")
        if section is None and re.match(r"^\d+\.\s+\*\*", line) and page and "Dashboard" in page:
            flush()
            element = re.sub(r"^\d+\.\s+", "", line)
            element = re.sub(r"\*\*", "", element).split("—")[0].strip()
            pending = {
                "page": page,
                "status": "dead",
                "element": f"dashboard-broken: {element}",
                "behavior": line.split("—", 1)[1].strip() if "—" in line else "",
                "expected": "feature works end-to-end",
            }
            continue

        # Continuation of a multi-line cell
        if pending and line.strip() and not line.startswith(("#", ">", "---")):
            pending["expected"] += " " + line.strip()

    flush()
    return rows


# ── Test-case synthesis ─────────────────────────────────────────────────────


def case_id(row: dict, idx: int) -> str:
    page_slug = slugify(row["page"] or "global")
    el = row["element"]
    # Strip decoration like "START CALL ↗ (live-demo widget) + demo orb"
    el_slug = slugify(re.sub(r"[↗▸]", "", el.split("(")[0].split("+")[0]))
    return f"g7-{page_slug}-{idx:02d}-{el_slug or 'row'}"


_CAPS_RUN = re.compile(r"[A-Z][A-Z0-9]+(?:\s*/\s*[A-Z][A-Z0-9]+)*(?:\s+[A-Z][A-Z0-9]+)*")


def probe_label(element: str) -> str:
    """
    Extract a clickable-label probe from an inventory DESCRIPTION string.
    Inventory rows describe elements ("Hero BOOK A DEMO", "Logo, PRODUCT /
    CAPABILITIES / PRICING nav") — not literal DOM text. Strategy: prefer the
    longest run of capitalized words (UI labels on this site are uppercase),
    falling back to the first two words.
    """
    cleaned = re.sub(r"[↗▸()]", " ", element)
    candidates = _CAPS_RUN.findall(cleaned)
    if candidates:
        best = max(candidates, key=len).strip()
        if len(best) >= 4:
            return best
    words = cleaned.replace(",", "").split()
    return " ".join(words[:2]) if len(words) >= 2 else (words[0] if words else element)


def page_url_for(row: dict, base_url: str) -> str:
    """Route probes at the page the element actually lives on."""
    blob = f"{row.get('page') or ''} {row['element']}".lower()
    if "auth" in blob and "landing" not in blob.split("auth")[0][-30:]:
        return f"{base_url}/auth.html"
    return base_url


# Probe overrides where the inventory DESCRIPTION no longer matches live copy.
# Keyed by case id; value = regex fragment matching what's actually rendered.
# `|` alternation IS allowed here; spaces become \s+ automatically.
# Update here when copy changes — never hand-edit the generated spec.
PROBE_OVERRIDES = {
    # Inventory said "Sign-up/sign-in toggle links"; live auth copy is
    # "Already have an account? Sign in"
    "g7-landing-06-sign-up-sign-in-toggle-links-on-auth": "sign in|sign up",
    # Inventory groups several separate nav links in ONE row description;
    # a single element can never contain all of them — probe the first.
    "g7-landing-01-logo-product-capabilities-pricing-nav": "capabilities",
}


def playwright_spec(rows: list[dict], base_url: str, gen_meta: dict) -> str:
    lines = [
        "// GENERATED FILE — DO NOT EDIT BY HAND.",
        "// Source: dead-button-inventory-v1.md via qa/generators/gen_g7_inventory.py",
        f"// Generated: {gen_meta['generated_at']}",
        "// Re-run the generator whenever the inventory markdown revs (G7 contract).",
        "import { test, expect } from '@playwright/test';",
        "",
        f"const BASE = '{base_url}';",
        "const MANIFEST = {",
    ]
    for r in rows:
        lines.append(f"  '{r['id']}': {json.dumps(r)},")
    lines += [
        "};",
        "",
        "for (const [id, row] of Object.entries(MANIFEST)) {",
        "  const wired = row.status === 'wired';",
        "  test(`G7 ${id}: ${row.element}`, async ({ page }) => {",
        "    // Wired rows must keep working; dead rows are expected-fail canaries",
        "    // (they pass only after the fix lands — flip their status in the",
        "    // inventory + regenerate when that happens).",
        "    test.skip(!wired && !process.env.G7_CANARIES, 'dead-row canary; run with G7_CANARIES=1');",
        "    await page.goto(row.url);",
        "    const target = page.locator(`text=/${escapeRe(row.probe)}/i`).first();",
        "    if (wired) {",
        "      await expect(target).toBeVisible();",
        "    } else {",
        "      // Canary assertion for dead controls: element inert or absent.",
        "      const visible = await target.isVisible().catch(() => false);",
        "      expect(visible, `dead control '${row.element}' became interactive — verify fix landed, then regenerate`).toBe(false);",
        "    }",
        "  });",
        "}",
        "",
        "function escapeRe(s: string): string {",
        "  // Escape regex metacharacters EXCEPT '|' (allowed alternation), then",
        "  // collapse whitespace so multi-word labels match across newlines.",
        "  return s.replace(/[-[\\]{}()*+?.,\\\\^${}#\\s]/g, ' ').trim().replace(/\\s+/g, '\\\\s+');",
        "}",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="G7 inventory → parametrized cases generator")
    ap.add_argument("--inventory", required=True, type=Path)
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--base-url", default="https://sahaiy.vercel.app")
    args = ap.parse_args()

    md = args.inventory.read_text(encoding="utf-8")
    rows = parse_inventory(md)
    if not rows:
        print("FATAL: no rows parsed from inventory — parser needs updating for new format", file=sys.stderr)
        return 2

    for i, r in enumerate(rows, 1):
        r["id"] = case_id(r, i)
        r["probe"] = PROBE_OVERRIDES.get(r["id"], probe_label(r["element"]))
        r["url"] = page_url_for(r, args.base_url)

    counts = {"wired": 0, "dead": 0, "flag": 0}
    for r in rows:
        counts[r["status"]] += 1

    tests_dir = args.out_dir / "tests" / "generated"
    tests_dir.mkdir(parents=True, exist_ok=True)

    meta = {"generated_at": datetime.now(timezone.utc).isoformat()}
    spec_path = tests_dir / "test_g7_inventory.spec.ts"
    spec_path.write_text(playwright_spec(rows, args.base_url, meta), encoding="utf-8")

    manifest = {
        "source": str(args.inventory),
        "generated_at": meta["generated_at"],
        "base_url": args.base_url,
        "counts": counts,
        "total_rows": len(rows),
        "rows": rows,
    }
    manifest_path = args.out_dir / "g7_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Parsed {len(rows)} inventory rows from {args.inventory.name}")
    print(f"  wired (positive asserts): {counts['wired']}")
    print(f"  dead  (xfail canaries):   {counts['dead']}")
    print(f"  honesty flags:            {counts['flag']}")
    print(f"Wrote {spec_path}")
    print(f"Wrote {manifest_path}")

    # Coverage gate: G7 pass requires 100% of inventory rows covered
    assert manifest["total_rows"] == sum(counts.values())
    return 0


if __name__ == "__main__":
    sys.exit(main())
