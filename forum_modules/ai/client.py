"""The one place that talks to the Claude API.

Every AI feature asks for a JSON answer that matches a schema; when the
module is not configured, the API fails or the model declines, the caller
gets None and carries on without AI (posting is never blocked by it).
"""
import json
import logging
import os

try:
    import anthropic
except ImportError:  # pip install -r requirements.txt
    anthropic = None

from .settings import ANTHROPIC_API_KEY, AI_MODEL

log = logging.getLogger(__name__)

# Models that accept server-side refusal fallbacks ("default" routing).
FALLBACK_MODELS = {'claude-opus-5-5', 'claude-opus-5', 'claude-fable-5-1', 'claude-sonnet-5-5'}

_client = {}


def api_key():
    return str(ANTHROPIC_API_KEY).strip() or os.environ.get('ANTHROPIC_API_KEY', '').strip()


def is_configured():
    return anthropic is not None and bool(api_key())


def get_client():
    key = api_key()
    if _client.get('key') != key:
        _client.update(key=key, client=anthropic.Anthropic(api_key=key, timeout=30.0, max_retries=2))
    return _client['client']


def ask_claude(system, content, schema, max_tokens=8000):
    """Returns Claude's answer to `content` as a dict matching the JSON `schema`, or None."""
    if not is_configured():
        return None

    model = str(AI_MODEL).strip()
    params = dict(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{'role': 'user', 'content': content}],
        # Classification work: low effort keeps it fast and cheap.
        output_config={'effort': 'low', 'format': {'type': 'json_schema', 'schema': schema}},
    )
    if model in FALLBACK_MODELS:
        params.update(betas=['server-side-fallback-2026-07-01'], fallbacks='default')

    try:
        response = get_client().beta.messages.create(**params)
    except anthropic.BadRequestError as e:
        log.error("AI request rejected (check the model setting '%s'): %s", model, e.message)
        return None
    except anthropic.AuthenticationError:
        log.error("AI request failed: the Anthropic API key is invalid")
        return None
    except anthropic.APIStatusError as e:
        log.warning("AI request failed with HTTP %s: %s", e.status_code, e.message)
        return None
    except anthropic.APIConnectionError as e:
        log.warning("AI request failed, could not reach the API: %s", e)
        return None

    if response.stop_reason != 'end_turn':
        # 'refusal' (declined even after fallbacks) or 'max_tokens': no usable answer.
        log.info("AI request ended with stop_reason=%s", response.stop_reason)
        return None

    text = next((block.text for block in response.content if block.type == 'text'), None)
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        log.warning("AI response was not valid JSON")
        return None
