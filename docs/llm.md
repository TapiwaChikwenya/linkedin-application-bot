# Local Llama sidecar

## Feature purpose

Let a local Llama model running in Ollama read leftover Easy Apply fields and
suggest short answers from resume facts, config bootstrap maps, and
**operator-approved Q&A memory**. The Selenium worker still clicks and types.
The model does not talk to LinkedIn and does not leave this machine.

This is **retrieve-then-generate**, not training. Approving answers in the
dashboard grows a SQLite lookup table. It does **not** fine-tune Ollama, apply
LoRA, export a GGUF, or change `llama3.2` weights.

## Scope

In scope: Ollama on `127.0.0.1:11434`, shadow-DOM field collection, JSON answers
for Yes/No, years, short text, and native selects, SQLite retrieval of approved
rows, pending seed questions generated for the operator to approve, and a
**job-fit gate** that scores listings from search-card or job-page text.

Out of scope: cloud LLMs, CAPTCHA solving, inventing employer essays, submitting
answers that are not grounded in local facts, fine-tuning, LoRA, and any form of
model training. Operator review is `GET/POST /api/questions*`. Approved rows are
mappings for retrieval, not training data.

## Inputs and outputs

| Input | Purpose |
|---|---|
| `LINKEDIN_LLM` | `auto` (default), `ollama`, or `off` |
| `OLLAMA_HOST` | Default `http://127.0.0.1:11434` |
| `OLLAMA_MODEL` | Default `llama3.2`. Use `llama3.2-vision` to send a screenshot. Dashboard **Settings** or SQLite `operator_settings.ollama_model` overrides this when set |
| `LINKEDIN_APPLICANT_SUMMARY` | Optional extra truthful facts for short text questions |
| `LINKEDIN_JOB_FIT_THRESHOLD` | Skip the listing when Llama fit is below this. Default `0.55` |
| Resume PDF | Text extract for leftover generation and seed-tool hints |
| `config.py` `keywords` / `years_experience` keys | Compact fit-profile skills and search keywords |
| `config.py` `years_experience` / `yes_no_answers` | **Bootstrap keyword maps**, used only when no approved row matches |
| Approved dashboard answers | Operator-confirmed mappings in SQLite `questions` |

Outputs: filled form controls, plus `skipped_fit` / `needs_review` job rows.
If Ollama is down, the worker still uses approved memory, then bootstrap maps,
and **does not skip the whole run**. The fit gate is skipped; leftover required
fields still skip that one job rather than guessing.

## Dependencies

- Ollama installed locally
- A pulled model such as `llama3.2`
- Optional `pypdf` for resume text (installed with the project)

## Fill and memory order

Worker fill order on each Easy Apply step:

1. **Approved RAG memory** — `Store.retrieve_approved_answers` for the current
   field. Exact `normalized_question` wins; otherwise a conservative token overlap
   is used so "SQL years" does not match "Python years".
2. **Mapped config bootstrap** — specific skill years and specific Yes/No keyword
   rules in `config.py`. These are hardcoded shortcuts, not learned weights.
3. **Ollama generation** — leftover unanswered fields. The prompt includes a
   compact `approved_answers` slice plus resume/config facts.
4. **Skip the job** — if a **required** field still has no approved, mapped, or
   LLM answer, the worker dismisses the modal and records `needs_review`. That is
   smart skip, not "fill everything". Optional fields may stay empty and pending.

Generic catch-alls are not used: there is no `years_experience["default"]`
fallback, and rules such as `experience → Yes` are ignored even if they reappear
in config. Unmatched questions stay empty rather than guessing "9 years" or "Yes".

```mermaid
flowchart TD
  A[Easy Apply modal opens] --> B[Walk light DOM and open shadow roots]
  B --> C[Fill phone, city, resume]
  C --> D{Approved SQLite answer for this field?}
  D -->|yes| E[Type approved value]
  D -->|no| F{Specific config map?}
  F -->|yes| G[Type bootstrap map]
  F -->|no| H{Ollama ready?}
  H -->|no| I[Leave empty and capture pending]
  H -->|yes| J[Retrieve matching approved Q and A]
  J --> K[Generate from facts plus approved_answers]
  K --> L[Validate ID, field type, option, confidence]
  L --> M[Apply grounded JSON answers]
  E --> N[Upsert observed fields]
  G --> N
  M --> N
  I --> N
  N --> P{Required field still empty?}
  P -->|yes| Q[Skip job needs_review]
  P -->|no| O[Click Next or Submit]
```

