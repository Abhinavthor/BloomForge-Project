# AI_PROMPT_LOG.md

Foundational prompts used to build **BloomForge** (Track 2). AI tools were used for
scaffolding and refinement; every component is understood and defensible by the team
per the Zero-Black-Box rule.

---

## 1. Architecture / scaffolding prompt
> Scaffold a single-file Streamlit app for a "syllabus → Bloom's Taxonomy quiz"
> tool. Three phases in session_state (setup, quiz, results). Setup takes pasted
> notes or an uploaded .txt/.md/.pdf; a generate step calls an LLM to return quiz
> questions as JSON; the quiz phase shows radio questions with a countdown timer;
> the results phase scores answers and offers a downloadable report. Keep pure
> logic (parsing, scoring) in Streamlit-free functions so they're unit-testable.

## 2. The core generation prompt (runtime — sent to the LLM)
> You are an expert educator and assessment designer. Using ONLY the study
> material below, generate multiple-choice questions with EXACTLY this
> distribution across three cognitive tiers (Bloom's Taxonomy): N Recall
> (Remember), N Application (Understand/Apply), N Code Tracing (Analyze —
> include a code snippet and ask for the output or the bug). Every question has
> exactly 4 options and one correct answer; vary the correct position; ground
> everything in the material. Return ONLY valid JSON (no markdown fences) matching
> this schema: { "questions": [ { id, topic, bloom_level, question, options[4],
> correct_index, explanation } ] }.

*(The tier counts are computed in code and injected, so the split is enforced,
not left to the model — see `compute_distribution()` and `build_prompt()`.)*

## 3. Robust JSON-parsing prompt
> Write a Python function that turns a possibly-messy LLM reply into a validated
> list of questions: strip ```json fences, isolate the outermost {...}, json.loads
> it, then drop any question that doesn't have exactly 4 options or a valid
> 0–3 correct_index. Return None on total failure so the caller can retry once.

## 4. Scoring & report prompt
> Given the questions and a {question_id: selected_index} map, compute overall %,
> per-Bloom-tier %, and per-topic %. Flag tiers/topics below 60% as "needs work"
> and at/above 80% as strengths. Then build a downloadable Markdown report with an
> overall line, a per-tier table, a per-topic table, and a one-line verdict.

## 5. Timer refinement prompt
> Add a countdown to the Streamlit quiz. Store start_time in session_state; on each
> rerun compute remaining time; auto-submit at zero. Use streamlit-autorefresh to
> tick every second, but make the deadline authoritative server-side so a missing
> autorefresh package doesn't break enforcement. Keep radio selections across
> refreshes by giving every widget a stable key.
