import json
import os
import re

from bs4 import BeautifulSoup


EXTRACTION_PROMPT = """You are a data extraction assistant. Given the text content of a web page, extract FAQ-style question and answer pairs.

Rules:
1. Extract EXPLICIT Q&A pairs — where a question is clearly written out (with or without a "?").
2. Also extract IMPLIED Q&A pairs — where a descriptive heading (h2/h3/h4 or bold text) is directly followed by a paragraph that clearly explains it. Convert the heading into a natural question (e.g. "How we handle onboarding" → "How do you handle onboarding?"). Only do this if the following text genuinely answers the heading as a topic — skip vague labels, CTAs, navigation text, or headings with no meaningful body paragraph.
3. For dedicated FAQ pages: extract every Q&A pair found.
4. For blog posts: extract explicit FAQ sections only. Return [] if none.
5. Keep answers concise — key information only, no marketing fluff. Maximum 500 characters per answer.
6. Return ONLY valid JSON. No markdown, no explanation, no code fences.

Return format:
[
  {{"question": "What is X?", "answer": "X is..."}},
  {{"question": "How does Y work?", "answer": "Y works by..."}}
]

If no extractable content is found, return: []

Page content:
---
{page_text}
---"""


ANALYSIS_PROMPT = """You are a content strategist analysing FAQ data scraped from competitor websites.

Given the list of competitor Q&A pairs below, return a JSON object with these exact keys:

{{
  "content_opportunities": [
    {{"question": "...", "why": "one sentence on why this is worth creating content for"}}
  ],
  "competitor_themes": [
    {{"theme": "...", "insight": "one sentence on what this reveals about their positioning"}}
  ],
  "top_questions": ["...", "...", "..."],
  "strategic_insight": "..."
}}

Rules:
- content_opportunities: up to 5 questions that have high search or AI answer engine intent — questions real buyers would ask an AI tool or Google
- competitor_themes: up to 4 recurring topic clusters across all the FAQs — name the theme and explain what it signals
- top_questions: exactly 3 questions that buyers are most commonly asking competitors — the highest-signal questions in the dataset
- strategic_insight: one sharp observation about what these FAQs reveal about how competitors are positioning themselves
- Be specific and direct. No generic observations. No marketing language.
- If page last-modified dates are available and most are older than 2 years, note this as a staleness risk in strategic_insight (e.g. "Note: most pages appear to be from 2021 — this content may not reflect current positioning.").
- Return ONLY valid JSON. No markdown, no explanation, no code fences.

FAQ data:
---
{faq_text}
---"""


def analyze_faqs(rows: list, provider: str, model: str) -> dict:
    """Analyse extracted FAQ rows and return structured key findings.

    rows: list of [competitor, url, question, answer, date]
    Returns a dict with content_opportunities, competitor_themes, top_questions, strategic_insight.
    """
    if not rows:
        return {}

    lines = []
    for r in rows:
        lm = f" [content date: {r[5]}]" if len(r) > 5 and r[5] else ""
        lines.append(f"[{r[0]}]{lm} Q: {r[2]}\n       A: {r[3]}")
    faq_text = "\n\n".join(lines)

    prompt = ANALYSIS_PROMPT.format(faq_text=faq_text)

    try:
        if provider == "openrouter":
            raw = _call_openrouter(prompt, model)
        elif provider == "anthropic":
            raw = _call_anthropic(prompt, model)
        elif provider == "openai":
            raw = _call_openai(prompt, model)
        elif provider == "gemini":
            raw = _call_gemini(prompt, model)
        else:
            return {}

        raw = re.sub(r"```json\s*", "", raw)
        raw = re.sub(r"```\s*", "", raw).strip()
        return json.loads(raw)
    except Exception as e:
        print(f"  [WARN] Analysis failed: {e}")
        return {}


