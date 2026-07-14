import pytest
import requests
import os
from openai import OpenAI
from typing import AsyncGenerator, Dict, Any, Tuple
from dotenv import load_dotenv
from openai import OpenAIError

# --- Endpoint routing (see README v1.1.0) -----------------------------------
# https://<host>            -> LiteLLM native  : standard OpenAI-compatible passthrough;
#                                                emits SSE terminator `data: [DONE]`, no session_id
# https://<host>/plus       -> middleware      : value-added features (chat history via
#                                                session_id/enable_history, Bedrock Managed Prompts)
#
# The OpenAI SDK appends "/chat/completions" to base_url. Both with and without a
# trailing "/v1" resolve to the same backend within each prefix, so:
#   base_url = <host>        -> LiteLLM native
#   base_url = <host>/plus   -> middleware
#
# We normalize API_ENDPOINT to the host root (strip any trailing "/v1") so tests
# are deterministic.
# ----------------------------------------------------------------------------

load_dotenv()

_raw_endpoint = (os.getenv("API_ENDPOINT") or "").rstrip("/")
host_base = (
    _raw_endpoint[: -len("/v1")].rstrip("/")
    if _raw_endpoint.endswith("/v1")
    else _raw_endpoint
)
api_key = os.getenv("API_KEY")
model_id = os.getenv("MODEL_ID")
print(f"host_base: {host_base} api_key: {api_key} model_id: {model_id}")

# Middleware client -> /plus/chat/completions (chat history, Bedrock prompts)
client = OpenAI(base_url=f"{host_base}/plus", api_key=api_key)
# LiteLLM native client -> /chat/completions (standard OpenAI passthrough)
native_client = OpenAI(base_url=host_base, api_key=api_key)
managed_prompt_arn = os.getenv("MANAGED_PROMPT_ARN")
managed_prompt_variable_name = os.getenv("MANAGED_PROMPT_VARIABLE_NAME")
managed_prompt_variable_value = os.getenv("MANAGED_PROMPT_VARIABLE_VALUE")

small_prompt = "Tell me a one sentence story."
small_prompt_follow_up = "What did I last ask you?"
large_prompt = "Hello" * 10000


async def stream_completion(
    prompt: str,
    model: str = "us.anthropic.claude-3-7-sonnet-20250219-v1:0",
    extra_body: Dict[str, Any] = None,
) -> AsyncGenerator[Tuple[str, str], None]:
    """
    Streams completion responses from the API asynchronously.
    Yields tuples of (content, session_id).
    Session ID is only returned in the first chunk.
    """
    if extra_body is None:
        extra_body = {}

    stream = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        stream=True,
        extra_body=extra_body,
    )

    session_id = None
    first_chunk = True

    for chunk in stream:
        # Get session_id from first chunk
        if first_chunk:
            session_id = getattr(chunk, "session_id", None)
            first_chunk = False

        if chunk.choices[0].delta.content is not None:
            yield chunk.choices[0].delta.content, session_id


def get_completion(
    messages: list,
    model: str = "us.anthropic.claude-3-7-sonnet-20250219-v1:0",
    extra_body: Dict[str, Any] = None,
) -> Tuple[str, str]:
    """
    Gets a complete response from the API in a single request.
    Returns a tuple of (content, session_id).
    """
    if extra_body is None:
        extra_body = {}

    response = client.chat.completions.create(
        model=model,
        messages=messages,
        stream=False,
        extra_body=extra_body,
    )

    session_id = response.model_extra.get("session_id")
    content = response.choices[0].message.content
    return content, session_id


def test_openai_chat():
    content, session_id = get_completion([{"role": "user", "content": small_prompt}])
    assert content is not None and content.strip()
    assert session_id is not None and session_id.strip()
    print(f"test_openai_chat response content: {content} session_id: {session_id}")


@pytest.mark.asyncio
async def test_openai_chat_streaming():
    session_id = None
    text_chunks = []
    async for text_chunk, chunk_session_id in stream_completion(small_prompt):
        if chunk_session_id and not session_id:
            session_id = chunk_session_id
            print(f"\nReceived session ID: {session_id}")
        text_chunks.append(text_chunk)
        print(text_chunk, end="", flush=True)
    print("\n")

    assert session_id is not None and session_id.strip()
    assert text_chunks, "text_chunks should not be empty"
    assert all(
        text_chunk is not None for text_chunk in text_chunks
    ), "All text_chunks should be non null"


def test_openai_chat_history():
    print("First request:", flush=True)
    response_content_1, session_id_1 = get_completion([{"role": "system", "content": "You are a master storyteller"},{"role": "user", "content": small_prompt}], model_id, extra_body={"enable_history": True})
    assert response_content_1 is not None and response_content_1.strip()
    assert session_id_1 is not None and session_id_1.strip()
    print(f"Content: {response_content_1}")
    print(f"Session ID: {session_id_1}\n")

    print("\nSecond request (with session_id):", flush=True)
    response_content_2, session_id_2 = get_completion(
        [{"role": "user", "content": small_prompt_follow_up}], model_id, extra_body={"session_id": session_id_1}
    )
    print(f"Content: {response_content_2}")
    print(f"Session ID: {session_id_2}\n")
    assert response_content_2 is not None and response_content_2.strip()
    assert session_id_2 is not None and session_id_2.strip()
    assert session_id_1 == session_id_2


