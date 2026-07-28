"""
Debug hatch — localhost REPL for inspecting a live Py4GW widget process.

Stdlib only. On first import, boots an HTTP server on 127.0.0.1:9977 serving a
browser REPL. Python code POSTed to /eval runs inside the game process against
a persistent namespace.

Usage
-----
At a "breakpoint" site in any widget:

    from Core.debug_hatch import snap
    snap("bag_scan", item=item, model_id=model_id, mods=mods)

Then open http://127.0.0.1:9977 in a browser:

    bag_scan['mods']
    for name in snaps(): print(name)

Namespace helpers injected for you: snap, snaps, ns, clear, help_repl.

Safety
------
The server runs on a background thread. Read-only inspection is generally safe.
Mutating game state may race with the main loop; keep writes short and don't
run long loops from the REPL (they block that server-thread frame).
"""

from __future__ import annotations

import ast
import io
import json
import sys
import threading
import traceback
from contextlib import redirect_stdout, redirect_stderr
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "127.0.0.1"
PORT = 9977

_namespace: dict = {}
_snapshots: dict = {}
_lock = threading.Lock()


# ---- Public snapshot / helper API ---------------------------------------

def snap(tag: str, _globals: bool = False, **overrides) -> None:
    """Capture a named snapshot of the caller's scope.

    Call with no args to grab caller's locals:      snap("bag_scan")
    Pass keyword overrides to add/replace entries:  snap("bag_scan", derived=x)
    Set _globals=True to also include module-level globals (useful for widgets
    where module state lives in globals rather than locals):
        snap("team_inv", _globals=True)

    Cheap; safe to call every frame. Stores references, not deep copies —
    values reflect current state when you inspect them.
    """
    try:
        frame = sys._getframe(1)  # caller's frame
    except ValueError:
        frame = None

    data: dict = {}
    if frame is not None:
        if _globals:
            data.update(
                (k, v) for k, v in frame.f_globals.items()
                if not k.startswith("__")
            )
        data.update(frame.f_locals)
    data.update(overrides)

    if frame is not None:
        data.setdefault(
            "__src__",
            f"{frame.f_code.co_filename}:{frame.f_lineno} in {frame.f_code.co_name}",
        )
    _snapshots[tag] = data
    _namespace[tag] = data


def snaps() -> dict:
    """Return a shallow copy of every recorded snapshot."""
    return dict(_snapshots)


def ns() -> list:
    """List user-visible names in the REPL namespace."""
    return sorted(k for k in _namespace if not k.startswith("_"))


def clear() -> None:
    """Wipe all snapshots and non-builtin names."""
    _snapshots.clear()
    for k in list(_namespace):
        if k not in _BUILTIN_NAMES and not k.startswith("__"):
            del _namespace[k]


def help_repl() -> str:
    return (
        "Debug hatch REPL — available helpers:\n"
        "  snap(tag, **data)  capture a named snapshot from widget code\n"
        "  snaps()            dict of every recorded snapshot\n"
        "  ns()               list names in this namespace\n"
        "  clear()            drop all snapshots + names\n"
        "  help_repl()        this text\n"
        "  _                  last expression result (like Python's REPL)"
    )


_BUILTIN_NAMES = {"snap", "snaps", "ns", "clear", "help_repl", "_"}
_namespace.update({
    "snap": snap,
    "snaps": snaps,
    "ns": ns,
    "clear": clear,
    "help_repl": help_repl,
})


# ---- JSON-safe serializer (for the live State view) --------------------

_MAX_DEPTH = 6
_MAX_SEQ = 200
_MAX_STR = 500


