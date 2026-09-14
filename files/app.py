"""
BloomForge — Dynamic Syllabus Parser & Bloom's Taxonomy Quiz Engine
DevSpark Round 3 · Track 2: Academic & Exam Intelligence

Pipeline:
    notes  ->  parser  ->  LLM question generator (strict JSON)
           ->  interactive timed quiz  ->  rule-based scorer
           ->  weakness/strength report (downloadable)

Run:  streamlit run app.py
"""

import os
import io
import json
import time
import datetime as dt

import streamlit as st
import db
import email_utils

# ----------------------------------------------------------------------------
# Configuration  (change these two lines to swap model / provider)
# ----------------------------------------------------------------------------
DEFAULT_PROVIDER = "gemini"          # "gemini" | "openai" | "groq" | "ollama"
GEMINI_MODEL = "gemini-2.0-flash"    # fast + free tier friendly
OPENAI_MODEL = "gpt-4o-mini"         # cheap + fast
GROQ_MODEL = "groq/compound-mini"
OLLAMA_MODEL = "llama3"

# The three tiers the problem statement asks for, mapped to Bloom's levels.
TIERS = ["Recall", "Application", "Code Tracing"]
BLOOM_MAP = {
    "Recall": "Remember",
    "Application": "Understand / Apply",
    "Code Tracing": "Analyze",
}
TIER_COLOR = {           # badges encode the cognitive level (information, not decor)
    "Recall": "#2563EB",         # blue   — lowest cognitive load
    "Application": "#D97706",     # amber  — mid
    "Code Tracing": "#7C3AED",    # violet — highest
}
WEAK_THRESHOLD = 0.60    # below this %, a tier/topic is flagged "needs work"
STRONG_THRESHOLD = 0.80  # at/above this %, flagged as a strength


# ============================================================================
# 1. INPUT PARSING  (pure functions — no Streamlit, so they're testable)
# ============================================================================
def clean_text(raw: str, max_chars: int = 12000) -> str:
    """Strip blank lines / stray whitespace and cap length to protect the
    model's context window. Returns cleaned study material."""
    lines = [ln.strip() for ln in raw.splitlines()]
    lines = [ln for ln in lines if ln]          # drop empty lines
    text = "\n".join(lines)
    return text[:max_chars]


def extract_pdf_text(file_bytes: bytes) -> str:
    """Best-effort PDF text extraction. Optional dependency: pdfplumber."""
    try:
        import pdfplumber
    except ImportError:
        return ""
    out = []
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            out.append(page.extract_text() or "")
    return "\n".join(out)


# ============================================================================
# 2. QUESTION GENERATION
# ============================================================================
def compute_distribution(n: int) -> dict:
    """Split n questions as evenly as possible across the 3 tiers.
    e.g. 9 -> 3/3/3, 7 -> 3/2/2. This is enforced, not left to the model."""
    base, extra = divmod(n, 3)
    counts = [base, base, base]
    for i in range(extra):
        counts[i] += 1
    return dict(zip(TIERS, counts))


def build_prompt(material: str, dist: dict) -> str:
    """The single foundational generation prompt (also logged in
    AI_PROMPT_LOG.md). Forces strict JSON + a fixed tier distribution."""
    schema = (
        '{\n'
        '  "questions": [\n'
        '    {\n'
        '      "id": 1,\n'
        '      "topic": "short 2-4 word tag",\n'
        '      "bloom_level": "Recall | Application | Code Tracing",\n'
        '      "question": "the question text",\n'
        '      "options": ["A", "B", "C", "D"],\n'
        '      "correct_index": 0,\n'
        '      "explanation": "one sentence why the answer is correct"\n'
        '    }\n'
        '  ]\n'
        '}'
    )
    return f"""You are an expert educator and assessment designer.
Using ONLY the study material below, generate multiple-choice questions.

Produce EXACTLY this distribution across three cognitive tiers (Bloom's Taxonomy):
- {dist['Recall']} "Recall" questions (Bloom: Remember) — definitions, terminology, stated facts.
- {dist['Application']} "Application" questions (Bloom: Understand/Apply) — a short scenario asking which concept/approach fits.
- {dist['Code Tracing']} "Code Tracing" questions (Bloom: Analyze) — INCLUDE a short code snippet in the question and ask for its output or the bug. If the material is non-programming, instead ask a multi-step reasoning/analysis question.

Rules:
- Every question has EXACTLY 4 options and EXACTLY one correct answer.
- Vary the position of the correct option across questions.
- Keep questions unambiguous and grounded strictly in the material.
- "correct_index" is the 0-based index of the correct option.

Return ONLY valid JSON. No markdown fences, no commentary, matching EXACTLY:
{schema}

STUDY MATERIAL:
\"\"\"
{material}
\"\"\"
"""


