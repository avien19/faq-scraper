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


ANALYSIS_PROMPT = """You are a content strategist analysing FAQ data scraped from a competitor website.

Given the Q&A pairs below (all from one company), return a JSON object with these exact keys:

{{
  "top_questions": ["...", "...", "..."],
  "strategic_insight": "..."
}}

Rules:
- top_questions: exactly 3 questions buyers are most commonly asking this competitor - highest-signal questions in the dataset
- strategic_insight: 1-2 sentences max on how this competitor is positioning themselves based on their FAQ content. Be specific. No generic observations. No marketing language. No em-dashes.
- If page last-modified dates are available and most are older than 2 years, flag it briefly in strategic_insight.
- Return ONLY valid JSON. No markdown, no explanation, no code fences.

FAQ data:
---
{faq_text}
---"""


COMBINED_ANALYSIS_PROMPT = """You are a content strategist analysing FAQ data scraped from multiple competitor websites.

Given the Q&A pairs below (labelled by company), return a JSON object with these exact keys:

{{
  "content_opportunities": [
    {{"question": "...", "why": "one sentence on why this is worth creating content for"}}
  ],
  "competitor_themes": [
    {{"theme": "...", "insight": "one sentence on what this reveals about their positioning"}}
  ]
}}

Rules:
- content_opportunities: up to 5 questions with high search or AI answer engine intent that real buyers would ask - synthesised across all competitors
- competitor_themes: up to 4 recurring topic clusters across all the FAQs - name the theme and explain what it signals. Where relevant, note which company drives the theme.
- Be specific and direct. No generic observations. No marketing language. No em-dashes.
- Return ONLY valid JSON. No markdown, no explanation, no code fences.

FAQ data:
---
{faq_text}
---"""


def _call_llm(prompt: str, provider: str, model: str) -> str:
    if provider == "openrouter":
        return _call_openrouter(prompt, model)
    elif provider == "anthropic":
        return _call_anthropic(prompt, model)
    elif provider == "openai":
        return _call_openai(prompt, model)
    elif provider == "gemini":
        return _call_gemini(prompt, model)
    raise ValueError(f"Unknown provider: {provider}")


def _parse_json(raw: str) -> dict:
    raw = re.sub(r"```json\s*", "", raw)
    raw = re.sub(r"```\s*", "", raw).strip()
    return json.loads(raw)


def analyze_faqs(rows: list, provider: str, model: str) -> dict:
    """Analyse extracted FAQ rows and return structured key findings.

    rows: list of [competitor, url, question, answer, date, ...]
    Returns:
      {
        "by_company": { company: {"strategic_insight": str, "top_questions": [...]} },
        "combined":   {"content_opportunities": [...], "competitor_themes": [...]}
      }
    """
    if not rows:
        return {}

    # Group rows by company
    by_company: dict[str, list] = {}
    for r in rows:
        by_company.setdefault(r[0], []).append(r)

    result: dict = {"by_company": {}, "combined": {}}

    # Per-company: insight + top questions only
    for company, company_rows in by_company.items():
        lines = []
        for r in company_rows:
            lm = f" [content date: {r[5]}]" if len(r) > 5 and r[5] else ""
            lines.append(f"Q:{lm} {r[2]}\nA: {r[3]}")
        try:
            raw = _call_llm(ANALYSIS_PROMPT.format(faq_text="\n\n".join(lines)), provider, model)
            result["by_company"][company] = _parse_json(raw)
        except Exception as e:
            print(f"  [WARN] Analysis failed for {company}: {e}")

    # Combined: content opportunities + themes across all companies
    all_lines = []
    for r in rows:
        lm = f" [content date: {r[5]}]" if len(r) > 5 and r[5] else ""
        all_lines.append(f"[{r[0]}]{lm} Q: {r[2]}\nA: {r[3]}")
    try:
        raw = _call_llm(COMBINED_ANALYSIS_PROMPT.format(faq_text="\n\n".join(all_lines)), provider, model)
        result["combined"] = _parse_json(raw)
    except Exception as e:
        print(f"  [WARN] Combined analysis failed: {e}")

    return result


