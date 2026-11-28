"""The app's rules and design as small chunks in a local vector index.

Instead of pasting whole method files into the prompt, each request gets:
  1. the hard rules, always (knowledge/hard_rules.md), whatever the search finds, and
  2. the few method, design and policy chunks closest in meaning to the question.

Retrieval is for context only. The hard rules are enforced in code (see each chunk's enforced_by). Nothing in
this file decides who may see or do anything: a missing index, a bad search or a model that ignores the
prompt can't move money, raise a level or show a W-2.

Embeddings are made on this computer only, never in the cloud, because questions can contain real names:
  ollama   Ollama's embedding model (nomic-embed-text by default): `ollama pull nomic-embed-text`
  hash     a built-in word-hashing embedder, standard library only. Used when Ollama isn't answering, and in tests.

    python -m agent.vectors build                     # embed every chunk (cached, only changed ones re-run)
    python -m agent.vectors search "fish came short" --level 2
"""
import argparse
import hashlib
import json
import math
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
KNOWLEDGE_DIR = HERE / "knowledge"
METHODS_DIR = HERE / "prompts" / "methods"
CACHE_DIR = HERE / ".vector_cache"
KINDS = ("hard", "method", "design", "policy")


class EmbedderUnavailable(Exception):
    """The embedding model couldn't be reached. The index falls back to the hash embedder."""


@dataclass
class Chunk:
    id: str
    kind: str
    text: str
    tags: list = field(default_factory=list)
    title: str = ""
    status: str = ""
    min_level: int = 0
    enforced_by: list = field(default_factory=list)
    gap: str = ""
    source: str = ""

    def embed_text(self):
        """What gets embedded: the title and tags help short chunks match the words people actually use."""
        return f"{self.title}. {', '.join(self.tags)}. {self.text}"

    def prompt_text(self):
        label = f" ({self.status})" if self.status else ""
        return f"[{self.id}{label}] {self.text}"


# ---------- reading the chunks ----------

_FIELD = re.compile(r"^(kind|tags|status|min_level|enforced_by|gap):\s*(.*)$")


def parse_knowledge(path):
    """A knowledge file is `## chunk.id`, then `key: value` lines, a blank line, then the text."""
    chunks = []
    path = Path(path)
    for block in re.split(r"^## ", path.read_text(encoding="utf-8"), flags=re.M)[1:]:
        head, _, rest = block.partition("\n")
        meta, body = {}, []
        lines = rest.splitlines()
        i = 0
        while i < len(lines) and (m := _FIELD.match(lines[i].strip())):
            meta[m.group(1)] = m.group(2).strip()
            i += 1
        body = " ".join(line.strip() for line in lines[i:] if line.strip())
        cid = head.strip()
        chunks.append(Chunk(
            id=cid, kind=meta.get("kind", ""), text=body,
            tags=[t.strip() for t in meta.get("tags", "").split(",") if t.strip()],
            title=cid.split(".", 1)[-1].replace("_", " "),
            status=meta.get("status", ""),
            min_level=int(meta.get("min_level", 0)),
            enforced_by=[e.strip() for e in meta.get("enforced_by", "").split(",") if e.strip()],
            gap=meta.get("gap", ""), source=path.name))
    return chunks


def chunk_method(path):
    """Splits a method file into one chunk per paragraph, so a search can return just the part that matters.

    The paragraphs keep the method's name as a tag, so the keyword router can still boost them.
    """
    path = Path(path)
    name = path.stem
    paras = [p.strip() for p in re.split(r"\n\s*\n", path.read_text(encoding="utf-8")) if p.strip()]
    title = paras[0].lstrip("# ").replace("Method: ", "") if paras and paras[0].startswith("#") else name
    pieces = []
    for p in paras[1:] if paras[0].startswith("#") else paras:
        pieces += _split_list(p)
    return [Chunk(id=f"method.{name}.{n}", kind="method", text=text, tags=[name], title=title,
                  source=f"prompts/methods/{path.name}")
            for n, text in enumerate(pieces, start=1)]


_ITEM = re.compile(r"^(?:[a-z]\.|\d+\.|- )\s")