@pytest.mark.asyncio
async def test_openai_chat_streaming_history():
    session_id_1 = None
    text_chunks = []
    print("First request:", flush=True)
    async for text_chunk, chunk_session_id in stream_completion(small_prompt):
        if chunk_session_id and not session_id_1:
            session_id_1 = chunk_session_id
            print(f"\nReceived session ID: {session_id_1}")
        text_chunks.append(text_chunk)
        print(f"text_chunk: {text_chunk}", end="", flush=True)
    print("\n")

    assert session_id_1 is not None and session_id_1.strip()
    assert text_chunks, "text_chunks should not be empty"
    assert all(
        text_chunk is not None for text_chunk in text_chunks
    ), "All text_chunks should be non null"

    session_id_2 = None
    text_chunks_2 = []
    print("\nSecond request (with session_id):", flush=True)
    async for text_chunk, chunk_session_id in stream_completion(
        small_prompt_follow_up, extra_body={"session_id": session_id_1}
    ):
        if chunk_session_id and not session_id_2:
            session_id_2 = chunk_session_id
            print(f"\nReceived session ID: {session_id_2}")
        text_chunks_2.append(text_chunk)
        print(f"text_chunk: {text_chunk}", end="", flush=True)
    print("\n")

    assert session_id_2 is not None and session_id_2.strip()
    assert text_chunks_2, "text_chunks_2 should not be empty"
    assert all(
        text_chunk is not None for text_chunk in text_chunks_2
    ), "All text_chunks_2 should be non null"
    assert session_id_1 == session_id_2


def test_bedrock_managed_prompt():
    """
    Tests the Bedrock managed prompt functionality with non-streaming response.
    """
    print("Testing Bedrock managed prompt:", flush=True)

    # Test with a managed prompt
    response_content, session_id = get_completion(
        [{"role": "user", "content": ""}],  # Empty prompt as it won't be used
        model=managed_prompt_arn,
        extra_body={
            "promptVariables": {
                managed_prompt_variable_name: {"text": managed_prompt_variable_value}
            }
        },
    )

    assert response_content is not None and response_content.strip()
    assert session_id is not None and session_id.strip()
    print(f"Content: {response_content}")
    print(f"Session ID: {session_id}\n")


@pytest.mark.asyncio
async def test_bedrock_managed_prompt_streaming():
    """
    Tests the Bedrock managed prompt functionality with streaming response.
    """
    print("Testing Bedrock managed prompt with streaming:", flush=True)

    session_id = None
    text_chunks = []

    async for text_chunk, chunk_session_id in stream_completion(
        "",  # Empty prompt as it won't be used
        model=managed_prompt_arn,
        extra_body={
            "promptVariables": {
                managed_prompt_variable_name: {"text": managed_prompt_variable_value}
            }
        },
    ):
        if chunk_session_id and not session_id:
            session_id = chunk_session_id
            print(f"\nReceived session ID: {session_id}")
        text_chunks.append(text_chunk)
        print(text_chunk, end="", flush=True)
    print("\n")

    assert session_id is not None and session_id.strip()
    assert text_chunks, "text_chunks should not be empty"
    assert all(
        text_chunk is not None for text_chunk in text_chunks
    ), "All text_chunks should be non null"


def test_large_prompt():
    content, session_id = get_completion([{"role": "user", "content": large_prompt}])
    assert content is not None and content.strip()
    assert session_id is not None and session_id.strip()
    print(f"test_openai_chat response content: {content} session_id: {session_id}")


def test_invalid_api_key():
    """
    Tests that the API properly handles invalid API keys with appropriate error messages.
    """
    print("Testing invalid API key handling:", flush=True)

    # Create a new client with an invalid API key
    invalid_client = OpenAI(base_url=host_base, api_key="sk-invalid_key_12345")

    # Attempt to make a request with the invalid client
    with pytest.raises(OpenAIError) as exc_info:
        response = invalid_client.chat.completions.create(
            model="us.anthropic.claude-3-7-sonnet-20250219-v1:0",
            messages=[{"role": "user", "content": small_prompt}],
            stream=False,
        )

    # Verify the error message contains authentication-related information
    error_message = str(exc_info.value).lower()
    print(f"Received error message: {error_message}")

    # Assert that the error message contains expected authentication-related terms
    assert any(
        term in error_message
        for term in ["auth", "authentication", "invalid", "key", "unauthorized"]
    ), "Error message should indicate authentication failure"


def test_v1_native_passthrough():
    """v1.0.4 routing contract: /v1/chat/completions is served by LiteLLM natively.

    Locks in the routing behavior introduced in v1.0.4:
    - The native /v1 endpoint does NOT inject the middleware's `session_id`.
    - Native streaming emits the SSE terminator `data: [DONE]` (the middleware
      path on /chat/completions does not forward it).
    """
    # Non-streaming: native path must NOT carry a middleware session_id
    response = native_client.chat.completions.create(
        model=model_id,
        messages=[{"role": "user", "content": small_prompt}],
        stream=False,
    )
    assert response.choices[0].message.content
    assert (
        response.model_extra.get("session_id") is None
    ), "LiteLLM native /v1 must not inject middleware session_id"

    # Streaming (raw SSE): native path must emit `data: [DONE]`
    raw = requests.post(
        f"{host_base}/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model_id,
            "messages": [{"role": "user", "content": small_prompt}],
            "max_tokens": 16,
            "stream": True,
        },
        stream=True,
        timeout=60,
    )
    body = raw.text
    assert "data: [DONE]" in body, (
        "LiteLLM native /v1 streaming should emit the SSE terminator data: [DONE]"
    )