def _clean(text: str) -> str:
    """Strip em-dashes and en-dashes from LLM output."""
    return text.replace("\u2014", " - ").replace("\u2013", " - ")


def _render_company_section(company: str, findings: dict) -> str:
    si = _clean(findings.get("strategic_insight", ""))
    tq = findings.get("top_questions", [])

    items = "".join(
        f'<li style="padding:6px 0;border-bottom:1px solid #E5E1E4;color:#47404E;font-size:14px;line-height:1.5;">{_clean(q)}</li>'
        for q in tq
    )
    questions_block = f'<ul style="list-style:none;margin:8px 0 0;padding:0;border-top:1px solid #E5E1E4;">{items}</ul>' if items else ""

    return f"""
  <div style="border-top:1px solid #E5E1E4;padding-top:20px;margin-bottom:24px;">
    <p style="font-family:monospace;font-size:10px;letter-spacing:0.08em;color:#8A8494;text-transform:uppercase;margin:0 0 8px;">{company}</p>
    {f'<p style="font-size:14px;color:#14141C;line-height:1.6;margin:0 0 10px;border-left:3px solid #6B49B2;padding-left:12px;">{si}</p>' if si else ""}
    {f'<p style="font-family:monospace;font-size:10px;letter-spacing:0.08em;color:#F16324;text-transform:uppercase;margin:0;">Top questions buyers are asking</p>{questions_block}' if questions_block else ""}
  </div>
"""


def findings_to_html(findings: dict) -> str:
    """Convert analysis findings to an inline HTML email block."""
    if not findings:
        return ""

    by_company = findings.get("by_company", {})
    combined = findings.get("combined", {})

    parts = []
    parts.append("""
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:600px;margin:0 auto;padding:32px 24px;">
  <div style="border-top:3px solid #F16324;padding-top:24px;margin-bottom:28px;">
    <p style="font-family:monospace;font-size:11px;letter-spacing:0.08em;color:#F16324;text-transform:uppercase;margin:0 0 8px;">Key Findings</p>
    <h2 style="font-size:22px;font-weight:700;color:#14141C;margin:0;">What your competitors' FAQs are telling you</h2>
  </div>
""")

    for company, company_findings in by_company.items():
        parts.append(_render_company_section(company, company_findings))

    # Combined content opportunities
    co = combined.get("content_opportunities", [])
    if co:
        cards = "".join(f"""
    <div style="border:1px solid #E5E1E4;border-radius:6px;padding:14px 16px;margin-bottom:8px;">
      <p style="font-size:14px;font-weight:600;color:#14141C;margin:0 0 3px;">{_clean(item.get('question',''))}</p>
      <p style="font-size:13px;color:#8A8494;margin:0;">{_clean(item.get('why',''))}</p>
    </div>""" for item in co)
        parts.append(f"""
  <div style="margin-bottom:28px;">
    <p style="font-family:monospace;font-size:10px;letter-spacing:0.08em;color:#F16324;text-transform:uppercase;margin:0 0 12px;">Content Opportunities</p>
    {cards}
  </div>
""")

    # Combined competitor themes
    ct = combined.get("competitor_themes", [])
    if ct:
        rows_html = "".join(
            f'<tr><td style="padding:10px 12px;font-size:14px;font-weight:600;color:#14141C;border-bottom:1px solid #E5E1E4;vertical-align:top;width:35%;">{_clean(item.get("theme",""))}</td>'
            f'<td style="padding:10px 12px;font-size:13px;color:#47404E;border-bottom:1px solid #E5E1E4;line-height:1.5;">{_clean(item.get("insight",""))}</td></tr>'
            for item in ct
        )
        parts.append(f"""
  <div style="margin-bottom:28px;">
    <p style="font-family:monospace;font-size:10px;letter-spacing:0.08em;color:#F16324;text-transform:uppercase;margin:0 0 12px;">Themes Across Competitors</p>
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