## Job-fit scoring

### Feature purpose

Use the same local Llama sidecar to skip junk listings after deterministic
title/company/Easy Apply filters pass. The model returns a grounded fit score
from applicant keywords, `years_experience` skill keys, and approved facts.
It does not invent experience and it is not fine-tuned.

### Scope

In scope: temperature-0 JSON `{fit, reason, decision}` against a compact listing
payload, skip when `fit < 0.55` or `decision=skip`, persist `skipped_fit` with
the skip reason and optional `jobs.fit_score`, and a `last_fit` field on
`GET /api/status`. Prefer search-card snippet so junk is not opened; if that
snippet is shorter than 80 characters, score the job-page description **before**
Easy Apply is clicked.

Out of scope: fine-tuning, cloud scoring, blocking the run when Ollama is down,
and restyling the dashboard.

### Inputs and outputs

| Input | Purpose |
|---|---|
| Search-card `{title, company, snippet}` | Preferred. Sibling owns pre-click extract |
| Job-page description excerpt | Fallback when the card snippet is too short |
| `config.keywords` | Applicant search keywords |
| `years_experience` keys | Top skills (catchall `default` omitted) |
| Approved SQLite facts | Compact RAG slice, not the full history |
| `LINKEDIN_JOB_FIT_THRESHOLD` | Default `0.55` |

Outputs: log lines `Job fit llama=0.82 apply` or
`Job fit llama=0.21 skip: staffing recruiter`. Skip rows use status
`skipped_fit`. Apply continues and stores `fit_score` on the later job row.

### Dependencies

Same local Ollama process as form RAG. `linkedin_easy_apply.job_fit` calls
`llm.generate_json` with a fit schema. Search-card HTML parsing stays in
`job_card.py`.

### Functional flow

```mermaid
flowchart TD
  A[Search card] --> B{Title/company/Easy Apply filters}
  B -->|fail| C[skipped_filter / already_applied / no Easy Apply]
  B -->|pass| D{Card snippet long enough?}
  D -->|yes| E[Llama fit JSON temperature 0]
  D -->|no| F[Open job page description]
  F --> E
  E --> G{Ollama ready?}
  G -->|no| H[Log skip-gate and continue]
  G -->|yes| I{fit less than 0.55 or decision skip?}
  I -->|yes| J[skipped_fit plus reason]
  I -->|no| K[Click Easy Apply]
  K --> L[Approved then mapped then Llama fill]
  L --> M{Required leftover?}
  M -->|yes| N[needs_review skip]
  M -->|no| O[Submit]
```

### Architecture

```mermaid
flowchart LR
  Card[Search card extract] --> Gate[job_fit.evaluate_listing_fit]
  Page[Job description excerpt] --> Gate
  Keywords[keywords plus skill keys] --> Gate
  Approved[Approved SQLite facts] --> Gate
  Gate --> Ollama[Ollama llama3.2 JSON schema]
  Ollama --> Gate
  Gate -->|skip| Jobs[(jobs.skipped_fit)]
  Gate -->|apply| Modal[Easy Apply fill RAG]
  Modal --> Approved
```

Fit scoring and form fill share Ollama and approved memory. They do not share
prompts. Fit never receives resume PDF text. Form fill still receives resume
facts for leftover short answers.

### Failure paths

- Ollama down or `LINKEDIN_LLM=off`: log `Job fit llama skipped: Ollama not ready`
  and continue. The run does not stop.
- Invalid JSON or missing `fit`: treat as gate skipped, do not skip the job.
- Short card snippet: `needs_description`; worker scores after the description
  is on the page, still before Easy Apply.
- Required form field with no grounded answer: skip that job as `needs_review`.

### Security considerations

The fit prompt contains only title, company, a short snippet, keywords, skill
keys, and approved Q&A. Cookies, passwords, and the resume file are not sent.
Keep `OLLAMA_HOST` on localhost.

### Performance considerations

Fit uses `num_predict=160` and a 25s timeout so a hung generate cannot stall
the run the way a 90s form fill might. Human pacing remains the larger delay.
Scoring from card text avoids opening junk pages when the sibling extract
already has a usable snippet.

Weights stay frozen. Growing memory still means approving Questions rows.

## Architecture

