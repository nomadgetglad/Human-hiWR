import os
"""The two model backends. Both run the same tool loop; the runner hands them a tool executor that has
already been wrapped with the access and privacy rules.

local  Ollama on this computer (http://localhost:11434). Nothing leaves the machine.
cloud  The Claude API through the official anthropic SDK. Only pseudonymized text reaches it.
"""
import json
import urllib.error
import urllib.request

MAX_TOOL_ROUNDS = 25


class BackendUnavailable(Exception):
    """The model couldn't be reached. Hybrid mode may fall back to local."""


def _result_text(result):
    return json.dumps(result, default=str, ensure_ascii=False)


class OllamaBackend:
    name = "local"

    def __init__(self, cfg, post=None):
        c = cfg["local"]
        self.model, self.host, self.num_ctx, self.timeout = c["model"], c["host"].rstrip("/"), c["num_ctx"], c["timeout_seconds"]
        # num_gpu = 0 runs the model on the CPU: slower, but works when the graphics card driver is failing
        ng = c.get("num_gpu", os.environ.get("HUMAN_NUM_GPU", ""))
        self.options = {"num_ctx": self.num_ctx, **({"num_gpu": int(ng)} if str(ng).strip() != "" else {})}
        self._post = post or self._http_post

    def _http_post(self, url, payload):
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:     # Ollama is up but failed (e.g. the GPU didn't load); say what it said
            try:
                detail = json.loads(e.read()).get("error") or e.reason
            except ValueError:
                detail = e.reason
            raise BackendUnavailable(f"Ollama returned an error ({e.code}: {detail}). Asking again usually works.")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise BackendUnavailable(f"Ollama isn't answering at {self.host} ({e}). Is it running? Try `ollama serve`.")

    def turn(self, system, history, user_text, tools, execute):
        msgs = [{"role": "system", "content": system}, *history, {"role": "user", "content": user_text}]
        spec = [{"type": "function", "function": t} for t in tools]
        nudged = False
        for _ in range(MAX_TOOL_ROUNDS):
            r = self._post(f"{self.host}/api/chat", {"model": self.model, "messages": msgs, "tools": spec, "stream": False,
                                                     "options": self.options})
            if "error" in r:
                raise BackendUnavailable(f"Ollama error: {r['error']}")
            m = r.get("message", {})
            calls = m.get("tool_calls") or []
            msgs.append({k: v for k, v in m.items() if k in ("role", "content", "tool_calls")})
            if not calls:
                text = (m.get("content") or "").strip()
                if text or nudged:
                    return text
                # gpt-oss sometimes stops after thinking, with no answer. Ask once for the answer itself.
                nudged = True
                msgs.append({"role": "user", "content": "Write your answer to my question now."})
                continue
            for c in calls:
                fn = c.get("function", {})
                args = fn.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {}
                msgs.append({"role": "tool", "tool_name": fn.get("name"), "content": _result_text(execute(fn.get("name"), args))})
        return "I ran out of steps on this one. Try narrowing the question."


class ClaudeBackend:
    name = "cloud"

    def __init__(self, cfg, client=None):
        c = cfg["cloud"]
        self.model, self.effort, self.max_tokens = c["model"], c["effort"], c["max_tokens"]
        self._client = client

    @property
    def client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError:
                raise BackendUnavailable("the anthropic package isn't installed (pip install anthropic)")
            self._client = anthropic.Anthropic()
        return self._client

    def _create(self, **kw):
        try:
            import anthropic
            errors = (anthropic.APIConnectionError, anthropic.AuthenticationError, anthropic.RateLimitError,
                      anthropic.InternalServerError)
        except ImportError:
            errors = ()
        try:
            return self.client.beta.messages.create(**kw)
        except errors as e:
            raise BackendUnavailable(f"Claude API unavailable: {e}")
        except TypeError as e:
            if "authentication" not in str(e):
                raise
            raise BackendUnavailable("no Claude API key found. Set ANTHROPIC_API_KEY or run `ant auth login`.")

    def turn(self, system, history, user_text, tools, execute):
        msgs = [*history, {"role": "user", "content": user_text}]
        spec = [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in tools]
        for _ in range(MAX_TOOL_ROUNDS):
            r = self._create(
                model=self.model, max_tokens=self.max_tokens,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                tools=spec, messages=msgs,
                thinking={"type": "adaptive"}, output_config={"effort": self.effort},
                betas=["server-side-fallback-2026-07-01"], fallbacks="default")
            if r.stop_reason == "refusal":
                return "I can't help with that one here. I can connect you with a person instead."
            msgs.append({"role": "assistant", "content": r.content})
            if r.stop_reason == "pause_turn":
                continue
            calls = [b for b in r.content if b.type == "tool_use"]
            if r.stop_reason != "tool_use" or not calls:
                text = "".join(b.text for b in r.content if b.type == "text").strip()
                if r.stop_reason == "max_tokens":
                    text += "\n\n(Cut off at the length limit. Ask me to continue.)"
                return text
            results = [{"type": "tool_result", "tool_use_id": c.id, "content": _result_text(execute(c.name, c.input))}
                       for c in calls]
            msgs.append({"role": "user", "content": results})
        return "I ran out of steps on this one. Try narrowing the question."