def _split_list(para, max_len=700):
    """A short paragraph stays whole. A long one that is a list ("a. ...", "1. ...", "- ...") becomes one piece
    per top-level item, each led by the paragraph's opening line so it still makes sense on its own."""
    flat = " ".join(line.strip() for line in para.splitlines())
    lines = para.splitlines()
    if len(flat) <= max_len or not any(_ITEM.match(line) for line in lines):
        return [flat]
    lead, items = [], []
    for line in lines:
        if _ITEM.match(line):
            items.append([line.strip()])
        elif items:
            items[-1].append(line.strip())       # an indented sub-point belongs to the item above it
        else:
            lead.append(line.strip())
    lead = " ".join(lead)
    out = [" ".join(i) for i in items]
    if lead and len(lead) <= 200:
        return [f"{lead} {i}" for i in out]
    return ([lead] if lead else []) + out


def load_chunks(knowledge_dir=KNOWLEDGE_DIR, methods_dir=METHODS_DIR):
    chunks = []
    for p in sorted(Path(knowledge_dir).glob("*.md")):
        if p.name.lower() != "readme.md":
            chunks += parse_knowledge(p)
    for p in sorted(Path(methods_dir).glob("*.md")):
        chunks += chunk_method(p)
    return chunks


# ---------- embedders ----------

_WORD = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")
_STOP = set("a an and are as at be by can for from has have in is it its of on or so that the their them then "
            "there these this to was we what when where which who will with you your i my me our do does did not "
            "no any all".split())


def _words(text):
    out = []
    for w in _WORD.findall(text.lower()):
        if w in _STOP:
            continue
        if len(w) > 4 and w.endswith("ies"):
            w = w[:-3] + "y"
        elif len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.append(w)
    return out


class HashEmbedder:
    """Turns text into a vector by hashing its words and word pairs into fixed slots.

    It only knows words, not meaning ("short shipment" and "delivery came light" share nothing), so it's the
    fallback, not the goal. It needs nothing installed and gives the same answer every time.
    """
    name = "hash"

    def __init__(self, dim=4096):
        self.dim = dim
        self.model = f"hash-{dim}"

    def _vec(self, text):
        v = [0.0] * self.dim
        ws = _words(text)
        feats = ws + [f"{a} {b}" for a, b in zip(ws, ws[1:])]
        for f in feats:
            h = int.from_bytes(hashlib.md5(f.encode()).digest()[:8], "big")
            v[h % self.dim] += 1.0 if (h >> 63) == 0 else -1.0
        return v

    def embed(self, texts, purpose="document"):
        return [self._vec(t) for t in texts]


class OllamaEmbedder:
    """Ollama's /api/embed on this computer. nomic-embed-text wants a task prefix on every text."""
    name = "ollama"
    PREFIX = {"document": "search_document: ", "query": "search_query: "}

    def __init__(self, host="http://localhost:11434", model="nomic-embed-text", timeout=60, post=None):
        self.host, self.model, self.timeout = host.rstrip("/"), model, timeout
        self._post = post or self._http_post

    def _http_post(self, url, payload):
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read())
        except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError) as e:
            raise EmbedderUnavailable(f"Ollama isn't answering at {self.host} ({e})")

    def embed(self, texts, purpose="document"):
        prefix = self.PREFIX[purpose] if "nomic" in self.model else ""
        r = self._post(f"{self.host}/api/embed", {"model": self.model, "input": [prefix + t for t in texts]})
        if "error" in r:
            raise EmbedderUnavailable(f"Ollama embed error: {r['error']} (try `ollama pull {self.model}`)")
        vecs = r.get("embeddings") or []
        if len(vecs) != len(texts):
            raise EmbedderUnavailable("Ollama returned the wrong number of embeddings")
        return vecs


def _norm(v):
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n else v


def cosine(a, b):
    """Both vectors are already length 1, so the dot product is the cosine: 1 same direction, 0 unrelated."""
    return sum(x * y for x, y in zip(a, b))


# ---------- the index ----------