```mermaid
flowchart LR
  Worker[Selenium worker] --> Page[Firefox job page]
  Worker --> Snapshot[Shadow-DOM snapshot]
  Worker --> Facts[Resume and config facts]
  Snapshot --> Ollama[Ollama llama3.2]
  Facts --> Ollama
  Review[Approved questions SQLite] --> Worker
  Review --> Facts
  Ollama --> Worker
  Worker --> Page
```

The browser session never includes the model. Ollama only receives form text,
optional screenshot bytes, and the local applicant fact bundle. Cookies and
passwords are not sent. Weights stay frozen.

## What "memory" means

Memory is reviewed SQLite retrieval (RAG over your approved Q&A). It is not:

- Fine-tuning
- LoRA
- Continued pretraining
- Updating Ollama model files

Growing memory means approving more rows. The next matching form looks those
rows up. Llama still generates only for leftovers, and must copy an approved
value when a retrieved row matches.

### Two ways questions enter the catalog

| Path | How it gets there | What you do |
|---|---|---|
| LinkedIn capture | Every Easy Apply step upserts visible screening fields | Approve (or reject) the real wording you just saw |
| Seed generator | Dashboard **Generate seed questions** inserts common Easy Apply patterns, one years prompt per `years_experience` skill, plus resume-tool prompts | Same review: type or confirm an answer, then Approve |

Both paths insert **pending** rows. Seeds never write `approved_value`. Until you
approve a row, it is not in the retrieval pool and cannot fill a live form via RAG.

## Screening question memory

This SQLite catalog is **memory and retrieval, not model training**. Captured
rows let you approve answers later and let the worker and Llama look up those
approved answers. Nothing here fine-tunes Ollama or leaves this machine.

### Feature purpose

Persist every Easy Apply screening question the worker sees, plus optional seed
prompts, so unanswered prompts can be answered by the operator and reused on
later applications.

### Scope

In scope: upsert into `data/assistant.sqlite` `questions`, operator approval via
the Questions page / `Store.approve_answer` / CLI, approved-row reads for
retrieval, and pending seed generation.

Out of scope: Ollama pulls, fine-tuning, and starting LinkedIn from the
dashboard.

### Inputs and outputs

| Input | Purpose |
|---|---|
| Form fields from `fillKnownFields` | Question text, kind, options, required, mapped/LLM/unanswered/approved tags |
| Seed generator | Common Easy Apply, skill-year, sponsorship, work-auth, relocation, resume-tool prompts |
| `job_id`, company, title | Last job where a LinkedIn-captured question was seen |
| Operator approve in UI or `--approve-question ID --answer TEXT` | Promotes a pending row into the retrieval pool |

Outputs: one `questions` row per unique `(normalized_question, field_kind,
option_set_hash)`. Repeats increment `seen_count` and keep the first raw wording.
JSONL `data/question_capture.jsonl` remains a value-free audit log beside this
table. Seed re-runs skip existing identities instead of flooding duplicates.

### Dependencies

- Worker `Store` (same SQLite file as jobs/events)
- `linkedin_easy_apply.question_capture.remember_questions`
- `linkedin_easy_apply.question_generator.generate_seed_questions`
- Dashboard Questions page; optional CLI

### Functional flow

```mermaid
flowchart TD
  A[Easy Apply step] --> B[collect_form_state]
  B --> C[Approved memory fill]
  C --> D[Mapped config bootstrap]
  D --> E[fill_llm_fields leftover fields]
  E --> F[upsert questions row]
  F --> G{Operator later}
  G --> H[approve_answer]
  H --> I[Retrieval reads approved rows]
  J[Generate seed questions] --> K[Pending seed rows]
  K --> G
```

Capture is triggered at the end of each modal step in `Linkedin.fillKnownFields`
after approved fills, mapped fills, and `fill_llm_fields`. `source` is
`mapped`, `llm`, `unanswered`, `manual`, or `seed`. File and password controls
are skipped. Email/phone `proposed_value` is stored as `[redacted-email]` /
`[redacted-phone]`. Resume text and page HTML are never written to this table.

### Architecture

```mermaid
flowchart LR
  Worker[linkedin.py fillKnownFields] --> Capture[question_capture.remember_questions]
  Capture --> SQLite[(questions table)]
  Seeds[question_generator] --> SQLite
  CLI[linkedin-easy-apply --pending-questions] --> SQLite
  UI[Questions page Approve] --> SQLite
  Retrieval[approved rows] --> Worker
  Retrieval --> Llama[Ollama prompt facts]
```

### Schema and migration

