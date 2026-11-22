"""llm_codex 的请求构造与令牌处理(不联网)。"""
import base64
import json
import time

from services import llm_codex


def _home(tmp_path, monkeypatch, exp_in=3600):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "models_cache.json").write_text(json.dumps({"client_version": "0.170.0", "models": [
        {"slug": "gpt-6-astra", "visibility": "list", "model_messages": {"instructions_template": "CODEX-6"}},
        {"slug": "gpt-6.2-sol", "visibility": "list", "base_instructions": "CODEX-62"},
    ]}))
    (tmp_path / "config.toml").write_text('model = "gpt-6-astra"\n')
    payload = base64.urlsafe_b64encode(json.dumps({"exp": time.time() + exp_in}).encode()).decode().rstrip("=")
    (tmp_path / "auth.json").write_text(json.dumps({"auth_mode": "chatgpt", "tokens": {
        "access_token": f"h.{payload}.s", "refresh_token": "rt-1", "id_token": "", "account_id": "acct-1"}}))


def test_body_puts_codex_instructions_on_top_and_own_prompt_first(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    msgs = [{"role": "user", "content": "示例股份怎么样"}]
    b = llm_codex.build_body(msgs, "你是行情助手", "gpt-6-astra", None, "low")
    assert b["instructions"] == "CODEX-6"
    assert b["input"][0] == {"type": "message", "role": "developer",
                             "content": [{"type": "input_text", "text": "你是行情助手"}]}
    assert b["store"] is False and b["stream"] is True
    assert "max_output_tokens" not in b and b["reasoning"]["effort"] == "low"


def test_newer_model_gets_codex_instructions_in_input(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    b = llm_codex.build_body([{"role": "user", "content": "问"}], "系统", "gpt-6.2-sol", None, None)
    assert "instructions" not in b
    assert [i["content"][0]["text"] for i in b["input"][:2]] == ["CODEX-62", "系统"]


def test_cache_key_stable_across_turns(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    first = [{"role": "user", "content": "问"}]
    later = first + [{"role": "assistant", "content": "答"}, {"role": "user", "content": "再问"}]
    k1 = llm_codex.build_body(first, "系统", "gpt-6-astra", None, None)["prompt_cache_key"]
    k2 = llm_codex.build_body(later, "系统", "gpt-6-astra", None, None)["prompt_cache_key"]
    assert k1 == k2


def test_pick_model_maps_claude_names_to_codex_default(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    assert llm_codex.pick_model("claude-opus-5-5") == "gpt-6-astra"
    assert llm_codex.pick_model("gpt-6.2-sol") == "gpt-6.2-sol"


def test_fresh_token_used_without_refresh(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    monkeypatch.setattr(llm_codex, "_refresh", lambda a: (_ for _ in ()).throw(AssertionError("不该刷新")))
    tok, acct = llm_codex.token()
    assert tok.startswith("h.") and acct == "acct-1"


def test_expiring_token_refreshes_and_writes_back(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch, exp_in=60)

    class R:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"access_token": "new-at", "refresh_token": "rt-2", "id_token": "new-id"}

    import requests
    monkeypatch.setattr(requests, "post", lambda *a, **k: R())
    tok, _ = llm_codex.token()
    assert tok == "new-at"
    saved = json.loads((tmp_path / "auth.json").read_text())
    assert saved["tokens"]["refresh_token"] == "rt-2" and saved["tokens"]["account_id"] == "acct-1"
