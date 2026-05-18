import json
import os
import platform
import secrets
import signal
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
PID_PATH = DATA_DIR / "tastespace.pid"
TOKEN_PATH = DATA_DIR / "control_token.txt"
LOG_PATH = DATA_DIR / "tastespace_server.log"

APP_PORT = int(os.environ.get("TASTESPACE_APP_PORT", "8000"))
CONTROL_PORT = int(os.environ.get("TASTESPACE_CONTROL_PORT", "9000"))
APP_HOST = os.environ.get("TASTESPACE_APP_HOST", "0.0.0.0")
CONTROL_HOST = os.environ.get("TASTESPACE_CONTROL_HOST", "0.0.0.0")
ALLOW_SLEEP = os.environ.get("TASTESPACE_ALLOW_SLEEP", "0") == "1"


def python_executable() -> str:
    if platform.system() == "Windows":
        candidate = BASE_DIR / ".venv" / "Scripts" / "python.exe"
    else:
        candidate = BASE_DIR / ".venv" / "bin" / "python"
    return str(candidate if candidate.exists() else Path(sys.executable))


def control_token() -> str:
    env_token = os.environ.get("TASTESPACE_CONTROL_TOKEN")
    if env_token:
        return env_token

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if TOKEN_PATH.exists():
        return TOKEN_PATH.read_text(encoding="utf-8").strip()

    token = secrets.token_urlsafe(24)
    TOKEN_PATH.write_text(token + "\n", encoding="utf-8")
    return token


def read_pid() -> int | None:
    if not PID_PATH.exists():
        return None
    try:
        return int(PID_PATH.read_text(encoding="utf-8").strip())
    except ValueError:
        return None


def process_running(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def app_running() -> bool:
    pid = read_pid()
    if process_running(pid):
        return True
    if PID_PATH.exists():
        PID_PATH.unlink()
    return False


def server_urls(host_header: str | None) -> dict[str, str]:
    host = host_header.split(":")[0] if host_header else "localhost"
    return {
        "ui": f"http://{host}:{APP_PORT}/ui",
        "manifest": f"http://{host}:{APP_PORT}/manifest.json",
        "control": f"http://{host}:{CONTROL_PORT}/",
    }


def start_app() -> dict:
    if app_running():
        return {"changed": False, "message": "TasteSpace is already running.", **status_payload(None)}

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_PATH.open("a", encoding="utf-8")
    env = os.environ.copy()
    if env.get("TASTESPACE_OFFLINE") == "1":
        env["HF_HUB_OFFLINE"] = "1"
        env["TRANSFORMERS_OFFLINE"] = "1"

    command = [
        python_executable(),
        "-m",
        "uvicorn",
        "app:app",
        "--host",
        APP_HOST,
        "--port",
        str(APP_PORT),
    ]
    kwargs = {
        "cwd": str(BASE_DIR),
        "env": env,
        "stdout": log_file,
        "stderr": subprocess.STDOUT,
        "stdin": subprocess.DEVNULL,
    }
    if platform.system() == "Windows":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True

    process = subprocess.Popen(command, **kwargs)
    PID_PATH.write_text(str(process.pid), encoding="utf-8")
    time.sleep(0.5)
    return {"changed": True, "message": "TasteSpace started.", **status_payload(None)}


def stop_app() -> dict:
    pid = read_pid()
    if not process_running(pid):
        if PID_PATH.exists():
            PID_PATH.unlink()
        return {"changed": False, "message": "TasteSpace is not running.", **status_payload(None)}

    if platform.system() == "Windows":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        os.kill(pid, signal.SIGTERM)

    for _ in range(20):
        if not process_running(pid):
            break
        time.sleep(0.1)

    if PID_PATH.exists():
        PID_PATH.unlink()
    return {"changed": True, "message": "TasteSpace stopped.", **status_payload(None)}


def sleep_pc() -> dict:
    if not ALLOW_SLEEP:
        return {
            "changed": False,
            "message": "Sleep is disabled. Set TASTESPACE_ALLOW_SLEEP=1 to enable it.",
            **status_payload(None),
        }

    system = platform.system()
    if system == "Windows":
        command = ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"]
    elif system == "Darwin":
        command = ["pmset", "sleepnow"]
    else:
        command = ["systemctl", "suspend"]

    subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"changed": True, "message": "Sleep command sent.", **status_payload(None)}


def status_payload(host_header: str | None) -> dict:
    pid = read_pid()
    running = process_running(pid)
    return {
        "running": running,
        "pid": pid if running else None,
        "app_port": APP_PORT,
        "control_port": CONTROL_PORT,
        "sleep_enabled": ALLOW_SLEEP,
        "urls": server_urls(host_header),
    }


HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>TasteSpace Control</title>
  <style>
    :root { color-scheme: dark; --bg:#111417; --panel:#1c2227; --line:#344049; --text:#f2f0e8; --muted:#9ba5ad; --ok:#6cc27b; --warn:#d7a84d; --bad:#e05f5f; }
    * { box-sizing:border-box; }
    body { margin:0; min-height:100vh; background:linear-gradient(140deg,#111417,#182026 55%,#101315); color:var(--text); font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif; }
    main { width:min(760px,100%); margin:0 auto; padding:28px 16px; }
    header { display:flex; align-items:center; justify-content:space-between; gap:16px; margin-bottom:18px; }
    h1 { margin:0; font-size:28px; letter-spacing:0; }
    .status { border:1px solid var(--line); border-radius:999px; padding:7px 11px; color:var(--muted); }
    .status.on { color:var(--ok); border-color:var(--ok); }
    .panel { background:rgba(28,34,39,.92); border:1px solid var(--line); border-radius:8px; padding:16px; }
    .token { display:grid; grid-template-columns:1fr auto; gap:10px; margin-bottom:14px; }
    input { min-width:0; width:100%; border:1px solid var(--line); border-radius:8px; background:#0e1215; color:var(--text); padding:10px; font:inherit; }
    .grid { display:grid; grid-template-columns:repeat(2,1fr); gap:10px; }
    button, a { border:1px solid var(--line); border-radius:8px; background:#252d33; color:var(--text); min-height:44px; padding:10px 12px; font:inherit; text-decoration:none; text-align:center; cursor:pointer; }
    button:hover, a:hover { border-color:#d7a84d; }
    button:disabled { cursor:not-allowed; opacity:.55; }
    button:disabled:hover { border-color:var(--line); }
    button.stop { color:var(--warn); }
    button.sleep { color:var(--bad); }
    .links { display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-top:10px; }
    pre { white-space:pre-wrap; word-break:break-word; color:var(--muted); margin:14px 0 0; }
    @media (max-width:620px) { header,.grid,.links,.token { grid-template-columns:1fr; display:grid; } }
  </style>
</head>
<body>
  <main>
    <header>
      <h1>TasteSpace Control</h1>
      <div id="running" class="status">Checking</div>
    </header>
    <section class="panel">
      <div class="token">
        <input id="token" type="password" placeholder="Control token">
        <button id="save">Save</button>
      </div>
      <div class="grid">
        <button data-action="start">Start</button>
        <button class="stop" data-action="stop">Stop</button>
        <button data-action="restart">Restart</button>
        <button id="sleep" class="sleep" data-action="sleep">Sleep PC</button>
      </div>
      <div class="links">
        <a id="ui" href="#">TasteSpace UI</a>
        <a id="manifest" href="#">Manifest</a>
      </div>
      <pre id="output"></pre>
    </section>
  </main>
  <script>
    const tokenInput = document.querySelector('#token');
    const output = document.querySelector('#output');
    const running = document.querySelector('#running');
    tokenInput.value = localStorage.getItem('tastespaceControlToken') || '';
    document.querySelector('#save').onclick = () => {
      localStorage.setItem('tastespaceControlToken', tokenInput.value.trim());
      refresh();
    };
    function token() { return encodeURIComponent(tokenInput.value.trim()); }
    async function call(path, method='GET') {
      const res = await fetch(`${path}${path.includes('?') ? '&' : '?'}token=${token()}`, { method });
      const text = await res.text();
      let data;
      try { data = JSON.parse(text); } catch { throw new Error(text || res.statusText); }
      if (!res.ok) throw new Error(data.error || res.statusText);
      output.textContent = JSON.stringify(data, null, 2);
      update(data);
      return data;
    }
    function update(data) {
      running.textContent = data.running ? 'Running' : 'Stopped';
      running.classList.toggle('on', !!data.running);
      if (data.urls) {
        document.querySelector('#ui').href = data.urls.ui;
        document.querySelector('#manifest').href = data.urls.manifest;
      }
      const sleep = document.querySelector('#sleep');
      if (data.sleep_enabled === false) {
        sleep.disabled = true;
        sleep.textContent = 'Sleep Disabled';
        sleep.title = 'Set TASTESPACE_ALLOW_SLEEP=1 to enable this action.';
      } else {
        sleep.disabled = false;
        sleep.textContent = 'Sleep PC';
        sleep.title = '';
      }
    }
    async function refresh() {
      if (!tokenInput.value.trim()) return;
      try { await call('/api/status'); } catch (error) { output.textContent = error.message; }
    }
    document.querySelectorAll('[data-action]').forEach((button) => {
      button.onclick = async () => {
        try { await call(`/api/${button.dataset.action}`, 'POST'); } catch (error) { output.textContent = error.message; }
      };
    });
    setInterval(refresh, 5000);
    refresh();
  </script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return

    def send_html(self) -> None:
        body = HTML.encode("utf-8")
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return

    def authorized(self, query: dict[str, list[str]]) -> bool:
        values = query.get("token", [])
        return bool(values and secrets.compare_digest(values[0], control_token()))

    def handle_api(self, action: str) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if not self.authorized(query):
            self.send_json(401, {"error": "Invalid or missing control token."})
            return

        try:
            if action == "status":
                payload = status_payload(self.headers.get("Host"))
            elif action == "start":
                payload = start_app()
                payload["urls"] = server_urls(self.headers.get("Host"))
            elif action == "stop":
                payload = stop_app()
                payload["urls"] = server_urls(self.headers.get("Host"))
            elif action == "restart":
                stop_app()
                payload = start_app()
                payload["message"] = "TasteSpace restarted."
                payload["urls"] = server_urls(self.headers.get("Host"))
            elif action == "sleep":
                payload = sleep_pc()
                payload["urls"] = server_urls(self.headers.get("Host"))
            else:
                self.send_json(404, {"error": "Unknown action."})
                return
            self.send_json(200, payload)
        except Exception as error:
            self.send_json(500, {"error": str(error)})

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self.send_html()
            return
        if parsed.path == "/api/status":
            self.handle_api("status")
            return
        self.send_json(404, {"error": "Not found."})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self.handle_api(parsed.path.removeprefix("/api/"))
            return
        self.send_json(404, {"error": "Not found."})

    def log_message(self, format: str, *args) -> None:
        return


class QuietThreadingHTTPServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address) -> None:
        _, error, _ = sys.exc_info()
        if isinstance(error, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
            return
        super().handle_error(request, client_address)


def main() -> int:
    token = control_token()
    server = QuietThreadingHTTPServer((CONTROL_HOST, CONTROL_PORT), Handler)
    print(f"TasteSpace control: http://0.0.0.0:{CONTROL_PORT}")
    print(f"Control token: {token}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