def findings_to_html(findings: dict) -> str:
    """Convert analysis findings dict to an inline HTML email block."""
    if not findings:
        return ""

    parts = []
    parts.append("""
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:600px;margin:0 auto;padding:32px 24px;">
  <div style="border-top:3px solid #F16324;padding-top:24px;margin-bottom:32px;">
    <p style="font-family:monospace;font-size:11px;letter-spacing:0.08em;color:#F16324;text-transform:uppercase;margin:0 0 8px;">Key Findings</p>
    <h2 style="font-size:22px;font-weight:700;color:#14141C;margin:0;">What your competitors' FAQs are telling you</h2>
  </div>
""")

    # Strategic insight
    si = findings.get("strategic_insight", "")
    if si:
        parts.append(f"""
  <div style="background:#FBF8F8;border-left:3px solid #6B49B2;padding:16px 20px;border-radius:0 6px 6px 0;margin-bottom:32px;">
    <p style="font-family:monospace;font-size:10px;letter-spacing:0.08em;color:#6B49B2;text-transform:uppercase;margin:0 0 6px;">Strategic Observation</p>
    <p style="font-size:15px;color:#14141C;line-height:1.6;margin:0;">{si}</p>
  </div>
""")

    # Top questions
    tq = findings.get("top_questions", [])
    if tq:
        items = "".join(f'<li style="padding:8px 0;border-bottom:1px solid #E5E1E4;color:#47404E;font-size:14px;line-height:1.5;">{q}</li>' for q in tq)
        parts.append(f"""
  <div style="margin-bottom:32px;">
    <p style="font-family:monospace;font-size:10px;letter-spacing:0.08em;color:#F16324;text-transform:uppercase;margin:0 0 12px;">Top 3 Questions Buyers Are Asking Competitors</p>
    <ul style="list-style:none;margin:0;padding:0;border-top:1px solid #E5E1E4;">{items}</ul>
  </div>
""")

    # Content opportunities
    co = findings.get("content_opportunities", [])
    if co:
        cards = ""
        for item in co:
            cards += f"""
    <div style="border:1px solid #E5E1E4;border-radius:6px;padding:16px;margin-bottom:10px;">
      <p style="font-size:14px;font-weight:600;color:#14141C;margin:0 0 4px;">{item.get('question','')}</p>
      <p style="font-size:13px;color:#8A8494;margin:0;">{item.get('why','')}</p>
    </div>"""
        parts.append(f"""
  <div style="margin-bottom:32px;">
    <p style="font-family:monospace;font-size:10px;letter-spacing:0.08em;color:#F16324;text-transform:uppercase;margin:0 0 12px;">Content Opportunities</p>
    {cards}
  </div>
""")

    # Competitor themes
    ct = findings.get("competitor_themes", [])
    if ct:
        rows_html = "".join(
            f'<tr><td style="padding:10px 12px;font-size:14px;font-weight:600;color:#14141C;border-bottom:1px solid #E5E1E4;vertical-align:top;width:35%;">{item.get("theme","")}</td><td style="padding:10px 12px;font-size:13px;color:#47404E;border-bottom:1px solid #E5E1E4;line-height:1.5;">{item.get("insight","")}</td></tr>'
            for item in ct
        )
        parts.append(f"""
  <div style="margin-bottom:32px;">
    <p style="font-family:monospace;font-size:10px;letter-spacing:0.08em;color:#F16324;text-transform:uppercase;margin:0 0 12px;">Competitor Themes</p>
    <table style="width:100%;border-collapse:collapse;border:1px solid #E5E1E4;border-radius:6px;overflow:hidden;">
      <thead><tr style="background:#14141C;"><th style="padding:10px 12px;font-family:monospace;font-size:11px;color:#fff;text-align:left;font-weight:500;">Theme</th><th style="padding:10px 12px;font-family:monospace;font-size:11px;color:#fff;text-align:left;font-weight:500;">What it signals</th></tr></thead>
      <tbody>{rows_html}</tbody>
    </table>
  </div>
""")

    parts.append("""
  <div style="border-top:1px solid #E5E1E4;padding-top:20px;margin-top:8px;">
    <p style="font-size:13px;color:#8A8494;margin:0;">Your full FAQ CSV is attached. — <a href="https://intelligentresourcing.co" style="color:#F16324;text-decoration:none;">Intelligent Resourcing</a></p>
  </div>
</div>
""")

    return "".join(parts)


