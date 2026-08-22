"""Tests for multi-provider llm_client.

Does NOT make real API calls — tests config, auth, URL building, model mapping.
"""
import os
import pytest
from unittest import mock

import services.llm_client as llm

@pytest.fixture(autouse=True)
def clear_module_state():
    """Reset module globals before each test."""
    saved_env = {
        "LLM_BASE_URL": os.environ.pop("LLM_BASE_URL", None),
        "LLM_API_KEY": os.environ.pop("LLM_API_KEY", None),
        "LLM_API_KEY_HEADER": os.environ.pop("LLM_API_KEY_HEADER", None),
        "LLM_API_KEY_PREFIX": os.environ.pop("LLM_API_KEY_PREFIX", None),
        "LLM_PROXY": os.environ.pop("LLM_PROXY", None),
        "LLM_MODEL_MAP": os.environ.pop("LLM_MODEL_MAP", None),
        "ANTHROPIC_API_KEY": os.environ.pop("ANTHROPIC_API_KEY", None),
    }
    # Reset module state
    llm._base_url = llm._DEFAULT_BASE_URL
    llm._api_key = ""
    llm._api_key_header = llm._DEFAULT_API_KEY_HEADER
    llm._api_key_prefix = ""
    llm._model_map = {}
    llm._proxy_url = ""
    llm._cached_token = None
    llm._apply_proxy()
    yield
    # Restore env
    for k, v in saved_env.items():
        if v is not None:
            os.environ[k] = v
        elif k in os.environ:
            del os.environ[k]


# ── URL building ─────────────────────────────────────────

def test_default_base_url():
    assert llm._base_url == "https://api.anthropic.com"


def test_build_api_url_anthropic():
    llm._base_url = "https://api.anthropic.com"
    url = llm._build_api_url()
    assert url == "https://api.anthropic.com/v1/messages?beta=true"


def test_build_api_url_third_party():
    llm._base_url = "https://api.deepseek.com"
    url = llm._build_api_url()
    assert url == "https://api.deepseek.com/v1/messages"
    assert "?beta=true" not in url


def test_build_api_url_trailing_slash():
    llm._base_url = "https://api.siliconflow.cn/"
    url = llm._build_api_url()
    assert url == "https://api.siliconflow.cn/v1/messages"


def test_is_anthropic_official():
    llm._base_url = "https://api.anthropic.com"
    assert llm._is_anthropic_official() is True
    llm._base_url = "https://api.deepseek.com"
    assert llm._is_anthropic_official() is False


# ── Model mapping ────────────────────────────────────────

def test_resolve_model_no_map():
    llm._model_map = {}
    assert llm.resolve_model("claude-sonnet-4-6-20250514") == "claude-sonnet-4-6-20250514"


def test_resolve_model_with_map():
    llm._model_map = {"smart": "deepseek-chat", "fast": "deepseek-chat"}
    assert llm.resolve_model("smart") == "deepseek-chat"
    assert llm.resolve_model("fast") == "deepseek-chat"
    # Direct model name still passes through
    assert llm.resolve_model("claude-sonnet-4-6") == "claude-sonnet-4-6"


def test_resolve_model_unknown_alias():
    llm._model_map = {"smart": "deepseek-chat"}
    # 用户 map 没配的逻辑别名 → 落到默认别名(真实 Anthropic 模型)
    assert llm.resolve_model("balanced") == llm._DEFAULT_ALIASES["balanced"]
    # 真正未知的名字 → 原样返回
    assert llm.resolve_model("turbo-x") == "turbo-x"


def test_get_model_map_returns_copy():
    llm._model_map = {"smart": "gpt-4"}
    m = llm.get_model_map()
    m["smart"] = "changed"
    assert llm._model_map["smart"] == "gpt-4"


def test_parse_model_map_valid():
    result = llm._parse_model_map('{"smart":"m1","fast":"m2"}')
    assert result == {"smart": "m1", "fast": "m2"}


def test_parse_model_map_empty():
    assert llm._parse_model_map("") == {}
    assert llm._parse_model_map(None) == {}
    assert llm._parse_model_map("  ") == {}


def test_parse_model_map_invalid():
    assert llm._parse_model_map("not json") == {}
    assert llm._parse_model_map('["array"]') == {}
    assert llm._parse_model_map('{"k": 123}') == {}  # values must be strings


# ── Auth header building ─────────────────────────────────