def _to_jsonable(value, depth: int = 0, seen: set | None = None):
    """Convert any Python value to something json.dumps can handle.

    Non-primitives become {"__repr__": "...", "__type__": "..."} nodes.
    Recursive containers are safe (cycles broken via id-tracking). Big
    strings/sequences are truncated so a bloated snapshot doesn't drown
    the browser.
    """
    if seen is None:
        seen = set()

    # Primitives pass through.
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if len(value) > _MAX_STR:
            return value[:_MAX_STR] + f"…(+{len(value) - _MAX_STR} chars)"
        return value

    if depth >= _MAX_DEPTH:
        return {"__truncated__": "max_depth", "__type__": type(value).__name__}

    vid = id(value)
    if vid in seen:
        return {"__cycle__": True, "__type__": type(value).__name__}
    seen = seen | {vid}

    # Mappings — preserve key order.
    if isinstance(value, dict):
        out = {}
        for i, (k, v) in enumerate(value.items()):
            if i >= _MAX_SEQ:
                out["__truncated__"] = f"+{len(value) - _MAX_SEQ} more keys"
                break
            key = k if isinstance(k, (str, int, float, bool)) else repr(k)
            out[str(key)] = _to_jsonable(v, depth + 1, seen)
        return out

    # Sequences.
    if isinstance(value, (list, tuple, set, frozenset)):
        seq = list(value)
        truncated = None
        if len(seq) > _MAX_SEQ:
            truncated = f"+{len(seq) - _MAX_SEQ} more items"
            seq = seq[:_MAX_SEQ]
        out = [_to_jsonable(v, depth + 1, seen) for v in seq]
        if truncated:
            out.append({"__truncated__": truncated})
        return out

    # Bytes.
    if isinstance(value, (bytes, bytearray)):
        return {"__type__": type(value).__name__, "__hex__": value[:_MAX_STR].hex()}

    # Callables / modules / everything else — just repr.
    try:
        text = repr(value)
    except Exception as e:
        text = f"<repr failed: {type(e).__name__}: {e}>"
    if len(text) > _MAX_STR:
        text = text[:_MAX_STR] + f"…(+{len(text) - _MAX_STR} chars)"
    return {"__repr__": text, "__type__": type(value).__name__}


def _snapshots_jsonable() -> dict:
    with _lock:
        return {tag: _to_jsonable(data) for tag, data in _snapshots.items()}


# ---- REPL evaluator -----------------------------------------------------

def _run_source(source: str) -> dict:
    """Compile+run REPL source, return {result, stdout, stderr, error}."""
    stdout, stderr = io.StringIO(), io.StringIO()
    result_repr: str | None = None
    error = False

    with _lock:
        try:
            tree = ast.parse(source, mode="exec")
        except SyntaxError:
            return {
                "result": None,
                "stdout": "",
                "stderr": traceback.format_exc(),
                "error": True,
            }

        try:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                if not tree.body:
                    pass
                elif isinstance(tree.body[-1], ast.Expr):
                    # Exec every statement except the last, then eval the last
                    # expression so its value is returned (IPython-style).
                    pre = ast.Module(body=tree.body[:-1], type_ignores=[])
                    expr = ast.Expression(body=tree.body[-1].value)
                    if pre.body:
                        exec(compile(pre, "<repl>", "exec"), _namespace)
                    value = eval(compile(expr, "<repl>", "eval"), _namespace)
                    if value is not None:
                        result_repr = repr(value)
                        _namespace["_"] = value
                else:
                    exec(compile(tree, "<repl>", "exec"), _namespace)
        except SystemExit:
            error = True
            stderr.write("SystemExit ignored inside REPL\n")
        except BaseException:
            error = True
            stderr.write(traceback.format_exc())

    return {
        "result": result_repr,
        "stdout": stdout.getvalue(),
        "stderr": stderr.getvalue(),
        "error": error,
    }


# ---- Browser page (inline HTML/CSS/JS) ----------------------------------

