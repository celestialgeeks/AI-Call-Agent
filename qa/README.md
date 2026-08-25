# qa/ — Demo-Gate Test Suite (issue #9)

Executable suite built from `test-plan-demo-gate-v1.md`.

## Layout

```
qa/
├── conftest.py               # in-process real FastAPI app + stubbed externals
├── contract_tests/           # exact-shape conformance vs api-contracts v1 §1.1–1.5
├── security_tests/           # SEC-01..06 permanent regressions
├── ws_client/                # G4 primary path: Python WS text_input client + tests
├── generators/
│   └── gen_g7_inventory.py   # parses dead-button-inventory-v1.md → G7 cases
├── e2e/                      # Playwright suite (G1–G10 + generated G7 spec)
│   ├── playwright.config.ts
│   ├── tests/gates.spec.ts           # hand-written gates
│   ├── tests/generated/              # GENERATED from inventory — do not edit
│   └── g7_manifest.json              # generated inventory manifest
└── fixtures/                 # CSVs (valid / invalid / formula-injection), TTS sets
```

## Running

Python suites (contract + security + G4 WS) — against in-process backend:

```bash
python3 -m venv .venv && .venv/bin/pip install -r qa/requirements-qa.txt
.venv/bin/python -m pytest qa/contract_tests qa/security_tests qa/ws_client -v
```

Against a deployed backend instead of in-process:

```bash
SAHAIY_API_BASE=https://backend.example .venv/bin/python -m pytest qa/contract_tests -v
```

Playwright E2E:

```bash
cd qa/e2e && npm init -y && npm i -D @playwright/test && npx playwright install chromium
E2E_BASE_URL=https://sahaiy.vercel.app npx playwright test
G7_CANARIES=1 npx playwright test tests/generated   # include dead-row canaries
```

Regenerate G7 cases when the inventory revs (**required by the G7 contract**):

```bash
.venv/bin/python qa/generators/gen_g7_inventory.py \
  --inventory "/Users/shreyashsingh/my info/projects/sahaiy/frontend/dead-button-inventory-v1.md" \
  --out-dir qa/e2e --base-url https://sahaiy.vercel.app
```

## Feature flags

| Flag | Effect |
|---|---|
| `SAHAIY_JWT_ENFORCED=1` | activates SEC-01/02/03 hard assertions when @backend-eng's JWT dependency lands |
| `E2E_BASE_URL` | target host for Playwright |
| `E2E_CAMPAIGN_UI=1` | enables G3/G5 campaign-UI specs once outbound UI ships |
| `E2E_AUTH_EMAIL` / `E2E_AUTH_PASSWORD` | seeded test account for G9 round-trip |
| `G7_CANARIES=1` | runs dead-row canary cases (expected-fail until fixes land) |

## Sequencing (per issue #9)

Cases are written against the CURRENT body-user_id shape and run green now.
When the auth feature flag flips, SEC-01/02/03 go hard and the JWT-migration
note in api-contracts v1 becomes enforceable — flip `SAHAIY_JWT_ENFORCED` in CI.
