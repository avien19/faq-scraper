import json
import os
import re


EXTRACTION_PROMPT = """You are a data extraction assistant. Given the text content of a web page, extract all FAQ-style question and answer pairs.

Rules:
1. Extract ONLY explicit question-and-answer pairs. A question must be clearly stated (or implied as a heading/subheading followed by explanatory text).
2. For dedicated FAQ pages: extract every Q&A pair.
3. For blog posts: extract only sections that are formatted as FAQs (e.g., "Frequently Asked Questions" sections, Q&A formatted content). If the page has no FAQ section, return an empty list.
4. Keep the question text exactly as written on the page.
5. Keep the answer concise - include the key information but trim excessive marketing language. Maximum 500 characters per answer.
6. Return ONLY valid JSON. No markdown, no explanation, no code fences.

Return format:
[
  {{"question": "What is X?", "answer": "X is..."}},
  {{"question": "How does Y work?", "answer": "Y works by..."}}
]

If no FAQ content is found, return: []

Page content:
---
{page_text}
---"""


def extract_faqs(page_text, source_url, provider, model):
    """Extract FAQ Q&A pairs from page text using an LLM."""
    prompt = EXTRACTION_PROMPT.format(page_text=page_text)

    if provider == "gemini":
        response_text = _call_gemini(prompt, model)
    elif provider == "openai":
        response_text = _call_openai(prompt, model)
    elif provider == "anthropic":
        response_text = _call_anthropic(prompt, model)
    else:
        print(f"  [ERROR] Unknown LLM provider: {provider}")
        return []

    return _parse_llm_response(response_text)


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
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return [
                r for r in result
                if isinstance(r, dict) and "question" in r and "answer" in r
            ]
    except json.JSONDecodeError:
        print(f"  [WARN] Failed to parse LLM response as JSON: {text[:200]}")
    return []
