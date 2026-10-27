# -*- coding: utf-8 -*-
"""PhoneDrop — локальная передача фото/скринов с телефона на ПК по Wi-Fi.

Запуск:  python server.py [--port 8800] [--dir ПАПКА]
Откройте http://IP_ПК:порт/ на телефоне (или отсканируйте QR).
Файлы сохраняются в ПАПКА/YYYY-MM-DD/.
Только стандартная библиотека Python 3.8+.
"""
import argparse
import datetime
import html
import http.server
import json
import os
import re
import socket
import threading
import urllib.parse
from http import HTTPStatus

import qr


def local_ips():
    ips = []
    try:
        # все IPv4 не loopback
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    # fallback через UDP-трюк
    if not ips:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            if ip and not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
        except Exception:
            pass
    # приоритет: 192.168.x.x, затем 10.x, затем остальные
    def _key(ip):
        if ip.startswith("192.168."):
            return (0, ip)
        if ip.startswith("10."):
            return (1, ip)
        return (2, ip)
    ips.sort(key=_key)
    if not ips:
        ips.append("127.0.0.1")
    return ips


def safe_name(name):
    name = os.path.basename(name.replace("\\", "/"))
    name = re.sub(r"[^\w.\- ]+", "_", name).strip(" .")
    if not name or name in (".", ".."):
        name = "file"
    # ограничение длины
    if len(name) > 120:
        root, ext = os.path.splitext(name)
        name = root[:120 - len(ext)] + ext
    return name


def unique_path(folder, name):
    base, ext = os.path.splitext(name)
    candidate = os.path.join(folder, name)
    i = 1
    while os.path.exists(candidate):
        candidate = os.path.join(folder, f"{base} ({i}){ext}")
        i += 1
    return candidate


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "PhoneDrop/1.0"

    def _send(self, code, body, ctype="text/html; charset=utf-8", headers=None):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        if headers:
            for k, v in headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        mon = self.server.monitor
        if path == "/":
            self._send(200, INDEX_HTML)
        elif path == "/qr.svg":
            url = mon.primary_url()
            svg = qr.matrix_to_svg(qr.make_qr(url, level="L"))
            self._send(200, svg, ctype="image/svg+xml")
        elif path == "/api/info":
            info = {
                "ips": mon.ips,
                "port": mon.port,
                "dir": mon.base_dir,
                "today": datetime.date.today().isoformat(),
            }
            self._send(200, json.dumps(info, ensure_ascii=False), ctype="application/json")
        elif path == "/api/files":
            files = mon.list_files()
            self._send(200, json.dumps(files, ensure_ascii=False), ctype="application/json")
        elif path.startswith("/files/"):
            rel = urllib.parse.unquote(path[len("/files/"):])
            full = mon.resolve_file(rel)
            if full and os.path.isfile(full):
                with open(full, "rb") as f:
                    data = f.read()
                ctype = "application/octet-stream"
                self._send(200, data, ctype=ctype)
            else:
                self._send(404, "not found", ctype="text/plain")
        else:
            self._send(404, "not found", ctype="text/plain")

    def do_POST(self):
        mon = self.server.monitor
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/upload":
            self._send(404, "not found", ctype="text/plain")
            return
        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ctype:
            self._send(400, "expected multipart/form-data", ctype="text/plain")
            return
        # parse boundary
        boundary = None
        for part in ctype.split(";"):
            part = part.strip()
            if part.lower().startswith("boundary="):
                boundary = part[9:].strip().strip('"')
        if not boundary:
            self._send(400, "no boundary", ctype="text/plain")
            return
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        saved = []
        # parse multipart manually
        delim = ("--" + boundary).encode()
        # split
        chunks = raw.split(delim)
        for chunk in chunks:
            chunk = chunk.strip(b"\r\n")
            if not chunk or chunk == b"--":
                continue
            # separate headers and body
            if b"\r\n\r\n" not in chunk:
                continue
            head, body = chunk.split(b"\r\n\r\n", 1)
            # remove trailing boundary marker
            if body.endswith(b"--"):
                body = body[:-2]
            body = body.rstrip(b"\r\n")
            # parse headers
            headers = {}
            for line in head.split(b"\r\n"):
                if b":" in line:
                    k, v = line.split(b":", 1)
                    headers[k.strip().lower().decode()] = v.strip().decode()
            cd = headers.get("content-disposition", "")
            m = re.search(r'filename="([^"]*)"', cd)
            if not m:
                continue
            filename = m.group(1)
            if not filename:
                continue
            # decode filename (utf-8)
            try:
                filename = filename.encode("latin-1").decode("utf-8")
            except Exception:
                pass
            name = safe_name(filename)
            folder = mon.ensure_today_folder()
            dest = unique_path(folder, name)
            with open(dest, "wb") as f:
                f.write(body)
            saved.append(os.path.basename(dest))
        self._send(200, json.dumps({"saved": saved}, ensure_ascii=False), ctype="application/json")

    def log_message(self, *args):
        pass