def test_build_headers_api_key_default():
    """Default: x-api-key header."""
    headers = llm._build_headers("sk-test-key-123", is_oauth=False)
    assert headers["x-api-key"] == "sk-test-key-123"
    assert headers["Content-Type"] == "application/json"
    assert "Authorization" not in headers


def test_build_headers_authorization_bearer():
    """Authorization: Bearer mode (e.g. DeepSeek, SiliconFlow)."""
    llm._api_key_header = "Authorization"
    llm._api_key_prefix = "Bearer"
    headers = llm._build_headers("sk-deepseek-key", is_oauth=False)
    assert headers["Authorization"] == "Bearer sk-deepseek-key"
    assert "x-api-key" not in headers


def test_build_headers_custom_header():
    """Custom header name."""
    llm._api_key_header = "X-Custom-Key"
    llm._api_key_prefix = ""
    headers = llm._build_headers("my-key", is_oauth=False)
    assert headers["X-Custom-Key"] == "my-key"


def test_build_headers_oauth_mode():
    """OAuth mode: CLAUDE_CODE_HEADERS + Bearer token."""
    headers = llm._build_headers("oat-token-123", is_oauth=True)
    assert headers["Authorization"] == "Bearer oat-token-123"
    assert "anthropic-beta" in headers
    assert "X-Claude-Code-Session-Id" in headers


# ── System prompt building ───────────────────────────────

def test_build_system_oauth_no_system():
    result = llm._build_system(None, is_oauth=True)
    assert len(result) == 1
    assert result[0]["type"] == "text"
    assert "Claude Code" in result[0]["text"]


def test_build_system_oauth_with_system():
    result = llm._build_system("You are a helpful assistant.", is_oauth=True)
    assert len(result) == 2
    assert result[0]["text"] == llm.CLAUDE_IDENTITY
    assert result[1]["text"] == "You are a helpful assistant."


def test_build_system_non_oauth():
    result = llm._build_system("You are helpful.", is_oauth=False)
    assert result == "You are helpful."


def test_build_system_non_oauth_none():
    result = llm._build_system(None, is_oauth=False)
    assert result is None


# ── configure_llm ────────────────────────────────────────

def test_configure_llm_basic():
    llm.configure_llm(
        base_url="https://api.deepseek.com",
        api_key="sk-ds-123",
        api_key_header="Authorization",
        api_key_prefix="Bearer",
        proxy="http://127.0.0.1:7890",
        model_map={"smart": "deepseek-chat"},
    )
    assert llm._base_url == "https://api.deepseek.com"
    assert llm._api_key == "sk-ds-123"
    assert llm._api_key_header == "Authorization"
    assert llm._api_key_prefix == "Bearer"
    assert llm._proxy_url == "http://127.0.0.1:7890"
    assert llm._model_map == {"smart": "deepseek-chat"}


def test_configure_llm_env_var_wins():
    """When env vars are set, configure_llm does NOT overwrite them with DB values."""
    os.environ["LLM_BASE_URL"] = "https://env.example.com"
    os.environ["LLM_API_KEY"] = "env-key"

    llm.configure_llm(
        base_url="https://db.example.com",
        api_key="db-key",
    )
    # DB values are rejected because env vars are present
    assert llm._api_key == ""  # _api_key not set; env is read at call-time in _resolve_auth
    assert llm._base_url != "https://db.example.com"  # DB value not applied


def test_configure_llm_partial():
    """Only provided fields are updated."""
    original = llm._base_url
    llm.configure_llm(api_key="new-key")
    assert llm._base_url == original  # unchanged
    assert llm._api_key == "new-key"


# ── get_llm_config ───────────────────────────────────────

def test_get_llm_config_defaults():
    config = llm.get_llm_config()
    assert config["base_url"] == "https://api.anthropic.com"
    assert config["has_api_key"] is False
    assert config["api_key_header"] == "x-api-key"
    assert config["api_key_prefix"] == ""


def test_get_llm_config_with_key():
    llm._api_key = "sk-1234567890abcdef"
    config = llm.get_llm_config()
    assert config["has_api_key"] is True
    assert config["api_key"] == "sk-1****cdef"  # masked


# ── Key masking ──────────────────────────────────────────

def test_mask_key_short():
    assert llm._mask_key("short") == "****"


def test_mask_key_normal():
    masked = llm._mask_key("sk-1234567890abcdef1234567890abcdef")
    assert masked.startswith("sk-1")
    assert "****" in masked
    # The key is 36 chars: first 4 + **** + last 4 = sk-1****cdef
    assert masked == "sk-1****cdef"


# ── Proxy ────────────────────────────────────────────────