_INDEX_HTML = r"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Py4GW Debug Hatch</title>
<style>
  html, body { height: 100%; margin: 0; }
  body {
    font-family: Consolas, 'Courier New', monospace;
    background: #1e1e1e; color: #d4d4d4;
    display: flex; flex-direction: column;
  }
  header {
    display: flex; align-items: center;
    padding: 4px 10px; background: #252526; border-bottom: 1px solid #333;
    font-size: 12px; color: #858585;
  }
  header b { color: #d4d4d4; }
  header .spacer { flex: 1; }
  header .tab {
    padding: 4px 12px; margin-right: 4px; cursor: pointer;
    border-radius: 2px; user-select: none;
  }
  header .tab.active { background: #0e639c; color: white; }
  header .tab:not(.active):hover { background: #333; color: #d4d4d4; }
  header .hint { color: #858585; margin-left: 12px; }
  header input[type=checkbox] { vertical-align: middle; margin: 0 4px 0 0; }

  /* Panel switching */
  .panel { display: none; flex: 1; flex-direction: column; overflow: hidden; }
  .panel.active { display: flex; }

  /* --- REPL --- */
  #log {
    flex: 1; overflow-y: auto; padding: 8px 10px;
    white-space: pre-wrap; font-size: 13px; line-height: 1.35;
  }
  #log > div { margin: 2px 0; }
  .in  { color: #569cd6; }
  .out { color: #d4d4d4; }
  .err { color: #f48771; }
  .res { color: #b5cea8; }
  form {
    display: flex; padding: 8px; border-top: 1px solid #333;
    background: #252526;
  }
  textarea {
    flex: 1; background: #1e1e1e; color: #d4d4d4;
    border: 1px solid #3c3c3c; border-radius: 2px;
    padding: 6px 8px; font-family: inherit; font-size: 13px;
    resize: vertical; min-height: 3em;
  }
  textarea:focus { outline: 1px solid #007acc; border-color: #007acc; }
  button {
    margin-left: 8px; background: #0e639c; color: white;
    border: none; padding: 0 18px; font-family: inherit; font-size: 13px;
    cursor: pointer; border-radius: 2px;
  }
  button:hover { background: #1177bb; }

  /* --- State tree --- */
  #state {
    flex: 1; overflow: auto; padding: 8px 10px;
    font-size: 13px; line-height: 1.4;
  }
  .node { padding-left: 16px; }
  .node.root { padding-left: 0; }
  .row { display: flex; align-items: baseline; }
  .toggle {
    display: inline-block; width: 14px; cursor: pointer;
    color: #858585; user-select: none; flex: 0 0 auto;
  }
  .toggle.leaf { color: transparent; cursor: default; }
  .key   { color: #9cdcfe; margin-right: 6px; }
  .kind  { color: #858585; margin-right: 6px; font-size: 11px; }
  .value { color: #ce9178; }
  .value.num  { color: #b5cea8; }
  .value.bool { color: #569cd6; }
  .value.null { color: #569cd6; }
  .value.repr { color: #dcdcaa; }
  .value.cycle, .value.trunc { color: #f48771; font-style: italic; }
  @keyframes flash-fade {
    0%   { background: #6b6b00; }
    100% { background: transparent; }
  }
  .flash { animation: flash-fade 1s ease-out; }
  .children.hidden { display: none; }
  .tag-header { color: #4ec9b0; font-weight: bold; margin-top: 6px; }
</style>
</head>
<body>
<header>
  <b>Py4GW Debug Hatch</b>
  <span class="hint">|</span>
  <span class="tab active" data-tab="state">State</span>
  <span class="tab" data-tab="repl">REPL</span>
  <div class="spacer"></div>
  <span class="hint" id="live-hint">
    <label><input type="checkbox" id="autorefresh" checked>Auto-refresh</label>
    <span id="last-fetch"></span>
  </span>
</header>

<!-- STATE PANEL -->
<div id="panel-state" class="panel active">
  <div id="state"></div>
</div>

<!-- REPL PANEL -->
<div id="panel-repl" class="panel">
  <div id="log"></div>
  <form id="f">
    <textarea id="code" rows="3" spellcheck="false" placeholder="Python. Ctrl+Enter to run."></textarea>
    <button type="submit">Run</button>
  </form>
</div>

<script>
// ============ Tab switching ============
const tabs = document.querySelectorAll('.tab');
const panels = {
  state: document.getElementById('panel-state'),
  repl: document.getElementById('panel-repl'),
};
tabs.forEach(t => t.addEventListener('click', () => {
  tabs.forEach(x => x.classList.remove('active'));
  Object.values(panels).forEach(p => p.classList.remove('active'));
  t.classList.add('active');
  panels[t.dataset.tab].classList.add('active');
  if (t.dataset.tab === 'repl') document.getElementById('code').focus();
}));

// ============ State tree ============
const stateEl = document.getElementById('state');
const autorefresh = document.getElementById('autorefresh');
const lastFetch = document.getElementById('last-fetch');

// Persist which paths are expanded across polls.
const expanded = new Set(['']);   // root always expanded
// Track last-seen serialized value per path so we can flash diffs.
const lastSeen = new Map();
// Flash timers per path so a burst of updates doesn't reset the flash.
const flashTimers = new Map();

function classifyValue(v) {
  if (v === null) return {kind: 'null', text: 'None'};
  if (typeof v === 'boolean') return {kind: 'bool', text: v ? 'True' : 'False'};
  if (typeof v === 'number') return {kind: 'num', text: String(v)};
  if (typeof v === 'string') return {kind: 'str', text: JSON.stringify(v)};
  return null;  // composite
}

function isReprObj(v)  { return v && typeof v === 'object' && !Array.isArray(v) && '__repr__' in v; }
function isCycleObj(v) { return v && typeof v === 'object' && !Array.isArray(v) && '__cycle__' in v; }
function isTruncObj(v) { return v && typeof v === 'object' && !Array.isArray(v) && '__truncated__' in v && Object.keys(v).length <= 2; }

function typeLabel(v) {
  if (Array.isArray(v))  return `list(${v.length})`;
  if (isReprObj(v))      return v.__type__ || '';
  if (typeof v === 'object' && v !== null) {
    const keys = Object.keys(v).filter(k => !k.startsWith('__'));
    return `dict(${keys.length})`;
  }
  return '';
}

function renderValue(v) {
  const prim = classifyValue(v);
  if (prim) return {leaf: true, node: makeSpan('value ' + prim.kind, prim.text)};
  if (isCycleObj(v)) return {leaf: true, node: makeSpan('value cycle', `<cycle: ${v.__type__}>`)};
  if (isTruncObj(v)) return {leaf: true, node: makeSpan('value trunc', `<${v.__truncated__}>`)};
  if (isReprObj(v))  return {leaf: true, node: makeSpan('value repr', v.__repr__)};
  return {leaf: false, node: null};
}

function makeSpan(cls, text) {
  const s = document.createElement('span');
  s.className = cls;
  s.textContent = text;
  return s;
}

function renderNode(key, value, path, root=false) {
  const node = document.createElement('div');
  node.className = 'node' + (root ? ' root' : '');

  const row = document.createElement('div');
  row.className = 'row';
  node.appendChild(row);

  const {leaf, node: valueNode} = renderValue(value);

  const toggle = document.createElement('span');
  toggle.className = 'toggle' + (leaf ? ' leaf' : '');
  toggle.textContent = leaf ? '·' : (expanded.has(path) ? '▾' : '▸');
  row.appendChild(toggle);

  if (key !== null) row.appendChild(makeSpan('key', String(key) + ':'));
  row.appendChild(makeSpan('kind', typeLabel(value)));

  if (leaf) {
    row.appendChild(valueNode);
  } else {
    // Placeholder summary for the row (also updated in-place on re-render).
    const summary = makeSpan('value', '');
    row.appendChild(summary);

    const children = document.createElement('div');
    children.className = 'children' + (expanded.has(path) ? '' : ' hidden');
    node.appendChild(children);

    const items = Array.isArray(value)
      ? value.map((v, i) => [i, v])
      : Object.entries(value).filter(([k]) => !k.startsWith('__'));

    for (const [k, v] of items) {
      children.appendChild(renderNode(k, v, path + '/' + k));
    }

    toggle.addEventListener('click', () => {
      if (expanded.has(path)) { expanded.delete(path); toggle.textContent = '▸'; children.classList.add('hidden'); }
      else                     { expanded.add(path);    toggle.textContent = '▾'; children.classList.remove('hidden'); }
    });
  }

  // Flash if the leaf value changed since last poll.
  if (leaf) {
    const serialized = JSON.stringify(value);
    if (lastSeen.has(path) && lastSeen.get(path) !== serialized) {
      row.classList.add('flash');
      clearTimeout(flashTimers.get(path));
      flashTimers.set(path, setTimeout(() => row.classList.remove('flash'), 800));
    }
    lastSeen.set(path, serialized);
  }

  return node;
}

function renderState(snapshots) {
  const scrollTop = stateEl.scrollTop;
  stateEl.innerHTML = '';
  const tags = Object.keys(snapshots).sort();
  if (tags.length === 0) {
    stateEl.innerHTML = '<div style="color:#858585;padding:20px;text-align:center">No snapshots captured yet.<br>Add <code>snap("tag")</code> somewhere in the code path.</div>';
    return;
  }
  for (const tag of tags) {
    const header = document.createElement('div');
    header.className = 'tag-header';
    header.textContent = tag;
    stateEl.appendChild(header);
    stateEl.appendChild(renderNode(null, snapshots[tag], tag, true));
  }
  stateEl.scrollTop = scrollTop;
}

async function pollState() {
  try {
    const res = await fetch('/state');
    const data = await res.json();
    renderState(data);
    lastFetch.textContent = ' · ' + new Date().toLocaleTimeString();
  } catch (e) {
    lastFetch.textContent = ' · fetch error';
  }
}

let pollTimer = null;
function schedulePoll() {
  clearTimeout(pollTimer);
  if (!autorefresh.checked) return;
  pollTimer = setTimeout(async () => { await pollState(); schedulePoll(); }, 500);
}
autorefresh.addEventListener('change', () => { if (autorefresh.checked) { pollState(); schedulePoll(); } });
pollState();
schedulePoll();

// ============ REPL ============
const log = document.getElementById('log');
const code = document.getElementById('code');
const form = document.getElementById('f');
const history = [];
let historyIdx = -1;

function append(kind, text) {
  if (!text) return;
  const div = document.createElement('div');
  div.className = kind;
  div.textContent = (text.endsWith('\n') ? text.slice(0, -1) : text);
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
}

async function submit() {
  const src = code.value;
  if (!src.trim()) return;
  history.push(src);
  historyIdx = history.length;
  const prompt = src.split('\n').map((l, i) => (i === 0 ? '>>> ' : '... ') + l).join('\n');
  append('in', prompt);
  code.value = '';
  try {
    const res = await fetch('/eval', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({code: src}),
    });
    const data = await res.json();
    if (data.stdout) append('out', data.stdout);
    if (data.stderr) append('err', data.stderr);
    if (data.result !== null && data.result !== undefined) append('res', data.result);
  } catch (e) {
    append('err', 'network error: ' + e.message);
  }
  code.focus();
}

form.addEventListener('submit', e => { e.preventDefault(); submit(); });
code.addEventListener('keydown', e => {
  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
    e.preventDefault(); submit();
  } else if (e.key === 'ArrowUp' && !code.value.includes('\n') && historyIdx > 0) {
    e.preventDefault();
    historyIdx--;
    code.value = history[historyIdx];
  } else if (e.key === 'ArrowDown' && historyIdx < history.length - 1) {
    e.preventDefault();
    historyIdx++;
    code.value = history[historyIdx];
  }
});
</script>
</body>
</html>
"""


# ---- HTTP handler -------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args, **kwargs):
        # Silence per-request stdout spam that would flood the game console.
        pass

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802 — required name
        if self.path in ("/", "/index.html"):
            body = _INDEX_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/snapshots":
            self._send_json(200, {k: repr(v) for k, v in _snapshots.items()})
        elif self.path == "/state":
            self._send_json(200, _snapshots_jsonable())
        else:
            self.send_error(404)

    def do_POST(self):  # noqa: N802
        if self.path != "/eval":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send_json(400, {"error": "invalid json"})
            return
        source = payload.get("code", "")
        self._send_json(200, _run_source(source))


# ---- Server bootstrap ---------------------------------------------------

_server: ThreadingHTTPServer | None = None
_server_thread: threading.Thread | None = None
_bind_error: str | None = None


def _start_server_once() -> None:
    global _server, _server_thread, _bind_error
    if _server is not None or _bind_error is not None:
        return
    try:
        _server = ThreadingHTTPServer((HOST, PORT), _Handler)
    except OSError as e:
        _bind_error = f"{type(e).__name__}: {e}"
        return
    _server_thread = threading.Thread(
        target=_server.serve_forever,
        name="debug-hatch-http",
        daemon=True,
    )
    _server_thread.start()


def status() -> str:
    """Human-readable status string for widget display."""
    if _server is not None:
        return f"Debug hatch running → http://{HOST}:{PORT}"
    if _bind_error is not None:
        return f"Debug hatch failed to bind: {_bind_error}"
    return "Debug hatch not started"


_start_server_once()
