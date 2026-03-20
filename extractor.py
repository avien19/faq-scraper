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
