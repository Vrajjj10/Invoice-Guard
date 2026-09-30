"""Gemini extraction: one call -> validated InvoiceFields; escalate if unsure."""

import time
from dataclasses import dataclass

from google import genai
from google.genai import types

from app.config import get_settings
from app.llm.schema import InvoiceFields

CONFIDENCE_THRESHOLD = 0.75  # below this, retry once with the escalation model
OCR_CONFIDENCE_THRESHOLD = 0.6

# Short and stable on purpose: Gemini implicit caching reuses the repeated prefix.
SYSTEM_PROMPT = (
    "You extract data from Indian GST invoices. Return only JSON matching the schema. "
    "Copy numbers exactly as printed (no currency symbols, no thousands separators). "
    "Dates as YYYY-MM-DD. Use null for anything not present; never guess. "
    "If the text is not an invoice set is_invoice=false. "
    "confidence is your overall certainty (0-1); reason is one short sentence."
)


@dataclass
class LLMResult:
    fields: InvoiceFields
    model: str
    escalated: bool
    tokens: int
    elapsed_ms: int


_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=get_settings().gemini_api_key)
    return _client


def _call(model: str, text: str) -> tuple[InvoiceFields, int]:
    resp = _get_client().models.generate_content(
        model=model,
        contents=text,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=InvoiceFields,
            temperature=0,
            max_output_tokens=2048,
        ),
    )
    fields = InvoiceFields.model_validate_json(resp.text)
    tokens = resp.usage_metadata.total_token_count if resp.usage_metadata else 0
    return fields, tokens


def extract_invoice(text: str, ocr_confidence: float | None = None) -> LLMResult:
    s = get_settings()
    start = time.perf_counter()
    model, tokens, escalated = s.default_model, 0, False
    try:
        fields, used = _call(model, text)
    except ValueError:  # malformed JSON / schema violation -> try the stronger model
        fields, used, model, escalated = None, 0, s.escalation_model, True
    tokens += used
    if fields is None:
        fields, used = _call(model, text)
        tokens += used
    elif fields.is_invoice and (
        fields.confidence < CONFIDENCE_THRESHOLD
        or (ocr_confidence is not None and ocr_confidence < OCR_CONFIDENCE_THRESHOLD)
    ):
        model, escalated = s.escalation_model, True
        fields, used = _call(model, text)
        tokens += used
    return LLMResult(fields, model, escalated, tokens, int((time.perf_counter() - start) * 1000))