def parse_questions_json(raw: str):
    """Robustly turn a model reply into a validated question list.
    Strips ```json fences, isolates the JSON object, validates each question.
    Returns list[dict] or None on failure."""
    if not raw:
        return None
    text = raw.strip()
    # strip code fences if the model added them anyway
    if text.startswith("```"):
        text = text.split("```", 2)[1] if text.count("```") >= 2 else text
        text = text.replace("json", "", 1).strip("` \n")
    # isolate the outermost JSON object
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None

    questions = data.get("questions", data if isinstance(data, list) else [])
    clean = []
    for i, q in enumerate(questions):
        try:
            opts = q["options"]
            ci = int(q["correct_index"])
            if not isinstance(opts, list) or len(opts) != 4:
                continue
            if not (0 <= ci <= 3):
                continue
            tier = q.get("bloom_level", "Recall")
            if tier not in TIERS:
                tier = "Recall"
            clean.append({
                "id": i + 1,
                "topic": str(q.get("topic", "General")).strip() or "General",
                "bloom_level": tier,
                "question": str(q["question"]).strip(),
                "options": [str(o) for o in opts],
                "correct_index": ci,
                "explanation": str(q.get("explanation", "")).strip(),
            })
        except (KeyError, TypeError, ValueError):
            continue
    return clean or None


# ---- provider calls (imported lazily so only the SDK you use is required) ----
def _call_gemini(prompt: str, api_key: str) -> str:
    from google import genai
    client = genai.Client(api_key=api_key)
    resp = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
    return resp.text


def _call_openai(prompt: str, api_key: str) -> str:
    from openai import OpenAI
    client = OpenAI(api_key=api_key)
    resp = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.4,
    )
    return resp.choices[0].message.content


def _call_groq(prompt: str, api_key: str) -> str:
    from openai import OpenAI
    client = OpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")
    resp = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.4,
    )
    return resp.choices[0].message.content


def _call_ollama(prompt: str) -> str:
    from openai import OpenAI
    client = OpenAI(api_key="ollama", base_url="http://localhost:11434/v1")
    resp = client.chat.completions.create(
        model=OLLAMA_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.4,
    )
    return resp.choices[0].message.content


def _call_llm(provider: str, prompt: str, api_key: str) -> str:
    if provider == "gemini":
        return _call_gemini(prompt, api_key)
    elif provider == "groq":
        return _call_groq(prompt, api_key)
    elif provider == "ollama":
        return _call_ollama(prompt)
    else:
        return _call_openai(prompt, api_key)


def generate_questions(material, n, provider, api_key):
    """Full generation stage with one automatic repair retry on bad JSON."""
    dist = compute_distribution(n)
    prompt = build_prompt(material, dist)
    raw = _call_llm(provider, prompt, api_key)
    parsed = parse_questions_json(raw)
    if parsed is None:                                   # one repair attempt
        repair = "Your previous output was not valid JSON. " + prompt
        parsed = parse_questions_json(_call_llm(provider, repair, api_key))
    if parsed is None:
        raise ValueError("Model did not return usable questions. Try again.")
    return parsed


# ============================================================================
# 3. SCORING & REPORT  (pure functions)
# ============================================================================
def score_quiz(questions, answers):
    """answers: {question_id: selected_index or None}.
    Returns overall %, per-tier and per-topic breakdowns, and per-question rows."""
    per_tier = {t: [0, 0] for t in TIERS}      # tier -> [correct, total]
    per_topic = {}                              # topic -> [correct, total]
    rows, correct_total = [], 0

    for q in questions:
        sel = answers.get(q["id"])
        is_correct = (sel == q["correct_index"])
        correct_total += int(is_correct)

        per_tier[q["bloom_level"]][1] += 1
        per_tier[q["bloom_level"]][0] += int(is_correct)
        per_topic.setdefault(q["topic"], [0, 0])
        per_topic[q["topic"]][1] += 1
        per_topic[q["topic"]][0] += int(is_correct)

        rows.append({
            "id": q["id"], "topic": q["topic"], "tier": q["bloom_level"],
            "selected": sel, "correct_index": q["correct_index"],
            "is_correct": is_correct,
        })

    total = len(questions)
    return {
        "overall_pct": (correct_total / total) if total else 0.0,
        "correct_total": correct_total,
        "total": total,
        "per_tier": per_tier,
        "per_topic": per_topic,
        "rows": rows,
    }


