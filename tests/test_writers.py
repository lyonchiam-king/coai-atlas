import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from atlas.llm import ClaudeLLM, LLMError, OllamaLLM, make_writer


class FakeOllama:
    def __init__(self, models=("llama3.1:8b", "qwen3:8b"), reply="<think>hmm</think>\nHi Aisha!", code=200):
        self.models, self.reply, self.code, self.bodies = list(models), reply, code, []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass

            def _json(self, code, obj):
                raw = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                self._json(200, {"models": [{"name": m} for m in outer.models]})

            def do_POST(self):
                outer.bodies.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                self._json(outer.code, {"message": {"role": "assistant", "content": outer.reply}})

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}"


@pytest.fixture
def ollama():
    o = FakeOllama()
    yield o
    o.srv.shutdown()


def test_ollama_lists_what_is_installed_and_strips_thinking(ollama):
    assert OllamaLLM.list_models(ollama.url) == ["llama3.1:8b", "qwen3:8b"]
    out = OllamaLLM("qwen3:8b", ollama.url).complete("sys", "write it")
    assert out == "Hi Aisha!"
    b = ollama.bodies[0]
    assert b["model"] == "qwen3:8b" and b["stream"] is False
    assert b["messages"] == [{"role": "system", "content": "sys"}, {"role": "user", "content": "write it"}]


def test_ollama_failures_are_llm_errors_that_name_the_problem(ollama):
    ollama.code = 404
    with pytest.raises(LLMError, match="pulled"):
        OllamaLLM("missing:1b", ollama.url).complete("s", "p")
    with pytest.raises(LLMError, match="not running"):
        OllamaLLM("x", "http://127.0.0.1:9", timeout=2).complete("s", "p")
    assert OllamaLLM.list_models("http://127.0.0.1:9") == []


def test_writer_choice(ollama, tmp_path, monkeypatch):
    pytest.importorskip("anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    env = tmp_path / ".env"
    o = {"ollama_url": ollama.url, "ollama_model": "qwen3:8b"}
    assert make_writer({"writer": "templates", **o}, env) is None
    assert make_writer({"writer": "auto", **o}, env).name == "Ollama (qwen3:8b)"      # no key: Ollama
    assert make_writer({"writer": "auto", **o, "ollama_model": "gone:7b"}, env) is None  # not installed
    assert make_writer({"writer": "claude", **o}, env) is None                         # asked, no key
    env.write_text("ANTHROPIC_API_KEY=sk-test\n")
    assert make_writer({"writer": "auto", **o}, env).name.startswith("Claude")        # key wins in auto
    assert make_writer({"writer": "ollama", **o}, env).name.startswith("Ollama")
    assert make_writer({"writer": "claude", "claude_model": "claude-haiku-4-5"}, env).model == "claude-haiku-4-5"


@pytest.mark.parametrize("model,effort,fallbacks", [
    ("claude-opus-5-5", True, True), ("claude-sonnet-5-5", True, True), ("claude-haiku-4-5", False, False)])
def test_each_claude_model_gets_only_the_parameters_it_accepts(model, effort, fallbacks):
    kw = ClaudeLLM(model, client=object()).request("s", "p")
    assert kw["model"] == model
    assert ("output_config" in kw) is effort
    assert ("fallbacks" in kw) is fallbacks and ("betas" in kw) is fallbacks