def test_apply_proxy_empty():
    llm._proxy_url = ""
    llm._apply_proxy()
    assert llm._llm_session.proxies == {}


def test_apply_proxy_set():
    llm._proxy_url = "http://127.0.0.1:7890"
    llm._apply_proxy()
    assert llm._llm_session.proxies == {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}
    # cleanup
    llm._proxy_url = ""
    llm._apply_proxy()


# ── test_connection (mocked) ─────────────────────────────

@mock.patch.object(llm._llm_session, "post")
def test_test_connection_ok(mock_post):
    mock_resp = mock.MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {"content": [{"type": "text", "text": "ok"}]}
    mock_post.return_value = mock_resp

    llm._api_key = "sk-test"
    result = llm.test_connection()
    assert result["ok"] is True
    assert result["latency_ms"] >= 0
    assert result["error"] == ""


@mock.patch.object(llm._llm_session, "post")
def test_test_connection_error(mock_post):
    mock_resp = mock.MagicMock()
    mock_resp.ok = False
    mock_resp.status_code = 401
    mock_resp.text = "Unauthorized"
    mock_post.return_value = mock_resp

    llm._api_key = "sk-bad"
    result = llm.test_connection()
    assert result["ok"] is False
    assert "401" in result["error"]


@mock.patch.object(llm._llm_session, "post")
def test_test_connection_exception(mock_post):
    mock_post.side_effect = Exception("Connection refused")

    llm._api_key = "sk-test"
    result = llm.test_connection()
    assert result["ok"] is False
    assert "Connection refused" in result["error"]


# ── _resolve_auth multi-tier fallback ────────────────────

def test_resolve_auth_llm_api_key_priority():
    """LLM_API_KEY takes priority over ANTHROPIC_API_KEY."""
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-old-key"
    llm._api_key = "sk-new-key"
    token, is_oauth = llm._resolve_auth()
    assert token == "sk-new-key"
    assert is_oauth is False


def test_resolve_auth_falls_back_to_env():
    """When _api_key is empty, fall back to ANTHROPIC_API_KEY."""
    llm._api_key = ""
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-oat-env-key"
    token, is_oauth = llm._resolve_auth()
    assert token == "sk-ant-oat-env-key"
    assert is_oauth is True  # sk-ant-oat pattern matches


def test_resolve_auth_uses_cached_oauth_token():
    """Cached OAuth token is reused without re-reading keychain."""
    llm._api_key = ""
    # Remove ANTHROPIC_API_KEY so it would need keychain
    os.environ.pop("ANTHROPIC_API_KEY", None)
    llm._cached_token = "cached-oat-token"
    token, is_oauth = llm._resolve_auth()
    assert token == "cached-oat-token"
    assert is_oauth is True


def test_resolve_auth_no_credentials_raises():
    """When all auth sources are empty, RuntimeError is raised."""
    llm._api_key = ""
    llm._cached_token = None
    os.environ.pop("ANTHROPIC_API_KEY", None)
    with pytest.raises(RuntimeError, match="无法获取 LLM API 凭证"):
        # Mock subprocess to avoid real keychain call
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1  # keychain not found
            llm._resolve_auth()


@mock.patch("subprocess.run")
def test_resolve_auth_keychain_success(mock_run):
    """macOS Keychain returns a valid OAuth token."""
    llm._api_key = ""
    llm._cached_token = None
    os.environ.pop("ANTHROPIC_API_KEY", None)

    mock_run.return_value.returncode = 0
    mock_run.return_value.stdout = '{"claudeAiOauth":{"accessToken":"keychain-token-123"}}'

    # Only test on Darwin or mock os.uname
    with mock.patch("os.uname") as mock_uname:
        mock_uname.return_value.sysname = "Darwin"
        token, is_oauth = llm._resolve_auth()
        assert token == "keychain-token-123"
        assert is_oauth is True
        assert llm._cached_token == "keychain-token-123"


# ── _safe_error_body ─────────────────────────────────────

def test_safe_error_body_masks_sk_ant_key():
    resp = mock.MagicMock()
    resp.text = '{"error":"invalid key sk-ant-abc123def456ghi789 for endpoint"}'
    result = llm._safe_error_body(resp)
    assert "sk-ant-abc123def456ghi789" not in result
    assert "***MASKED***" in result


def test_safe_error_body_masks_bearer_token():
    resp = mock.MagicMock()
    resp.text = 'auth failed: Bearer eyJhbGciOiJIUzI1NiJ9.abc.def was rejected'
    result = llm._safe_error_body(resp)
    assert "eyJhbGciOiJIUzI1NiJ9" not in result
    assert "***MASKED***" in result