def _pct(pair):
    c, t = pair
    return (c / t) if t else 0.0


def build_report_md(result, quiz_title="Quiz") -> str:
    """Assemble the downloadable weakness/strength summary as Markdown."""
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# BloomForge — Performance Report",
        f"**Quiz:** {quiz_title}  ",
        f"**Generated:** {now}",
        "",
        f"## Overall: {result['correct_total']}/{result['total']} "
        f"({result['overall_pct']*100:.0f}%)",
        "",
        "## By cognitive tier (Bloom's)",
        "| Tier | Bloom's level | Score | % |",
        "|------|---------------|-------|---|",
    ]
    for t in TIERS:
        c, tot = result["per_tier"][t]
        lines.append(f"| {t} | {BLOOM_MAP[t]} | {c}/{tot} | "
                     f"{_pct([c, tot])*100:.0f}% |")

    lines += ["", "## By topic",
              "| Topic | Score | % |", "|-------|-------|---|"]
    for topic, pair in sorted(result["per_topic"].items(),
                              key=lambda kv: _pct(kv[1])):
        lines.append(f"| {topic} | {pair[0]}/{pair[1]} | {_pct(pair)*100:.0f}% |")

    strengths = [t for t in TIERS if _pct(result["per_tier"][t]) >= STRONG_THRESHOLD]
    weak_tiers = [t for t in TIERS if _pct(result["per_tier"][t]) < WEAK_THRESHOLD]
    weak_topics = [tp for tp, pr in result["per_topic"].items()
                   if _pct(pr) < WEAK_THRESHOLD]

    lines += ["", "## Summary"]
    lines.append(f"- **Strengths (cognitive):** "
                 f"{', '.join(strengths) if strengths else 'None yet'}")
    lines.append(f"- **Needs work (cognitive):** "
                 f"{', '.join(weak_tiers) if weak_tiers else 'None — solid across tiers'}")
    lines.append(f"- **Weak topics:** "
                 f"{', '.join(weak_topics) if weak_topics else 'None — well covered'}")

    verdict = ("Strong grasp — push into harder application/analysis material."
               if result["overall_pct"] >= STRONG_THRESHOLD else
               "Solid base — target the weak topics above."
               if result["overall_pct"] >= WEAK_THRESHOLD else
               "Focus on fundamentals first, then re-test.")
    lines += ["", f"**Verdict:** {verdict}", ""]
    return "\n".join(lines)