class Monitor:
    def __init__(self, base_dir, port):
        self.base_dir = base_dir
        self.port = port
        self.ips = local_ips()

    def primary_url(self):
        ip = self.ips[0] if self.ips else "127.0.0.1"
        return f"http://{ip}:{self.port}/"

    def ensure_today_folder(self):
        today = datetime.date.today().isoformat()
        folder = os.path.join(self.base_dir, today)
        os.makedirs(folder, exist_ok=True)
        return folder

    def list_files(self):
        out = []
        if not os.path.isdir(self.base_dir):
            return out
        for root, _, files in os.walk(self.base_dir):
            for fn in files:
                full = os.path.join(root, fn)
                rel = os.path.relpath(full, self.base_dir).replace("\\", "/")
                try:
                    st = os.stat(full)
                    out.append({"name": fn, "path": rel, "size": st.st_size,
                                "mtime": int(st.st_mtime)})
                except Exception:
                    pass
        out.sort(key=lambda x: x["mtime"], reverse=True)
        return out[:200]

    def resolve_file(self, rel):
        rel = rel.replace("\\", "/").lstrip("/")
        # prevent traversal
        full = os.path.normpath(os.path.join(self.base_dir, rel))
        if not full.startswith(os.path.normpath(self.base_dir) + os.sep):
            return None
        return full


INDEX_HTML = r"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>PhoneDrop — передача файлов по Wi-Fi</title>
<style>
:root {
  --bg:#0b0f14; --panel:#121821; --line:#1e2633; --text:#e6edf3; --muted:#8b98a5;
  --accent:#4f8cff; --good:#2ee6a8; --bad:#ff5d6c;
}
* { box-sizing: border-box; }
body { margin:0; font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
  background: var(--bg); color: var(--text); min-height:100vh; display:flex; flex-direction:column; }