def test_safe_error_body_clean_text_passes_through():
    resp = mock.MagicMock()
    resp.text = '{"error":"model not found"}'
    result = llm._safe_error_body(resp)
    assert result == '{"error":"model not found"}'


def test_safe_error_body_empty():
    resp = mock.MagicMock()
    resp.text = ""
    result = llm._safe_error_body(resp)
    assert result == ""


def test_safe_error_body_masks_deepseek_key():
    """DeepSeek-style keys (sk- + long token) are masked."""
    resp = mock.MagicMock()
    resp.text = '{"error":"invalid key sk-ds-abc123def456ghi789jkl012mno345pqr678"}'
    result = llm._safe_error_body(resp)
    assert "sk-ds-abc123def456ghi789" not in result
    assert "***MASKED***" in result


def test_safe_error_body_masks_multiple_occurrences():
    """Multiple API keys in same body are all masked."""
    resp = mock.MagicMock()
    resp.text = 'first=sk-ant-abc123def456ghi789 second=sk-ant-xyz987wvu654tsr321'
    result = llm._safe_error_body(resp)
    assert "abc123def456ghi789" not in result
    assert "xyz987wvu654tsr321" not in result
    # Both should be masked
    assert result.count("***MASKED***") >= 2


def test_safe_error_body_masks_bearer_with_prefix_preserved():
    """Bearer prefix is preserved, only the token value is masked."""
    resp = mock.MagicMock()
    resp.text = 'failed auth: Bearer abcdefghijklmnopqrstuvwxyz123456 was rejected'
    result = llm._safe_error_body(resp)
    assert "Bearer" in result
    assert "abcdefghijklmnopqrstuvwxyz" not in result
    assert "***MASKED***" in result


def test_safe_error_body_no_false_positive_short_strings():
    """Short words that happen to start with sk- are not masked."""
    resp = mock.MagicMock()
    resp.text = '{"error":"model not found: skip-3"}'
    result = llm._safe_error_body(resp)
    # "skip-3" should NOT be masked (key needs >=8 chars after prefix)
    assert "skip-3" in result or "***MASKED***" not in result


# ── Retry logic ──────────────────────────────────────────

def test_compute_backoff_exponential():
    """Backoff doubles on each attempt, capped at max."""
    saved_max = llm._RETRY_MAX_BACKOFF_S
    saved_init = llm._RETRY_INITIAL_BACKOFF_S
    llm._RETRY_INITIAL_BACKOFF_S = 1.0
    llm._RETRY_MAX_BACKOFF_S = 8.0
    try:
        assert llm._compute_backoff(0, None) == 1.0
        assert llm._compute_backoff(1, None) == 2.0
        assert llm._compute_backoff(2, None) == 4.0
        assert llm._compute_backoff(3, None) == 8.0  # capped
        assert llm._compute_backoff(10, None) == 8.0  # still capped
    finally:
        llm._RETRY_INITIAL_BACKOFF_S = saved_init
        llm._RETRY_MAX_BACKOFF_S = saved_max


def test_compute_backoff_respects_retry_after():
    """Retry-After header takes precedence over exponential backoff."""
    saved_max = llm._RETRY_MAX_BACKOFF_S
    llm._RETRY_MAX_BACKOFF_S = 8.0
    try:
        # Numeric Retry-After in seconds
        assert llm._compute_backoff(0, "3") == 3.0
        # Even if exponential would be smaller
        assert llm._compute_backoff(0, "10") == 10.0
        # Invalid format falls back to exponential
        assert llm._compute_backoff(0, "invalid") == 1.0
    finally:
        llm._RETRY_MAX_BACKOFF_S = saved_max


@mock.patch("time.sleep")  # don't actually sleep
@mock.patch.object(llm._llm_session, "post")
def test_post_with_retry_429_then_success(mock_post, mock_sleep):
    """429 → retry → success on second attempt."""
    err_resp = mock.MagicMock()
    err_resp.status_code = 429
    err_resp.headers = {}

    ok_resp = mock.MagicMock()
    ok_resp.status_code = 200
    ok_resp.ok = True

    mock_post.side_effect = [err_resp, ok_resp]

    resp = llm._post_with_retry({"k": "v"}, {"m": "x"})
    assert resp is ok_resp
    assert mock_post.call_count == 2
    assert mock_sleep.called


