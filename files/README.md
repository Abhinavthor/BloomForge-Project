# BloomForge 🌱
**Dynamic Syllabus Parser & Bloom's Taxonomy Quiz Engine**
DevSpark Round 3 · Track 2: Academic & Exam Intelligence

Paste study material → an LLM generates a quiz tiered by Bloom's Taxonomy →
take it against a live countdown → download a report showing exactly which
cognitive tiers and topics are weak.

## Pipeline
```
notes → parser → LLM generator (strict JSON) → timed quiz → rule-based scorer → downloadable report
```

## Deliverables → features
| Required deliverable | Where it lives |
|----------------------|----------------|
| Document intake parser (notes / markdown / text) | `render_setup()` + `clean_text()` / `extract_pdf_text()` |
| LLM workflow: 5–10 questions tiered by Bloom's | `build_prompt()` + `generate_questions()` |
| Interactive quiz UI (live countdown, instant scoring) | `render_quiz()` + `score_quiz()` |
| Downloadable weakness/strength summary | `build_report_md()` + download button |

The three required tiers map to Bloom's cognitive levels:
Recall → Remember · Application → Understand/Apply · Code Tracing → Analyze.
The tier split is **enforced in code** (e.g. 9 questions → 3/3/3), not left to the model.

## Run locally
```bash
pip install -r requirements.txt
streamlit run app.py
```
The app opens at http://localhost:8501.

### Add your AI key
Pick a provider in the sidebar and provide its key one of two ways:

- Environment variable:
  ```bash
  export GEMINI_API_KEY="your-key"     # or OPENAI_API_KEY
  streamlit run app.py
  ```
- Or `.streamlit/secrets.toml`:
  ```toml
  GEMINI_API_KEY = "your-key"
  ```

Gemini (free tier) is the default. To use OpenAI, switch the provider in the sidebar.

### No key? Use Demo Mode
Click **Try Demo Mode** to run the full flow (quiz → timer → scoring → report)
with a built-in sample quiz. Handy if the API is rate-limited during judging.

### Model / provider
Change these constants at the top of `app.py`:
```python
DEFAULT_PROVIDER = "gemini"
GEMINI_MODEL = "gemini-2.0-flash"
OPENAI_MODEL = "gpt-4o-mini"
```

## Deploy (live hosted link)
1. Push this folder to a public GitHub repo.
2. Go to https://share.streamlit.io → New app → pick the repo → main file `app.py`.
3. In the app's **Settings → Secrets**, paste `GEMINI_API_KEY = "..."`.
4. Deploy. Share the resulting URL.

## Design robustness
- **Strict-JSON parsing** with fence-stripping, validation, and one auto-repair retry.
- **Deterministic scoring** (rule-based, not an LLM) — instant and easy to defend.
- **Timer degrades gracefully**: the deadline is stored server-side, so even if
  `streamlit-autorefresh` is missing the quiz still auto-submits when time is up.
- **Demo Mode** guarantees a working demo regardless of network/API state.

## Out of scope (by design, for the 90-min sprint)
User accounts, database persistence, OCR for scanned PDFs, free-text grading.