# ============================================================================
# 4. DEMO DATA  (lets the app + live demo work even with no API key / rate limit)
# ============================================================================
DEMO_QUESTIONS = [
    {"id": 1, "topic": "Normalization", "bloom_level": "Recall",
     "question": "In relational databases, what is the main goal of normalization?",
     "options": ["Increase redundancy", "Reduce data redundancy and anomalies",
                 "Speed up all queries", "Encrypt stored data"],
     "correct_index": 1,
     "explanation": "Normalization organizes data to minimize redundancy and update anomalies."},
    {"id": 2, "topic": "Primary Key", "bloom_level": "Recall",
     "question": "A primary key must be:",
     "options": ["Nullable", "Unique and non-null", "A foreign key", "Always numeric"],
     "correct_index": 1,
     "explanation": "A primary key uniquely identifies a row and cannot be null."},
    {"id": 3, "topic": "Indexing", "bloom_level": "Recall",
     "question": "What structure is most commonly used to implement a database index?",
     "options": ["Linked list", "B-tree", "Stack", "Hash of the whole row"],
     "correct_index": 1,
     "explanation": "B-trees keep keys sorted for fast range and lookup queries."},
    {"id": 4, "topic": "Joins", "bloom_level": "Application",
     "question": "You need all customers AND their orders, keeping customers who have no orders. Which join?",
     "options": ["INNER JOIN", "LEFT JOIN", "CROSS JOIN", "SELF JOIN"],
     "correct_index": 1,
     "explanation": "LEFT JOIN keeps all left-table rows even without a match."},
    {"id": 5, "topic": "Transactions", "bloom_level": "Application",
     "question": "A transfer debits one account and credits another. Which ACID property guarantees both happen or neither?",
     "options": ["Isolation", "Durability", "Atomicity", "Consistency"],
     "correct_index": 2,
     "explanation": "Atomicity makes a transaction all-or-nothing."},
    {"id": 6, "topic": "Aggregation", "bloom_level": "Application",
     "question": "To get total sales per region, which clause groups the rows before SUM()?",
     "options": ["WHERE", "ORDER BY", "GROUP BY", "HAVING"],
     "correct_index": 2,
     "explanation": "GROUP BY buckets rows so aggregates run per group."},
    {"id": 7, "topic": "SQL Output", "bloom_level": "Code Tracing",
     "question": "Given rows (1,2,3,NULL) in col x, what does SELECT COUNT(x) return?",
     "options": ["4", "3", "0", "NULL"],
     "correct_index": 1,
     "explanation": "COUNT(column) ignores NULLs, so it counts 3 values."},
    {"id": 8, "topic": "Python Output", "bloom_level": "Code Tracing",
     "question": "What prints?\n\nx = [1,2,3]\nprint(x[-1] + x[0])",
     "options": ["2", "4", "3", "Error"],
     "correct_index": 1,
     "explanation": "x[-1]=3 and x[0]=1, so 3+1=4."},
    {"id": 9, "topic": "Loop Tracing", "bloom_level": "Code Tracing",
     "question": "What prints?\n\ns = 0\nfor i in range(1, 4):\n    s += i\nprint(s)",
     "options": ["3", "6", "4", "10"],
     "correct_index": 1,
     "explanation": "1+2+3 = 6."},
]


# ============================================================================
# 5. STREAMLIT UI
# ============================================================================
def get_api_key(provider: str) -> str:
    """Look in Streamlit secrets first (for hosted deploy), then env vars."""
    if provider == "ollama": return "ollama"
    
    key_name = "OPENAI_API_KEY"
    if provider == "gemini": key_name = "GEMINI_API_KEY"
    elif provider == "groq": key_name = "GROQ_API_KEY"
    
    try:
        if key_name in st.secrets:
            return st.secrets[key_name]
    except Exception:
        pass
    return os.environ.get(key_name, "")


def inject_css():
    st.markdown("""
    <style>
      #MainMenu, footer {visibility: hidden;}
      .block-container {max-width: 780px; padding-top: 2.2rem;}
      .bf-title {font-size: 2rem; font-weight: 700; letter-spacing: -0.02em;
                 margin-bottom: 0; color: #111;}
      .bf-sub {color: #666; margin-top: .2rem; margin-bottom: 1.4rem;}
      .bf-badge {display:inline-block; padding:2px 10px; border-radius:999px;
                 font-size:.72rem; font-weight:600; color:#fff; margin-bottom:.5rem;}
      .bf-q {font-size:1.05rem; font-weight:600; margin:.2rem 0 .1rem;}
      .bf-topic {color:#888; font-size:.8rem;}
      .bf-timer {font-variant-numeric: tabular-nums; font-weight:700;
                 font-size:1.05rem;}
      .stRadio > label {font-weight:600;}
      div[data-testid="stMetricValue"] {font-size:1.6rem;}
      pre {background:#f6f6f7; border-radius:8px;}
    </style>
    """, unsafe_allow_html=True)


def badge(tier: str) -> str:
    return (f'<span class="bf-badge" style="background:{TIER_COLOR[tier]}">'
            f'{tier} · {BLOOM_MAP[tier]}</span>')


def init_state():
    ss = st.session_state
    ss.setdefault("phase", "login")     # login | setup | quiz | results
    ss.setdefault("questions", [])
    ss.setdefault("start_time", None)
    ss.setdefault("duration", 300)
    ss.setdefault("result", None)
    ss.setdefault("username", None)
    
    # OTP states
    ss.setdefault("otp_pending", False)
    ss.setdefault("otp_email", "")
    ss.setdefault("otp_action", "")
    ss.setdefault("expected_otp", "")
    ss.setdefault("pending_password", "")