@mock.patch("time.sleep")
@mock.patch.object(llm._llm_session, "post")
def test_post_with_retry_500_then_success(mock_post, mock_sleep):
    """500 → retry → success."""
    err = mock.MagicMock()
    err.status_code = 500
    err.headers = {}

    ok = mock.MagicMock()
    ok.status_code = 200

    mock_post.side_effect = [err, ok]
    resp = llm._post_with_retry({"k": "v"}, {"m": "x"})
    assert resp is ok
    assert mock_post.call_count == 2


@mock.patch("time.sleep")
@mock.patch.object(llm._llm_session, "post")
def test_post_with_retry_502_503_504_all_retried(mock_post, mock_sleep):
    """All retryable server errors get retried."""
    for code in (502, 503, 504):
        mock_post.reset_mock()
        mock_sleep.reset_mock()
        err = mock.MagicMock()
        err.status_code = code
        err.headers = {}
        ok = mock.MagicMock()
        ok.status_code = 200
        mock_post.side_effect = [err, ok]
        resp = llm._post_with_retry({}, {})
        assert resp is ok
        assert mock_post.call_count == 2


@mock.patch("time.sleep")
@mock.patch.object(llm._llm_session, "post")
def test_post_with_retry_401_not_retried(mock_post, mock_sleep):
    """401 is NOT retried by _post_with_retry (handled separately by OAuth flow)."""
    err = mock.MagicMock()
    err.status_code = 401
    err.headers = {}
    mock_post.return_value = err

    resp = llm._post_with_retry({}, {})
    assert resp.status_code == 401
    assert mock_post.call_count == 1  # no retry
    assert not mock_sleep.called


@mock.patch("time.sleep")
@mock.patch.object(llm._llm_session, "post")
def test_post_with_retry_400_not_retried(mock_post, mock_sleep):
    """400 (bad request) is NOT retried."""
    err = mock.MagicMock()
    err.status_code = 400
    err.headers = {}
    mock_post.return_value = err

    resp = llm._post_with_retry({}, {})
    assert resp.status_code == 400
    assert mock_post.call_count == 1


@mock.patch("time.sleep")
@mock.patch.object(llm._llm_session, "post")
def test_post_with_retry_403_not_retried(mock_post, mock_sleep):
    """403 (permission) is NOT retried."""
    err = mock.MagicMock()
    err.status_code = 403
    err.headers = {}
    mock_post.return_value = err

    resp = llm._post_with_retry({}, {})
    assert resp.status_code == 403
    assert mock_post.call_count == 1


@mock.patch("time.sleep")
@mock.patch.object(llm._llm_session, "post")
def test_post_with_retry_exhausts_then_returns_last(mock_post, mock_sleep):
    """After max retries exhausted, return the last retryable response."""
    saved_retries = llm._MAX_RETRIES
    llm._MAX_RETRIES = 2
    try:
        err = mock.MagicMock()
        err.status_code = 503
        err.headers = {}
        mock_post.return_value = err

        resp = llm._post_with_retry({}, {})
        assert resp.status_code == 503
        # 1 initial + 2 retries = 3 calls
        assert mock_post.call_count == 3
    finally:
        llm._MAX_RETRIES = saved_retries


@mock.patch("time.sleep")
@mock.patch.object(llm._llm_session, "post")
def test_post_with_retry_network_error_then_success(mock_post, mock_sleep):
    """ConnectionError → retry → success."""
    import requests as req
    ok = mock.MagicMock()
    ok.status_code = 200
    mock_post.side_effect = [
        req.exceptions.ConnectionError("network down"),
        ok,
    ]
    resp = llm._post_with_retry({}, {})
    assert resp is ok
    assert mock_post.call_count == 2


@mock.patch("time.sleep")
@mock.patch.object(llm._llm_session, "post")
def test_post_with_retry_timeout_exhausted(mock_post, mock_sleep):
    """Timeout exceptions exhaust retries and re-raise."""
    import requests as req
    saved_retries = llm._MAX_RETRIES
    llm._MAX_RETRIES = 2
    try:
        mock_post.side_effect = req.exceptions.Timeout("timed out")
        with pytest.raises(req.exceptions.Timeout):
            llm._post_with_retry({}, {})
        # 1 initial + 2 retries = 3 attempts
        assert mock_post.call_count == 3
    finally:
        llm._MAX_RETRIES = saved_retries


