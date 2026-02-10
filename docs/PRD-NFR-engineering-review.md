# PRD & NFR Engineering Review

**Scope:** PRD (§5 Functional, §6 Non-functional), NFR-implementation-order.md, and codebase alignment.  
**Purpose:** Prioritize missing features, necessary fixes, and doc/code inconsistencies.

---

## 1. Executive summary

- **PRD/NFR coverage:** All listed NFRs (NFR1–NFR10) are implemented and covered by tests in `test/nfr/`. Functional requirements F1–F27 are implemented; success note (F9) and error note (F10) content match the PRD.
- **Config precedence:** Documented as “env > YAML > defaults” and **correct in code** via `settings_customise_sources` (env first). The NFR doc contains an outdated note that suggests otherwise and should be fixed.
- **Gaps:** (1) NFR doc note on config precedence is wrong. (2) NFR8 test’s required-docs list is a subset of what the PRD describes. (3) Security-review.md still says “400” for ingest schema rejection (actual: 422). (4) Optional: lockfile / pip-audit HIGH (L1) is out of scope for PRD but listed in security-review as open.

No P0 functional or non-functional work is missing. Remaining items are **documentation accuracy**, **test/doc alignment**, and **optional hardening**.

---

## 2. Prioritized fixes and missing items

### P1 – Necessary (doc correctness / single source of truth)

| # | Item | Location | Problem | Fix |
|---|------|----------|---------|-----|
| 1 | **NFR6 config precedence note** | NFR-implementation-order.md §1 table, NFR6 row | Note says “pydantic-settings merge order is init kwargs then env then defaults” and “verify whether env overrides YAML”. In fact `Settings.settings_customise_sources` puts **env first**, so env does override YAML. The note is misleading. | Replace the note with: “Precedence env > YAML > defaults is enforced via `settings_customise_sources` (env, flat env, init). Verified by `test/unit/test_config.py::test_env_overrides_yaml`.” |
| 2 | **M2 response code in security-review** | security-review.md §4 Step 2.2 | Remediation says “invalid structure returns **400**”. Implementation returns **422** (FastAPI validation error). API and operations docs correctly say 422. | In security-review.md step 2.2 and verification column, change “400” to “422” (and “payload missing ticket id 400” → “422”). |

**Why P1:** Wrong docs cause wrong mental model and wrong test expectations (e.g. someone might add a test expecting 400 for invalid body).

---

### P2 – Recommended (consistency and traceability)

| # | Item | Location | Problem | Fix |
|---|------|----------|---------|-----|
| 3 | **NFR8 required docs vs PRD** | test/nfr/test_nfr8_docs.py | Test requires: 00, 01, 02, 04, 07, 08, 09, config-reference. PRD NFR8 says “path policy, **signing/TSA**, storage, operations, and security” and “Document Zammad setup”. So **06-signing**, **03-data-model**, and **api.md** are implied by PRD but not in the test. | Either: (a) Add `06-signing-and-timestamp.md` and `api.md` to the `required` list in `test_nfr8_key_docs_exist`, or (b) Add a short comment in the test that the list is the minimal set and PRD also references 03, 05, 06, api. Prefer (a) for signing and api so “signing/TSA” and the HTTP contract are covered by NFR8. |
| 4 | **Optional hardening (L1) in NFR doc** | NFR-implementation-order.md §5 | Security-review L1 (lockfile, pip-audit HIGH) is open. NFR optional-hardening table doesn’t mention it. | Add one row: “4 | L1 (security-review): lockfile + pip-audit fail on HIGH | See security-review.md §4 Step 3.1.” So engineers see it when reading “optional hardening” and don’t assume PRD requires it. |

**Why P2:** NFR8 test should align with what “document signing/TSA and operations” means; optional hardening should reference the one remaining open security-review item.

---

### P3 – Nice to have

| # | Item | Location | Problem | Fix |
|---|------|----------|---------|-----|
| 5 | **PRD §8.1 “as implemented”** | PRD.md §8.1 | “All P0 and P1 items above that are implemented” is vague. | Add one sentence: “Verification: dedicated NFR tests in `test/nfr/` and integration/unit coverage; see [NFR implementation order](NFR-implementation-order.md).” |
| 6 | **NFR8 test: 03, 05, faq** | test_nfr8_docs.py | 03-data-model, 05-pdf-rendering, faq.md are referenced from PRD/overview but not in NFR8 required list. | Optional: extend required list to include 03, 05, faq if you want strict “every linked doc exists” guarantee; otherwise leave as-is and rely on (b) in item 3. |

---

## 3. Verification done (no change needed)

- **Config load:** `load_settings()` loads dotenv then YAML then builds `Settings(**yaml_data)`. `Settings.settings_customise_sources` returns `(env_settings, _flat_env_settings_source, init_settings, ...)`, so env wins over init (YAML). Unit test `test_env_overrides_yaml` confirms.
- **Ingest schema:** `IngestBody` requires a resolvable positive ticket id; invalid/missing → FastAPI 422. Documented in api.md and 08-operations.
- **F9 / F10:** Success note includes path, filename, sidecar, size_bytes, sha256, request_id, delivery_id, time_utc; error note includes classification, error, action, request_id, delivery_id, time_utc. Matches PRD.
- **F12 archive_path:** `_parse_archive_path_segments` accepts string (split by `>`) or list of strings. Matches PRD “string with `>` or list of segments”.
- **NFR tests:** All 10 NFRs have a corresponding test module in `test/nfr/`; list in NFR-implementation-order §1 matches.

---

## 4. Out of scope for this review

- **L1 (lockfile / pip-audit HIGH):** Required by security-review, not by PRD or NFR. Treated as optional hardening; no PRD/NFR change needed unless the product decides to adopt it as an NFR.
- **Functional traceability matrix:** PRD does not require a formal F1–F27 ↔ test mapping; current coverage is sufficient for “verification” in §7.

---

## 5. Summary table

| Priority | Action |
|----------|--------|
| **P1** | Fix NFR6 note in NFR-implementation-order.md (config precedence). Fix M2 “400” → “422” in security-review.md. |
| **P2** | Align NFR8 test required docs with PRD (add 06-signing, api.md). Reference L1 in NFR optional hardening. |
| **P3** | Tighten PRD §8.1 with verification sentence. Optionally add 03, 05, faq to NFR8 test. |

No new features or code changes are required for PRD/NFR compliance; only documentation and one test list update.
