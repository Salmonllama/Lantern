"""Minimal Ollama HTTP client (standard library only, no GTK imports).

Talks to a local Ollama server (default http://127.0.0.1:11434) so all
inference stays on this machine.
"""

import json
import urllib.error
import urllib.request

DEFAULT_HOST = "http://127.0.0.1:11434"


class OllamaError(Exception):
    pass


class OllamaClient:
    def __init__(self, host=DEFAULT_HOST, timeout=10):
        self.host = host.rstrip("/")
        self.timeout = timeout

    # ---- helpers -------------------------------------------------------
    def _request(self, path, payload=None, timeout=None):
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.host + path, data=data, headers=headers)
        try:
            return urllib.request.urlopen(req, timeout=timeout or self.timeout)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            try:
                body = json.loads(body).get("error", body)
            except ValueError:
                pass
            raise OllamaError(f"Ollama returned {e.code}: {body}") from e
        except (urllib.error.URLError, OSError) as e:
            raise OllamaError(
                f"Can't reach Ollama at {self.host}. Is it running? ({e})"
            ) from e

    def _stream(self, path, payload, cancel=None):
        """Yield decoded JSON objects from an NDJSON streaming endpoint."""
        # Long read timeout: a big model can take a while to load.
        resp = self._request(path, payload, timeout=600)
        try:
            for raw in resp:
                if cancel is not None and cancel.is_set():
                    break
                line = raw.strip()
                if not line:
                    continue
                obj = json.loads(line)
                if "error" in obj:
                    raise OllamaError(obj["error"])
                yield obj
        finally:
            resp.close()

    # ---- API -----------------------------------------------------------
    def version(self):
        with self._request("/api/version") as r:
            return json.load(r).get("version", "unknown")

    def list_models(self):
        with self._request("/api/tags") as r:
            models = json.load(r).get("models", [])
        return sorted(m["name"] for m in models)

    def chat(self, model, messages, cancel=None):
        """Stream a chat reply. Yields text chunks."""
        payload = {"model": model, "messages": messages, "stream": True}
        for obj in self._stream("/api/chat", payload, cancel):
            chunk = obj.get("message", {}).get("content", "")
            if chunk:
                yield chunk
            if obj.get("done"):
                break

    def pull(self, model, cancel=None):
        """Download a model. Yields (status_text, fraction_or_None)."""
        payload = {"model": model, "stream": True}
        for obj in self._stream("/api/pull", payload, cancel):
            total, done = obj.get("total"), obj.get("completed")
            frac = (done / total) if total and done is not None else None
            yield obj.get("status", ""), frac