def render_auth():
    ss = st.session_state
    
    st.markdown('<div class="bf-title">Welcome to BloomForge</div>', unsafe_allow_html=True)
    st.markdown('<div class="bf-sub">Please login or register to continue.</div>', unsafe_allow_html=True)
    
    if ss.otp_pending:
        st.subheader(f"OTP Verification ({ss.otp_action.capitalize()})")
        st.info(f"An OTP has been sent to **{ss.otp_email}**.")
        entered_otp = st.text_input("Enter 6-digit OTP", key="entered_otp")
        
        col1, col2 = st.columns(2)
        with col1:
            if st.button("Verify OTP", type="primary", use_container_width=True):
                if entered_otp == ss.expected_otp:
                    if ss.otp_action == "register":
                        if db.create_user(ss.otp_email, ss.pending_password):
                            st.success("Registration successful! You are now logged in.")
                            ss.phase = "setup"
                            ss.username = ss.otp_email
                        else:
                            st.error("Failed to create user. Email may already exist.")
                    elif ss.otp_action == "login":
                        ss.phase = "setup"
                        ss.username = ss.otp_email
                    
                    # Reset OTP state
                    ss.otp_pending = False
                    st.rerun()
                else:
                    st.error("Invalid OTP. Please try again.")
        with col2:
            if st.button("Cancel", use_container_width=True):
                ss.otp_pending = False
                st.rerun()
        return

    tab1, tab2 = st.tabs(["Login", "Register"])
    
    with tab1:
        st.subheader("Login")
        login_user = st.text_input("Email", key="login_user")
        login_pass = st.text_input("Password", type="password", key="login_pass")
        if st.button("Login", type="primary", use_container_width=True):
            if db.verify_user(login_user, login_pass):
                otp = email_utils.generate_otp()
                if email_utils.send_otp_email(login_user, otp, action="login"):
                    ss.otp_pending = True
                    ss.otp_email = login_user
                    ss.otp_action = "login"
                    ss.expected_otp = otp
                    st.rerun()
                else:
                    st.error("Could not send login OTP. Check SMTP settings.")
            else:
                st.error("Invalid email or password")

    with tab2:
        st.subheader("Register")
        reg_user = st.text_input("Email (New)", key="reg_user")
        reg_pass = st.text_input("New Password", type="password", key="reg_pass")
        if st.button("Register", use_container_width=True):
            if reg_user and reg_pass:
                otp = email_utils.generate_otp()
                if email_utils.send_otp_email(reg_user, otp, action="register"):
                    ss.otp_pending = True
                    ss.otp_email = reg_user
                    ss.otp_action = "register"
                    ss.expected_otp = otp
                    ss.pending_password = reg_pass
                    st.rerun()
                else:
                    st.error("Could not send registration OTP. Check SMTP settings.")
            else:
                st.warning("Please fill in both fields.")


def render_setup():
    ss = st.session_state
    st.markdown('<div class="bf-title">BloomForge</div>', unsafe_allow_html=True)
    st.markdown('<div class="bf-sub">Paste your notes → get a tiered quiz → '
                'see exactly where you\'re weak.</div>', unsafe_allow_html=True)

    with st.sidebar:
        st.subheader("Settings")
        provider_options = ["gemini", "openai", "groq", "ollama"]
        idx = provider_options.index(DEFAULT_PROVIDER) if DEFAULT_PROVIDER in provider_options else 0
        provider = st.selectbox("AI provider", provider_options, index=idx)
        api_key = get_api_key(provider)
        
        if provider == "ollama":
            st.caption("Local model ✅ (No key needed)")
        else:
            st.caption("Key loaded ✅" if api_key else "No key found — Demo Mode still works.")
        num_q = st.slider("Number of questions", 5, 10, 9)
        minutes = st.slider("Time limit (minutes)", 1, 20, 5)
        st.divider()
        d = compute_distribution(num_q)
        st.caption(f"Tier split → Recall {d['Recall']} · "
                   f"Application {d['Application']} · Code Tracing {d['Code Tracing']}")

    # ---- input ----
    uploaded = st.file_uploader("Upload notes (.txt, .md, .pdf)",
                                type=["txt", "md", "pdf"])
    default_text = ""
    if uploaded is not None:
        if uploaded.name.lower().endswith(".pdf"):
            default_text = extract_pdf_text(uploaded.read())
            if not default_text:
                st.warning("Couldn't read that PDF (install pdfplumber). "
                           "Paste the text below instead.")
        else:
            default_text = uploaded.read().decode("utf-8", errors="ignore")

    notes = st.text_area("…or paste your study material",
                         value=default_text, height=220,
                         placeholder="Paste syllabus notes, definitions, code, etc.")

    col1, col2 = st.columns([1, 1])
    with col1:
        go = st.button("Generate quiz", type="primary", width='stretch')
    with col2:
        demo = st.button("Try Demo Mode", width='stretch')

    if demo:
        ss.questions = DEMO_QUESTIONS[:num_q]
        _start_quiz(minutes)

    if go:
        material = clean_text(notes)
        if len(material) < 40:
            st.error("Add a bit more material (at least a few sentences).")
            return
        if not api_key and provider != "ollama":
            st.error("No API key found. Add one in the sidebar's provider, "
                     "or use Demo Mode to see the full flow.")
            return
        with st.spinner("Generating tiered questions…"):
            try:
                ss.questions = generate_questions(material, num_q,
                                                  provider, api_key)
                _start_quiz(minutes)
            except Exception as e:
                st.error(f"Generation failed: {e}")