def extract_faqs(page_text, source_url, provider, model, raw_html=None, mode="llm"):
    """Extract FAQ Q&A pairs from a page.

    mode="llm"  — use LLM (internal tool, best quality)
    mode="free" — use schema.org + HTML patterns (lead magnet, no API cost)
    """
    if mode == "free":
        return _extract_free(page_text, raw_html)

    # LLM mode
    prompt = EXTRACTION_PROMPT.format(page_text=page_text)

    if provider == "gemini":
        response_text = _call_gemini(prompt, model)
    elif provider == "openai":
        response_text = _call_openai(prompt, model)
    elif provider == "anthropic":
        response_text = _call_anthropic(prompt, model)
    elif provider == "openrouter":
        response_text = _call_openrouter(prompt, model)
    else:
        print(f"  [ERROR] Unknown LLM provider: {provider}")
        return []

    return _parse_llm_response(response_text)


# ---------------------------------------------------------------------------
# Free extraction (no LLM)
# ---------------------------------------------------------------------------

def _extract_free(page_text, raw_html=None):
    """Try schema.org JSON-LD first, then HTML pattern matching."""
    if raw_html:
        faqs = _extract_schema(raw_html)
        if faqs:
            print(f"  [FREE] Extracted {len(faqs)} FAQ(s) via Schema.org JSON-LD.")
            return faqs

        faqs = _extract_html_patterns(raw_html)
        if faqs:
            print(f"  [FREE] Extracted {len(faqs)} FAQ(s) via HTML patterns.")
            return faqs

    # Last resort: plain text heuristics
    faqs = _extract_text_patterns(page_text)
    if faqs:
        print(f"  [FREE] Extracted {len(faqs)} FAQ(s) via text patterns.")
    return faqs


def _extract_schema(raw_html):
    """Extract FAQs from Schema.org FAQPage JSON-LD markup."""
    soup = BeautifulSoup(raw_html, "html.parser")
    faqs = []

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue

        # Handle both single object and array of objects
        items = data if isinstance(data, list) else [data]
        for item in items:
            # Support @graph wrapper
            if item.get("@type") == "WebPage" or "@graph" in item:
                items = item.get("@graph", items)

            for entry in (items if isinstance(items, list) else [item]):
                if entry.get("@type") == "FAQPage":
                    for entity in entry.get("mainEntity", []):
                        q = entity.get("name", "").strip()
                        a = entity.get("acceptedAnswer", {}).get("text", "").strip()
                        if q and a:
                            faqs.append({"question": q, "answer": a[:500]})

    return faqs