@mock.patch("time.sleep")
@mock.patch.object(llm._llm_session, "post")
def test_post_with_retry_retry_after_header(mock_post, mock_sleep):
    """Retry-After header in response is used for backoff."""
    err = mock.MagicMock()
    err.status_code = 429
    err.headers = {"retry-after": "5"}
    ok = mock.MagicMock()
    ok.status_code = 200

    mock_post.side_effect = [err, ok]
    llm._post_with_retry({}, {})
    # Check that time.sleep was called with 5 (from Retry-After)
    assert mock_sleep.called
    assert mock_sleep.call_args[0][0] == 5.0


@mock.patch("time.sleep")
@mock.patch.object(llm._llm_session, "post")
def test_call_claude_retries_on_503(mock_post, mock_sleep):
    """End-to-end: call_claude retries on 503."""
    saved_retries = llm._MAX_RETRIES
    llm._MAX_RETRIES = 2
    try:
        err = mock.MagicMock()
        err.status_code = 503
        err.headers = {}
        err.text = "service unavailable"

        ok = mock.MagicMock()
        ok.status_code = 200
        ok.ok = True
        ok.json.return_value = {"content": [{"type": "text", "text": "success"}]}

        mock_post.side_effect = [err, ok]
        llm._api_key = "sk-test"

        result = llm.call_claude("hello")
        assert result == "success"
        assert mock_post.call_count == 2
    finally:
        llm._MAX_RETRIES = saved_retries


# ── 529 overloaded_error (Anthropic 私有码) ─────────────
# 这一档单独测: 529 曾经不在 _RETRYABLE_STATUS 里, 高峰期每个过载都零重试直接抛给用户,
# 而隔壁 500/503 老老实实退避三次。回归的代价是"问一句就报错", 所以钉死。

@mock.patch("time.sleep")
@mock.patch.object(llm._llm_session, "post")
def test_529_is_retried(mock_post, mock_sleep):
    """529 overloaded → 退避重试 → 第二次成功。"""
    err = mock.MagicMock(status_code=529, headers={})
    ok = mock.MagicMock(status_code=200, ok=True)
    mock_post.side_effect = [err, ok]

    resp = llm._post_with_retry({}, {})
    assert resp is ok
    assert mock_post.call_count == 2
    assert mock_sleep.called


@mock.patch("time.sleep")
@mock.patch.object(llm._llm_session, "post")
def test_overload_gets_more_attempts_than_generic(mock_post, mock_sleep):
    """过载档的重试上限独立于 _MAX_RETRIES —— 一般错误重试 1 次, 过载重试 4 次。"""
    saved_retries, saved_overload = llm._MAX_RETRIES, llm._OVERLOAD_RETRIES
    llm._MAX_RETRIES, llm._OVERLOAD_RETRIES = 1, 4
    try:
        for code, want_calls in ((500, 2), (529, 5), (429, 5)):
            mock_post.reset_mock()
            mock_post.return_value = mock.MagicMock(status_code=code, headers={})
            llm._post_with_retry({}, {})
            assert mock_post.call_count == want_calls, f"{code} 应尝试 {want_calls} 次"
    finally:
        llm._MAX_RETRIES, llm._OVERLOAD_RETRIES = saved_retries, saved_overload


@mock.patch("time.sleep")
@mock.patch.object(llm._llm_session, "post")
def test_529_exhausted_raises_llm_overloaded(mock_post, mock_sleep):
    """重试用尽后抛 LLMOverloaded(带 status), 让 agent loop 能识别是暂时性过载。"""
    saved = llm._OVERLOAD_RETRIES
    llm._OVERLOAD_RETRIES = 1
    try:
        mock_post.return_value = mock.MagicMock(
            status_code=529, headers={}, ok=False,
            text='{"type":"error","error":{"type":"overloaded_error"}}')
        llm._api_key = "sk-test"

        with pytest.raises(llm.LLMOverloaded) as ei:
            llm.call_claude_messages([{"role": "user", "content": "hi"}])
        assert ei.value.status == 529
        assert isinstance(ei.value, RuntimeError)      # 老的 except RuntimeError 仍能兜住
    finally:
        llm._OVERLOAD_RETRIES = saved


@mock.patch("time.sleep")
@mock.patch.object(llm._llm_session, "post")
def test_429_exhausted_raises_llm_overloaded_with_429(mock_post, mock_sleep):
    saved = llm._OVERLOAD_RETRIES
    llm._OVERLOAD_RETRIES = 0
    try:
        mock_post.return_value = mock.MagicMock(status_code=429, headers={}, ok=False, text="rate limited")
        llm._api_key = "sk-test"
        with pytest.raises(llm.LLMOverloaded) as ei:
            llm.call_claude_messages([{"role": "user", "content": "hi"}])
        assert ei.value.status == 429
        assert "限流" in str(ei.value)
    finally:
        llm._OVERLOAD_RETRIES = saved