def _start_quiz(minutes):
    ss = st.session_state
    ss.duration = minutes * 60
    ss.start_time = time.time()
    ss.phase = "quiz"
    # clear any previous answer widgets
    for k in list(ss.keys()):
        if k.startswith("ans_"):
            del ss[k]
    st.rerun()


def render_quiz():
    ss = st.session_state
    elapsed = time.time() - ss.start_time
    remaining = max(0, int(ss.duration - elapsed))

    # live tick — degrades gracefully if the package isn't installed
    if remaining > 0:
        try:
            from streamlit_autorefresh import st_autorefresh
            st_autorefresh(interval=1000, key="ticker")
        except ImportError:
            pass

    if remaining <= 0:                       # time up -> auto-submit
        _submit_quiz()
        return

    mins, secs = divmod(remaining, 60)
    top = st.columns([3, 1])
    with top[0]:
        st.markdown('<div class="bf-title" style="font-size:1.4rem">'
                    'Quiz in progress</div>', unsafe_allow_html=True)
    with top[1]:
        color = "#DC2626" if remaining <= 30 else "#111"
        st.markdown(f'<div class="bf-timer" style="color:{color};text-align:right">'
                    f'⏱ {mins:02d}:{secs:02d}</div>', unsafe_allow_html=True)
    st.progress(remaining / ss.duration)

    answered = sum(1 for q in ss.questions if ss.get(f"ans_{q['id']}") is not None)
    st.caption(f"{answered}/{len(ss.questions)} answered")

    for q in ss.questions:
        st.markdown(badge(q["bloom_level"]), unsafe_allow_html=True)
        st.markdown(f'<span class="bf-topic">Topic: {q["topic"]}</span>',
                    unsafe_allow_html=True)
        st.markdown(f'<div class="bf-q">Q{q["id"]}.</div>', unsafe_allow_html=True)
        # code-tracing questions read better in a code block
        if q["bloom_level"] == "Code Tracing" and "\n" in q["question"]:
            head, _, code = q["question"].partition("\n")
            st.write(head)
            st.code(code, language="python")
        else:
            st.write(q["question"])
        st.radio("Choose one:", options=list(range(4)),
                 format_func=lambda i, opts=q["options"]: opts[i],
                 index=None, key=f"ans_{q['id']}")
        st.divider()

    if st.button("Submit quiz", type="primary", width='stretch'):
        _submit_quiz()


def _submit_quiz():
    ss = st.session_state
    answers = {q["id"]: ss.get(f"ans_{q['id']}") for q in ss.questions}
    ss.result = score_quiz(ss.questions, answers)
    
    if ss.get("username"):
        db.save_quiz_score(ss.username, ss.result['correct_total'], ss.result['total'])
        
    ss.phase = "results"
    st.rerun()