The capture schema is the source of truth. New databases run `QUESTIONS_DDL`.
Existing capture tables are never rebuilt: missing columns such as `reuse_count`
are added with `ALTER TABLE`. Identity stays unique on
`(normalized_question, field_kind, option_set_hash)`. Older databases that still
CHECK `source` without `seed` store generator rows as `source=manual` with
`provenance=seed`.

Columns: `id`, `normalized_question`, `raw_question`, `field_kind`, `options_json`,
`option_set_hash`, `required`, `job_id`, `company`, `title`, `source`
(`mapped|llm|unanswered|manual|seed`), `proposed_value`, `approved_value`,
`approval_status` (`pending|approved|rejected`), `provenance`, `confidence`,
`seen_count`, `reuse_count`, `last_seen_at`, `created_at`, plus classification
columns `cleaned_question`, `classified_kind`, `answer_shape`, `cluster_key`,
`merged_into_id`, `classification_status`, `not_user_answerable`. Unique index:
`idx_questions_identity`. Display title is `cleaned_question`; identity stays on
the captured `raw_question`.

### Failure paths

- No `Store` on the worker (unit tests): capture is skipped.
- Empty or file/password fields: not inserted.
- Unknown question id on `--approve-question`: CLI exits 2.
- Approved rows keep `approved_value` when the same prompt is seen again.
- Seed generation skips an identity that already exists, including LinkedIn-captured rows.

### Security considerations

Do not store passwords, resume text, or HTML snapshots in `questions`. Redact
email and phone in `proposed_value`. Live events still log counts only. Seed
`proposed_value` hints copied from config are still pending until you approve
them.

### Performance considerations

One indexed upsert per visible field per modal step. Seed generation is a single
pass over a small catalog and the unique identity index. Identity lookup is a
unique index, not a table scan.

### How to review and approve

In the dashboard: open **Questions**, filter to pending, type or confirm the
answer, click **Approve**. That row becomes retrieval memory.

```powershell
linkedin-easy-apply --pending-questions
linkedin-easy-apply --approve-question 12 --answer Yes
```

Retrieval calls `Store.list_approved_questions()` then matches the current
unanswered fields (exact normalized question, then conservative token overlap)
via `Store.retrieve_approved_answers(questions)`. That compact slice is injected
into the local prompt as `approved_answers` with `source_id` values. Direct
worker fills use the same lookup **before** config maps.

## Seed questions

### Feature purpose

Grow the review queue with likely Easy Apply prompts **before** LinkedIn shows
them, so you can approve a truthful library. This is catalog growth, not
training.

### Scope

In scope: pending rows for sponsorship, work authorization, relocation,
common workplace Yes/No prompts, one years question per `years_experience`
key (never the removed `default` catch-all), and extra tool prompts scanned
from resume text when available.

Out of scope: auto-approval, writing `approved_value`, calling Ollama, or
submitting LinkedIn applications.

### Inputs and outputs

| Input | Purpose |
|---|---|
| `config.py` skill years | One pending "How many years of {skill}…" prompt per key |
| Common Easy Apply list | Sponsorship, work auth, relocate, on-site/hybrid/remote, and similar |
| Resume extract | Additional tool year / "do you have experience with {tool}" prompts |

Outputs: `{ inserted, skipped, candidates, source }` from
`POST /api/questions/generate-seeds`. Re-running skips existing
`(normalized_question, field_kind, option_set_hash)` rows.

### Functional flow

```mermaid
flowchart TD
  A[Questions page Generate seed questions] --> B[build_seed_questions]
  B --> C{Identity already in SQLite?}
  C -->|yes| D[Skip]
  C -->|no| E[Insert pending source=seed]
  E --> F[Operator approves in UI]
  F --> G[Approved retrieval pool]
```

### Failure paths

- Duplicate identity: counted in `skipped`, no second row.
- Missing resume: skill-year and common patterns still insert; resume-tool extras are omitted.
- Existing DBs that reject `source=seed`: rows store as `manual` with `provenance=seed`.

### Security considerations

Seeds are local SQLite rows. They do not upload the resume. `proposed_value` may
copy a config hint (for example SQL years) but `approved_value` stays empty.
Sensitive live answers still require operator approval before retrieval.

### Performance considerations

Generation is one indexed existence check per catalog item. Re-runs are
idempotent and do not bump `seen_count` for existing identities.

## Question classification (normalize before answering)

### Feature purpose