@mock.patch.object(llm._llm_session, "post")
def test_non_overload_error_is_plain_runtimeerror(mock_post):
    """400 这类客户端错误不能被认成过载 —— 否则 agent 会白等两分钟再重发一遍同样错的请求。"""
    mock_post.return_value = mock.MagicMock(status_code=400, headers={}, ok=False, text="bad request")
    llm._api_key = "sk-test"
    with pytest.raises(RuntimeError) as ei:
        llm.call_claude_messages([{"role": "user", "content": "hi"}])
    assert not isinstance(ei.value, llm.LLMOverloaded)


def test_compute_backoff_overload_uses_higher_cap_with_jitter():
    """过载档封顶更高(几十秒), 且带 ±25% 抖动防同时重发。"""
    saved_cap, saved_over = llm._RETRY_MAX_BACKOFF_S, llm._OVERLOAD_MAX_BACKOFF_S
    llm._RETRY_MAX_BACKOFF_S, llm._OVERLOAD_MAX_BACKOFF_S = 8.0, 30.0
    try:
        assert llm._compute_backoff(10, None) == 8.0                    # 普通档: 8 秒封顶
        vals = [llm._compute_backoff(10, None, overload=True) for _ in range(50)]
        assert all(22.5 <= v <= 37.5 for v in vals), vals               # 30 ± 25%
        assert len(set(vals)) > 1                                       # 确实有抖动, 不是定值
        # 抖动只加在过载档, 普通档必须可预测(别的测试依赖精确值)
        assert llm._compute_backoff(1, None) == 2.0
    finally:
        llm._RETRY_MAX_BACKOFF_S, llm._OVERLOAD_MAX_BACKOFF_S = saved_cap, saved_over


def test_compute_backoff_overload_still_honors_retry_after():
    """服务端明确说了等多久就等多久, 不叠抖动。"""
    assert llm._compute_backoff(3, "7", overload=True) == 7.0


# ── prompt 缓存 ─────────────────────────────────────────
# agent 每轮把 system(1.0万 tokens) + 35 个工具定义(0.6万) 原样重发, 跑满 14 轮光前缀
# 就重复 21 万 tokens。这一组保证断点打在对的位置、且不会逐轮累积。

def _cache_on():
    llm._base_url = "https://api.anthropic.com"
    llm._PROMPT_CACHE = True


def test_cache_only_for_official_endpoint():
    """第三方兼容端点见到 cache_control 可能直接 400, 宁可不缓存也别弄坏兼容性。"""
    _cache_on()
    assert llm._cache_enabled() is True
    llm._base_url = "https://api.deepseek.com"
    assert llm._cache_enabled() is False
    llm._base_url = "https://my-proxy.internal/anthropic"
    assert llm._cache_enabled() is False


def test_cache_can_be_disabled_by_env():
    _cache_on()
    llm._PROMPT_CACHE = False
    try:
        assert llm._cache_enabled() is False
    finally:
        llm._PROMPT_CACHE = True


def test_breakpoint_goes_on_last_system_block():
    """顺序是 tools → system → messages, 断点落在 system 末尾才能覆盖 tools+system。"""
    blocks = [{"type": "text", "text": "身份"}, {"type": "text", "text": "长长的规则"}]
    out = llm._cached_system(blocks)
    assert "cache_control" not in out[0]
    assert out[1]["cache_control"] == {"type": "ephemeral"}
    assert blocks[1] == {"type": "text", "text": "长长的规则"}     # 原对象没被改


def test_cached_system_wraps_plain_string():
    out = llm._cached_system("一段 system")
    assert out == [{"type": "text", "text": "一段 system",
                    "cache_control": {"type": "ephemeral"}}]
    assert llm._cached_system("") == ""
    assert llm._cached_system(None) is None


def test_rolling_breakpoint_on_last_content_block():
    msgs = [
        {"role": "user", "content": "茅台怎么了"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "get_quote"}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "{}"},
                                     {"type": "tool_result", "tool_use_id": "t2", "content": "{}"}]},
    ]
    out = llm._cached_messages(msgs)
    assert "cache_control" not in out[1]["content"][0]
    assert out[2]["content"][0].get("cache_control") is None
    assert out[2]["content"][1]["cache_control"] == {"type": "ephemeral"}


