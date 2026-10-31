"""General Responses input validation must not trigger tool-content recovery retries."""

import httpx
import pytest
from openai import BadRequestError, UnprocessableEntityError

from agent.error_classifier import FailoverReason, classify_api_error


_VALIDATION_MESSAGE = (
    "203 validation errors:\n"
    " {'type': 'string_type', 'loc': 'body.input', 'msg': 'Input should be a valid string', "
    "'input': '<list of 4 items>'}\n"
    " {'type': 'literal_error', 'loc': 'body.input.reasoning.content.type', "
    "'msg': \"Input should be 'input_text'\", 'input': 'reasoning_text'}"
)


@pytest.mark.parametrize("body_code", [400, "invalid_request_error"])
def test_responses_input_union_validation_is_nonretryable(body_code):
    body = {"error": {"type": "Bad Request", "code": body_code,
                      "param": "body.input", "message": _VALIDATION_MESSAGE}}
    error = BadRequestError(
        "Synthetic HTTP 400",
        response=httpx.Response(400, request=httpx.Request("POST", "https://relay.example.invalid/v1/responses")),
        body=body,
    )
    classified = classify_api_error(error, provider="custom", model="synthetic-model")
    assert classified.status_code == 400
    assert classified.reason == FailoverReason.format_error
    assert classified.retryable is False
    assert classified.should_compress is False


def test_console_go_explicit_tool_content_validation_still_allows_recovery():
    message = (
        "Error from provider (Console Go): messages.67.tool.content.str: "
        "Upstream request failed: [invalid_request_error] Input should be a valid string"
    )
    error = UnprocessableEntityError(
        "Synthetic HTTP 422",
        response=httpx.Response(422, request=httpx.Request("POST", "https://relay.example.invalid/v1/chat/completions")),
        body={"error": {"type": "invalid_request_error", "param": "messages.67.tool.content.str",
                        "message": message}},
    )
    classified = classify_api_error(error, provider="opencode-go", model="synthetic-model")
    assert classified.status_code == 422
    assert classified.reason == FailoverReason.multimodal_tool_content_unsupported
    assert classified.retryable is True