Correct poorly typed LinkedIn captures **before** they appear in the Questions
queue. Email must show as an email/text field, years as a number, and Yes/No as
yes/no — not a generic string. Wording is cleaned. Duplicates are merged. This
is classification only. It does **not** invent answers and does **not**
fine-tune Ollama.

### Scope

In scope: heuristic classification on every `upsert_question` (LinkedIn capture
or seeds), optional Ollama wording polish from the dashboard, duplicate merge
by cluster, and hiding file/resume plus merged rows from `list_questions`.

Out of scope: fine-tuning, answering the field, starting Easy Apply, or blocking
the Selenium worker on Llama.

### Inputs and outputs

| Input | Purpose |
|---|---|
| `raw_question`, `field_kind`, `options` | Captured LinkedIn or seed prompt |
| Heuristic rules | Fast kind + cluster without Ollama |
| Ollama JSON (optional) | `{cleaned_question, kind, answer_shape, duplicate_of_id?, confidence}` |

Outputs written on the same SQLite row: `cleaned_question` (display title),
`classified_kind`, `answer_shape`. `raw_question` is unchanged. Duplicates get
`merged_into_id` and `classification_status=duplicate`. File/resume rows are
`not_user_answerable`.

Allowed `kind`: `email`, `tel`, `number`, `text`, `select`, `textarea`,
`yes_no`, `file`, `date`, `unknown`. Email uses `answer_shape=text`.

### Classification rules

- **email / e-mail / email address** (or options that are `[email]`,
  `Select an option`, `email addresses`, or a single email-looking token) →
  `email`, display `Email`, cluster `email`, `answer_shape=text`. LinkedIn's
  native `select` kind is never kept for these rows.
- **phone / mobile / telephone** (or `[phone]` placeholder options) → `tel`,
  display `Phone`, cluster `tel`
- Repeated capture labels are collapsed (`Email address` ×3 → `Email`)
- **city / location** with placeholder-only options → `text`, not `select`
- **years of experience / how many years** → `number` (not “years of age”)
- **yes/no options, authorized, sponsorship, willing to** → `yes_no`
- **resume / cv / curriculum vitae** → `file`, hidden from the pending queue
- Everything else keeps light whitespace cleanup and the captured kind when it
  is already one of the allowed kinds and the option list has real choices

Email variants such as “Email address”, “Email address” repeated, and “Email”
share cluster `email` and merge into one visible row (`seen_count` summed).
`GET /api/questions` and `classify-pending` backfill rows that were already
stored as `select`.

### Dependencies

Heuristics: none. Ollama polish: local `llama3.2` on `127.0.0.1:11434`, same as
the fill sidecar. Temperature 0. Prompt forbids inventing an answer.

### Functional flow

```mermaid
flowchart TD
  A[upsert_question capture or seed] --> B[heuristic_classify]
  B --> C[Write cleaned_question classified_kind answer_shape]
  C --> D[Merge cluster duplicates]
  D --> E[Worker returns immediately]
  F[Questions page] --> G[POST /api/questions/classify-pending]
  G --> H{Ollama ready?}
  H -->|no| I[Heuristics already applied]
  H -->|yes| J[JSON classify max 8 timeout 8s]
  J --> K[Refresh list with cleaned titles]
```

Worker upsert never calls Ollama. `GET /api/questions` is not a pure read:
it calls `repair_contact_classifications` on the **live** `assistant.sqlite`
so LinkedIn email/phone placeholder-selects become `email`/`tel` text rows
before the list is returned. **Refresh the Questions page** after a capture
or after upgrading to see cleaned title **Email** and an `<input type="email">`
instead of a select of `[email]`. `classify-pending` still polishes leftover
wording (max N, timeout per question) without blocking the worker.

### Architecture summary

`question_classifier.classify_question_with_llm(row)` is background-safe: no
Selenium, no LinkedIn. Heuristic first. Llama optional. Persistence is
`Store.save_question_classification` plus `merge_duplicate_question`.

### Failure paths

- Ollama down or `LINKEDIN_LLM=off`: email/phone/years still classify; UI works.
- Llama returns an answer-like value (Yes/No, a bare number, an email): discarded;
  heuristic wording is kept.
- Unknown `kind` from Llama: heuristic kind is kept.
- Duplicate merge never deletes rows; `list_questions` hides them.
- `classify-pending` SQLite busy: HTTP 503. Easy Apply is not started.

### Security considerations