def render_results():
    ss = st.session_state
    r = ss.result
    st.markdown('<div class="bf-title">Your results</div>', unsafe_allow_html=True)

    c = st.columns(3)
    c[0].metric("Overall", f"{r['overall_pct']*100:.0f}%",
                f"{r['correct_total']}/{r['total']}")
    best = max(TIERS, key=lambda t: _pct(r["per_tier"][t]))
    worst = min(TIERS, key=lambda t: _pct(r["per_tier"][t]))
    c[1].metric("Strongest tier", best, f"{_pct(r['per_tier'][best])*100:.0f}%")
    c[2].metric("Weakest tier", worst, f"{_pct(r['per_tier'][worst])*100:.0f}%",
                delta_color="inverse")

    st.subheader("By cognitive tier")
    st.dataframe(
        [{"Tier": t, "Bloom's": BLOOM_MAP[t],
          "Score": f"{r['per_tier'][t][0]}/{r['per_tier'][t][1]}",
          "%": round(_pct(r["per_tier"][t]) * 100)} for t in TIERS],
        hide_index=True, width='stretch')

    st.subheader("By topic")
    st.dataframe(
        [{"Topic": tp, "Score": f"{pr[0]}/{pr[1]}",
          "%": round(_pct(pr) * 100)}
         for tp, pr in sorted(r["per_topic"].items(), key=lambda kv: _pct(kv[1]))],
        hide_index=True, width='stretch')

    # per-question review
    with st.expander("Review answers"):
        qmap = {q["id"]: q for q in ss.questions}
        for row in r["rows"]:
            q = qmap[row["id"]]
            mark = "✅" if row["is_correct"] else "❌"
            sel = q["options"][row["selected"]] if row["selected"] is not None else "— skipped —"
            st.markdown(f"**{mark} Q{q['id']} ({q['bloom_level']})** — {q['question'].splitlines()[0]}")
            st.caption(f"Your answer: {sel}  ·  Correct: {q['options'][q['correct_index']]}")
            if q["explanation"]:
                st.caption(f"↳ {q['explanation']}")

    report = build_report_md(r)
    st.download_button("⬇ Download report (.md)", data=report,
                       file_name="bloomforge_report.md",
                       mime="text/markdown", type="primary",
                       width='stretch')

    if st.button("Start over", width='stretch'):
        for k in list(ss.keys()):
            del ss[k]
        st.rerun()


def render_sidebar_profile():
    ss = st.session_state
    if ss.get("username"):
        with st.sidebar:
            st.divider()
            st.subheader("User Profile")
            st.write(f"Logged in as: **{ss.username}**")
            
            if st.button("🏆 Leaderboard", use_container_width=True):
                ss.phase = "leaderboard"
                st.rerun()
                
            if st.button("My Profile", use_container_width=True):
                ss.phase = "profile"
                st.rerun()
                
            if st.button("Logout", use_container_width=True):
                for key in list(ss.keys()):
                    del ss[key]
                ss.phase = "login"
                st.rerun()


def render_profile():
    ss = st.session_state
    st.markdown('<div class="bf-title">My Profile</div>', unsafe_allow_html=True)
    
    profile = db.get_user_profile(ss.username)
    if profile is None:
        st.error("Profile not found.")
        if st.button("Back"):
            ss.phase = "setup"
            st.rerun()
        return
        
    with st.form("profile_form"):
        full_name = st.text_input("Full Name", value=profile.get("full_name", ""))
        details = st.text_area("Details / Bio", value=profile.get("details", ""))
        
        if st.form_submit_button("Save Profile", use_container_width=True):
            if db.update_user_profile(ss.username, full_name, details):
                st.success("Profile updated successfully!")
            else:
                st.error("Failed to update profile.")
                
    if st.button("Back to Dashboard", use_container_width=True):
        ss.phase = "setup"
        st.rerun()



def render_leaderboard():
    ss = st.session_state
    st.markdown('<div class="bf-title">🏆 Global Leaderboard</div>', unsafe_allow_html=True)
    st.markdown('<div class="bf-sub">Top minds on BloomForge</div><br>', unsafe_allow_html=True)
    
    leaderboard_data = db.get_leaderboard()
    
    if not leaderboard_data:
        st.info("No quizzes have been completed yet. Be the first!")
    else:
        st.dataframe(leaderboard_data, hide_index=False, width='stretch')
        
    if st.button("Back to Dashboard", use_container_width=True):
        ss.phase = "setup"
        st.rerun()


def main():
    st.set_page_config(page_title="BloomForge", page_icon="🌱", layout="centered")
    inject_css()
    init_state()

    if st.session_state.phase != "login":
        render_sidebar_profile()
        
    phase = st.session_state.phase
    if phase == "login":
        render_auth()
    elif phase == "setup":
        render_setup()
    elif phase == "quiz":
        render_quiz()
    elif phase == "profile":
        render_profile()
    elif phase == "leaderboard":
        render_leaderboard()
    else:
        render_results()



if __name__ == "__main__":
    main()