def _extract_html_patterns(raw_html):
    """Extract FAQs using common HTML structural patterns."""
    soup = BeautifulSoup(raw_html, "html.parser")
    faqs = []

    # Pattern 1: <details> / <summary> (native HTML accordion)
    for details in soup.find_all("details"):
        summary = details.find("summary")
        if not summary:
            continue
        question = summary.get_text(strip=True)
        # Answer = everything in <details> except the <summary>
        summary.decompose()
        answer = details.get_text(separator=" ", strip=True)
        if question and answer:
            faqs.append({"question": question, "answer": answer[:500]})
    if faqs:
        return faqs

    # Pattern 2: Elements with faq/question/answer class names
    faq_containers = soup.find_all(
        True,
        class_=re.compile(r"faq|accordion|q-?a", re.I)
    )
    for container in faq_containers:
        q_el = container.find(
            True,
            class_=re.compile(r"question|title|heading|toggle|trigger|header", re.I)
        )
        a_el = container.find(
            True,
            class_=re.compile(r"answer|body|content|panel|text", re.I)
        )
        if q_el and a_el:
            q = q_el.get_text(strip=True)
            a = a_el.get_text(separator=" ", strip=True)
            if q and a and q != a:
                faqs.append({"question": q, "answer": a[:500]})
    if faqs:
        return faqs

    # Pattern 3: Heading (h2/h3/h4) followed immediately by a <p>
    # Only in sections that look like FAQs
    faq_sections = soup.find_all(
        True,
        id=re.compile(r"faq|frequently", re.I)
    ) or soup.find_all(
        True,
        class_=re.compile(r"faq|frequently", re.I)
    )

    search_scope = faq_sections if faq_sections else [soup]
    for scope in search_scope:
        for heading in scope.find_all(["h2", "h3", "h4"]):
            text = heading.get_text(strip=True)
            if not text.endswith("?") and "?" not in text:
                continue
            sibling = heading.find_next_sibling()
            if sibling and sibling.name in ["p", "div", "span"]:
                answer = sibling.get_text(separator=" ", strip=True)
                if answer:
                    faqs.append({"question": text, "answer": answer[:500]})

    return faqs


def _extract_text_patterns(page_text):
    """Last-resort: find Q&A pairs in plain text using heuristics."""
    faqs = []
    lines = [l.strip() for l in page_text.splitlines() if l.strip()]

    i = 0
    while i < len(lines):
        line = lines[i]
        # A line ending in "?" is likely a question
        if line.endswith("?") and len(line) > 10:
            # Collect the next non-empty lines as the answer (up to 3 lines)
            answer_parts = []
            j = i + 1
            while j < len(lines) and len(answer_parts) < 3:
                if lines[j].endswith("?"):
                    break
                answer_parts.append(lines[j])
                j += 1
            if answer_parts:
                answer = " ".join(answer_parts)
                faqs.append({"question": line, "answer": answer[:500]})
                i = j
                continue
        i += 1

    return faqs


# ---------------------------------------------------------------------------
# LLM providers
# ---------------------------------------------------------------------------

def _call_gemini(prompt, model):
    """Call Google Gemini API."""
    import google.generativeai as genai

    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    model_obj = genai.GenerativeModel(model)
    response = model_obj.generate_content(prompt)
    return response.text


def _call_openai(prompt, model):
    """Call OpenAI API."""
    from openai import OpenAI

    client = OpenAI()
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
    )
    return response.choices[0].message.content


def _call_openrouter(prompt, model):
    """Call OpenRouter API (OpenAI-compatible)."""
    from openai import OpenAI

    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=os.environ["OPENROUTER_API_KEY"],
    )
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
    )
    return response.choices[0].message.content


def _call_anthropic(prompt, model):
    """Call Anthropic Claude API."""
    import anthropic

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    response = client.messages.create(
        model=model,
        max_tokens=4096,
        temperature=0.0,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


def _parse_llm_response(text):
    """Parse LLM response into a list of FAQ dicts."""
    text = re.sub(r"```json\s*", "", text)
    text = re.sub(r"```\s*", "", text)
    text = text.strip()

    # Try parsing the whole response first
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return [r for r in result if isinstance(r, dict) and "question" in r and "answer" in r]
    except json.JSONDecodeError:
        pass

    # Model sometimes returns explanation text around a JSON block — extract it
    match = re.search(r"\[.*?\]", text, re.DOTALL)
    if match:
        try:
            result = json.loads(match.group())
            if isinstance(result, list):
                return [r for r in result if isinstance(r, dict) and "question" in r and "answer" in r]
        except json.JSONDecodeError:
            pass

    # Non-empty response that isn't JSON = model explained why there are no FAQs
    if text:
        print(f"  [WARN] LLM returned non-JSON (likely no FAQs found): {text[:120]}")
    return []