Classification JSON has no `value`/`answer` field. Email/phone `proposed_value`
is re-redacted when kind becomes `email`/`tel`. File/resume rows are not shown
for answering. No fine-tune, no cloud LLM, no LinkedIn calls.

### Performance considerations

Heuristic classify is regex-only and runs inside upsert (target well under
200ms). Ollama classify is dashboard-only, `timeout=8s`, `num_predict=120`,
max 8 rows per `classify-pending` request so the worker never waits on Llama.

## Answer retrieval (how Llama remembers)

Memory is a reviewed SQLite table. We are not training, fine-tuning, or applying
LoRA to Ollama. The frozen `llama3.2` weights stay unchanged.

Retrieve-then-generate:

1. Approved SQLite answers fill first when they match the live field.
2. Specific `config.py` maps fill next. They are a bootstrap, not a trained
   policy. Generic experience→Yes and default→N years are omitted.
3. For leftover fields, `Store.retrieve_approved_answers` keeps rows that match
   the current questions. Exact `normalized_question` wins; otherwise a
   high-precision token overlap is used.
4. Only that compact slice (default 12) is injected as `approved_answers` with
   `source_id`. `applicant_facts["approved_answers"]` carries the same slice,
   never the whole history.
5. The prompt requires: use `approved_answers` when present; omit on conflict or
   when the answer is not in facts/approved list; return `confidence` and
   `source_ids`. Existing `q0` question ids stay.
6. Typed LLM values are tagged `_llm_value` and upserted at the end of
   `fillKnownFields` as `source=llm`, `approval_status=pending`. Email/phone
   `proposed_value` is redacted. Sponsorship, authorization, salary, disability,
   race, gender, veteran, and clearance are never auto-approved.
7. If the application later succeeds and the answer was already approved or
   exact-mapped, `reuse_count` increments. LLM proposals stay pending.

How to grow it: approve pending rows (captured or seeded) from the dashboard.
The next matching form retrieves those rows automatically.

## Failure paths

- Ollama not installed or not running: worker continues with approved memory and
  mapped bootstrap answers and logs `Job fit llama skipped: Ollama not ready`
  plus `Ollama skipped: local model is not ready` on form fill. The run does not
  stop.
- Model missing: Ollama is marked not ready and reports the required `ollama pull`
  command; no generation request is attempted.
- The model returns an unknown question ID, a non-numeric value for a number field,
  or a value outside a select/radio option list: that answer is discarded.
- The model omits a question or returns an essay longer than 400 characters: that field
  is left empty. If it was **required**, the job is skipped as `needs_review`.
- Fit JSON is invalid: the gate is skipped and Easy Apply may still run.
- Closed shadow roots are walked with WebDriver `element.shadow_root`. A vision model plus
  screenshot remains a fallback when a nested closed tree still cannot be serialized.

Live-run events expose only counts and state: whether Ollama was invoked or skipped and
why, plus accepted/applied/rejected answer totals. Questions, generated values, resume
text, and contact values are intentionally omitted.

## Security considerations

Keep `LINKEDIN_LLM=auto` pointed at localhost. Do not point `OLLAMA_HOST` at a remote
URL that would upload the resume. The sidecar never receives `LINKEDIN_PASSWORD`.
Operator-approved answers stay on this machine and are not used to fine-tune the model.

## Performance considerations

Each unanswered form step can add a few seconds while Llama generates JSON. Human pacing
is still the larger delay. Prefer `llama3.2` (text) over vision unless selectors keep
missing the modal. Approved memory fills are a SQLite lookup and are cheap compared
with generation.

## How to run

1. Install Ollama from https://ollama.com/download once.
2. Open `Start-Dashboard.cmd` and click **Start run**. That starts `ollama serve` and
   pulls `llama3.2` if it is missing. You do not need `Start-Ollama.cmd`.
3. The header LLM chip should say ready. `linkedin-easy-apply --check` is optional.
4. On **Questions**, optionally click **Generate seed questions**, then approve
   answers you want in RAG memory.
5. To use another local tag, set `OLLAMA_MODEL` in `.env` (for example
   `llama3.2-vision`) or pick/pull a tag on dashboard **Settings**. That writes
   SQLite `operator_settings.ollama_model`; `.env` is the fallback.

Optional vision:

```powershell
ollama pull llama3.2-vision
```

Set `OLLAMA_MODEL=llama3.2-vision` in `.env`. The worker then attaches a screenshot of
the current form.