def test_rolling_breakpoint_does_not_mutate_caller_list():
    """agent loop 复用同一个 messages 往后 append —— 就地改会让断点逐轮累积,
    超过 4 个 Anthropic 直接报错, 表现为"多问几轮就必失败"。"""
    msgs = [{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": "{}"}]}]
    llm._cached_messages(msgs)
    assert "cache_control" not in msgs[0]["content"][0]
    assert "cache_control" not in msgs[0]


def test_only_two_breakpoints_across_many_rounds():
    """模拟 agent 连跑 10 轮: 每轮都重新标一次, 发出去的断点数必须恒为 2。"""
    _cache_on()
    msgs = [{"role": "user", "content": "问题"}]
    sysb = [{"type": "text", "text": "规则"}]
    for rnd in range(10):
        payload_sys = llm._cached_system(sysb)
        payload_msgs = llm._cached_messages(msgs)
        n = sum(1 for b in payload_sys if isinstance(b, dict) and "cache_control" in b)
        for m in payload_msgs:
            c = m.get("content")
            if isinstance(c, list):
                n += sum(1 for b in c if isinstance(b, dict) and "cache_control" in b)
        assert n == 2, f"第 {rnd + 1} 轮断点数 {n}, 应恒为 2"
        # 下一轮: 追加 assistant + tool_result(照 agent loop 的写法)
        msgs.append({"role": "assistant", "content": [{"type": "tool_use", "id": f"t{rnd}"}]})
        msgs.append({"role": "user", "content": [{"type": "tool_result",
                                                  "tool_use_id": f"t{rnd}", "content": "{}"}]})


@mock.patch("time.sleep")
@mock.patch.object(llm._llm_session, "post")
def test_payload_carries_cache_control_end_to_end(mock_post, mock_sleep):
    _cache_on()
    llm._api_key = "sk-test"
    ok = mock.MagicMock(status_code=200, ok=True)
    ok.json.return_value = {"content": [{"type": "text", "text": "hi"}],
                            "usage": {"input_tokens": 5, "output_tokens": 2,
                                      "cache_creation_input_tokens": 16000,
                                      "cache_read_input_tokens": 0}}
    mock_post.return_value = ok

    msgs = [{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": "{}"}]}]
    llm.call_claude_messages(msgs, system="很长的规则", tools=[{"name": "get_quote"}])

    sent = mock_post.call_args[1]["json"]
    assert sent["system"][-1]["cache_control"] == {"type": "ephemeral"}
    assert sent["messages"][-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in sent["tools"][0]        # 只靠 system 那个断点覆盖
    assert "cache_control" not in msgs[0]["content"][0]   # 调用方的对象没被污染


@mock.patch("time.sleep")
@mock.patch.object(llm._llm_session, "post")
def test_third_party_endpoint_gets_no_cache_control(mock_post, mock_sleep):
    llm._base_url = "https://api.deepseek.com"
    llm._api_key = "sk-test"
    ok = mock.MagicMock(status_code=200, ok=True)
    ok.json.return_value = {"content": [], "usage": {}}
    mock_post.return_value = ok

    llm.call_claude_messages([{"role": "user", "content": "hi"}], system="规则")
    sent = mock_post.call_args[1]["json"]
    assert sent["system"] == "规则"                      # 原样, 没被包成块
    assert sent["messages"][0]["content"] == "hi"


def test_usage_stats_hit_rate():
    """命中率 = 从缓存读到的 prompt tokens / 全部 prompt tokens。"""
    with llm._usage_lock:
        for k in llm._usage_totals:
            llm._usage_totals[k] = 0
    llm._record_usage({"usage": {"input_tokens": 1000, "output_tokens": 200,
                                 "cache_creation_input_tokens": 16000,
                                 "cache_read_input_tokens": 0}})          # 第一轮: 写缓存
    llm._record_usage({"usage": {"input_tokens": 500, "output_tokens": 300,
                                 "cache_creation_input_tokens": 0,
                                 "cache_read_input_tokens": 16000}})      # 第二轮: 命中
    s = llm.get_usage_stats()
    assert s["calls"] == 2
    assert s["cache_read"] == 16000 and s["cache_write"] == 16000
    assert s["prompt_total"] == 1000 + 16000 + 500 + 16000
    assert s["hit_rate"] == round(16000 / 33500 * 100, 1)


def test_usage_stats_tolerates_missing_usage():
    """有的兼容端点不回 usage —— 不能因此抛错把整次问答弄挂。"""
    llm._record_usage({})
    llm._record_usage(None)
    assert isinstance(llm.get_usage_stats()["hit_rate"], float)