class RuleIndex:
    def __init__(self, chunks=None, embedder=None, cache_dir=CACHE_DIR, fallback=True):
        self.chunks = chunks if chunks is not None else load_chunks()
        self.embedder = embedder or HashEmbedder()
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.fallback = fallback
        self.note = None              # set when the index had to fall back to the hash embedder
        self.vectors = None

    @property
    def hard(self):
        return [c for c in self.chunks if c.kind == "hard"]

    def _cache_file(self):
        safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", f"{self.embedder.name}-{self.embedder.model}")
        return self.cache_dir / f"{safe}.json"

    def _embed_all(self):
        texts = [c.embed_text() for c in self.chunks]
        keys = [hashlib.sha256(t.encode()).hexdigest() for t in texts]
        cache = {}
        use_cache = self.cache_dir is not None and self.embedder.name != "hash"
        if use_cache and self._cache_file().exists():
            cache = json.loads(self._cache_file().read_text(encoding="utf-8"))
        todo = [i for i, k in enumerate(keys) if k not in cache]
        if todo:
            for i, v in zip(todo, self.embedder.embed([texts[i] for i in todo], "document")):
                cache[keys[i]] = _norm(v)
        if use_cache and todo:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            keep = {k: cache[k] for k in keys}          # drop vectors for chunks that no longer exist
            self._cache_file().write_text(json.dumps(keep), encoding="utf-8")
        self.embedded_now = len(todo)
        return [cache[k] for k in keys]

    def _fall_back(self, err):
        if not self.fallback or isinstance(self.embedder, HashEmbedder):
            raise err
        self.note = f"{err}. Using the built-in word matcher for rules until it's back."
        self.embedder = HashEmbedder()
        self.vectors = None

    def build(self):
        try:
            self.vectors = self._embed_all()
        except EmbedderUnavailable as e:
            self._fall_back(e)
            self.vectors = self._embed_all()
        return self

    def search(self, query, level=0, k=6, boost_tags=(), kinds=("method", "design", "policy")):
        """The k chunks closest to the query that this level may see. Hard rules aren't searched: they're
        always included by the caller."""
        if self.vectors is None:
            self.build()
        try:
            q = _norm(self.embedder.embed([query], "query")[0])
        except EmbedderUnavailable as e:
            self._fall_back(e)
            self.build()
            q = _norm(self.embedder.embed([query], "query")[0])
        boost = set(boost_tags)
        scored = []
        for c, v in zip(self.chunks, self.vectors):
            if c.kind not in kinds or c.min_level > level:
                continue
            s = cosine(q, v) + (0.15 if boost & set(c.tags) else 0.0)
            if s > 0:
                scored.append((s, c))
        scored.sort(key=lambda sc: -sc[0])
        return scored[:k]

    def context(self, query, level=0, k=6, boost_tags=()):
        """The block the runner puts in the system prompt. Hard rules first, always, then the search hits."""
        parts = ["# Hard rules (always on, and enforced in code whatever this prompt says)"]
        parts += [f"- {c.text}" for c in self.hard]
        hits = self.search(query, level, k, boost_tags)
        if hits:
            parts.append("# Rules and app design that match this request (call load_method for a full method)")
            parts += [f"- {c.prompt_text()}" for _, c in hits]
        return "\n".join(parts), [c.id for _, c in hits]


def make_index(cfg, chunks=None, cache_dir=CACHE_DIR):
    """Builds the index the config asks for. embedder "ollama" falls back to "hash" when Ollama is down."""
    r = cfg["rules"]
    if r["embedder"] == "ollama":
        emb = OllamaEmbedder(cfg["local"]["host"], r["embed_model"], timeout=r["embed_timeout_seconds"])
    elif r["embedder"] == "hash":
        emb = HashEmbedder()
    else:
        raise ValueError(f"rules.embedder must be 'ollama' or 'hash', not {r['embedder']!r}")
    return RuleIndex(chunks, emb, cache_dir=cache_dir)


def main(argv=None):
    from . import config as config_mod
    ap = argparse.ArgumentParser(prog="python -m agent.vectors", description="Rules and design vector index")
    ap.add_argument("command", choices=["build", "search", "list"])
    ap.add_argument("query", nargs="*")
    ap.add_argument("--level", type=int, default=0, help="access level to search as (0 to 4)")
    ap.add_argument("--k", type=int, default=6)
    ap.add_argument("--config")
    a = ap.parse_args(argv)
    idx = make_index(config_mod.load(a.config))
    if a.command == "list":
        for c in idx.chunks:
            print(f"{c.id:40} {c.kind:7} {c.status or '':9} {', '.join(c.tags)}")
        return
    idx.build()
    if idx.note:
        print(idx.note)
    if a.command == "build":
        print(f"{len(idx.chunks)} chunks, {idx.embedded_now} embedded now, model {idx.embedder.model}")
        return
    for s, c in idx.search(" ".join(a.query), a.level, a.k):
        print(f"{s:.3f}  {c.id}: {c.text[:110]}")


if __name__ == "__main__":
    main()