header { padding:16px 20px; border-bottom:1px solid var(--line); display:flex; align-items:center; gap:12px; }
header h1 { font-size:18px; margin:0; }
header .sub { color:var(--muted); font-size:13px; }
main { flex:1; padding:20px; display:grid; grid-template-columns: 1fr 1fr; gap:20px; max-width:1100px; margin:0 auto; width:100%; }
@media (max-width:760px){ main{ grid-template-columns:1fr; } }
.card { background:var(--panel); border:1px solid var(--line); border-radius:14px; padding:18px; }
.card h2 { font-size:15px; margin:0 0 12px; color:var(--muted); text-transform:uppercase; letter-spacing:.5px; }
.qr { display:flex; justify-content:center; padding:10px; background:#fff; border-radius:10px; }
.qr svg { width:220px; height:220px; }
.link { word-break:break-all; font-family:monospace; font-size:13px; background:#0a0e13; padding:10px; border-radius:8px; border:1px solid var(--line); margin-top:10px; }
.ips { display:flex; flex-wrap:wrap; gap:6px; margin-top:8px; }
.ips a { color:var(--accent); font-size:13px; text-decoration:none; padding:4px 8px; border:1px solid var(--line); border-radius:6px; }
.ips a:hover { background:#1a2230; }
.drop { border:2px dashed var(--line); border-radius:12px; padding:30px 20px; text-align:center; color:var(--muted); cursor:pointer; transition:.15s; }
.drop:hover, .drop.over { border-color:var(--accent); color:var(--text); background:#0f1622; }
.drop input { display:none; }
button { background:var(--accent); color:#fff; border:none; border-radius:8px; padding:10px 16px; font-size:14px; cursor:pointer; font-weight:600; }
button:hover { filter:brightness(1.1); }
.row { display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-top:10px; }
.progress { height:6px; background:#0a0e13; border-radius:3px; overflow:hidden; margin-top:10px; }
.progress > div { height:100%; background:var(--accent); width:0%; transition:width .2s; }
.files { max-height:320px; overflow:auto; }
.files .item { display:flex; justify-content:space-between; padding:8px 0; border-bottom:1px solid var(--line); font-size:13px; gap:10px; }
.files .item .name { flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.files .item .size { color:var(--muted); white-space:nowrap; }
.toast { position:fixed; bottom:20px; left:50%; transform:translateX(-50%); background:#1a2230; border:1px solid var(--line); padding:10px 16px; border-radius:8px; font-size:14px; opacity:0; transition:.2s; pointer-events:none; }
.toast.show { opacity:1; }
.status { font-size:13px; color:var(--muted); margin-top:8px; }
</style>
</head>
<body>
<header>
  <h1>📱 PhoneDrop</h1>
  <span class="sub">Передача фото и скринов с телефона на ПК по Wi-Fi</span>
</header>
<main>
  <section class="card">
    <h2>Подключение</h2>
    <div class="qr" id="qrBox"><div style="color:#555;padding:40px">Загрузка QR…</div></div>
    <div class="link" id="linkBox">—</div>
    <div class="ips" id="ipsBox"></div>
    <p class="status">Откройте ссылку на телефоне (в той же Wi-Fi сети) или отсканируйте QR-код камерой.</p>
  </section>
  <section class="card">
    <h2>Загрузка файлов</h2>
    <div class="drop" id="drop">
      Перетащите файлы сюда или нажмите, чтобы выбрать
      <input type="file" id="fileInput" multiple accept="image/*,video/*">
    </div>
    <div class="row">
      <button id="pickBtn">Выбрать файлы</button>
      <button id="camBtn">Сделать фото</button>
      <span class="status" id="statusText">Готово</span>
    </div>
    <input type="file" id="camInput" accept="image/*" capture="environment" style="display:none">
    <div class="progress"><div id="prog"></div></div>
    <h2 style="margin-top:16px">Загруженные файлы</h2>
    <div class="files" id="filesList"></div>
  </section>
</main>
<div class="toast" id="toast"></div>
<script>
const $ = (s) => document.querySelector(s);
let uploading = false;
async function loadInfo() {
  const r = await fetch('/api/info'); const j = await r.json();
  const url = 'http://' + j.ips[0] + ':' + j.port + '/';
  $('#linkBox').textContent = url;
  $('#qrBox').innerHTML = '<img src="/qr.svg?t=' + Date.now() + '" alt="QR">';
  const ips = $('#ipsBox');
  ips.innerHTML = '';
  j.ips.forEach(ip => {
    const a = document.createElement('a');
    a.href = 'http://' + ip + ':' + j.port + '/';
    a.textContent = ip;
    a.target = '_blank';
    ips.appendChild(a);
  });
}
function toast(msg) {
  const t = $('#toast'); t.textContent = msg; t.classList.add('show');
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove('show'), 2600);
}
async function uploadFiles(files) {
  if (!files || !files.length) return;
  uploading = true;
  const fd = new FormData();
  for (const f of files) fd.append('files', f, f.name);
  $('#statusText').textContent = 'Загрузка ' + files.length + '…';
  const prog = $('#prog'); prog.style.width = '0%';
  const xhr = new XMLHttpRequest();
  xhr.open('POST', '/upload');
  xhr.upload.onprogress = (e) => {
    if (e.lengthComputable) prog.style.width = (e.loaded / e.total * 100) + '%';
  };
  xhr.onload = () => {
    uploading = false;
    prog.style.width = '100%';
    try {
      const j = JSON.parse(xhr.responseText);
      toast('Сохранено: ' + (j.saved || []).join(', '));
      $('#statusText').textContent = 'Готово';
      loadFiles();
    } catch (e) {
      toast('Ошибка загрузки');
      $('#statusText').textContent = 'Ошибка';
    }
    setTimeout(() => { prog.style.width = '0%'; }, 800);
  };
  xhr.onerror = () => { uploading = false; toast('Сеть недоступна'); };
  xhr.send(fd);
}
async function loadFiles() {
  const r = await fetch('/api/files'); const j = await r.json();
  const box = $('#filesList');
  box.innerHTML = '';
  if (!j.length) { box.innerHTML = '<div class="status">Пока пусто</div>'; return; }
  j.forEach(f => {
    const div = document.createElement('div');
    div.className = 'item';
    const name = document.createElement('span'); name.className = 'name'; name.textContent = f.name;
    const size = document.createElement('span'); size.className = 'size'; size.textContent = (f.size/1024).toFixed(1) + ' КБ';
    div.appendChild(name); div.appendChild(size);
    box.appendChild(div);
  });
}
const drop = $('#drop');
drop.addEventListener('click', () => $('#fileInput').click());
drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('over'); });
drop.addEventListener('dragleave', () => drop.classList.remove('over'));
drop.addEventListener('drop', (e) => { e.preventDefault(); drop.classList.remove('over'); uploadFiles(e.dataTransfer.files); });
$('#fileInput').addEventListener('change', (e) => { uploadFiles(e.target.files); e.target.value = ''; });
$('#pickBtn').addEventListener('click', () => $('#fileInput').click());
$('#camBtn').addEventListener('click', () => $('#camInput').click());
$('#camInput').addEventListener('change', (e) => { uploadFiles(e.target.files); e.target.value = ''; });

loadInfo();
loadFiles();
setInterval(loadFiles, 8000);
</script>
</body>
</html>
"""

def main():
    ap = argparse.ArgumentParser(description="PhoneDrop — передача файлов по Wi-Fi")
    ap.add_argument("--port", type=int, default=8800)
    ap.add_argument("--dir", type=str, default=None, help="папка для сохранения (по умолчанию ~/Pictures/PhoneDrop)")
    args = ap.parse_args()

    if args.dir:
        base_dir = args.dir
    else:
        base_dir = os.path.join(os.path.expanduser("~"), "Pictures", "PhoneDrop")
    os.makedirs(base_dir, exist_ok=True)

    mon = Monitor(base_dir, args.port)

    httpd = http.server.ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    httpd.monitor = mon
    httpd.daemon_threads = True

    url = mon.primary_url()
    print(f"PhoneDrop запущен: {url}")
    print(f"Папка сохранения: {base_dir}")
    print(f"Локальные IP: {', '.join(mon.ips)}")
    print("Остановка: Ctrl+C")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")
        httpd.shutdown()


if __name__ == "__main__":
    main()
