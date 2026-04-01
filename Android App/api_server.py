"""
HFS Password Finder - API Server
Serves the mobile web UI and exposes REST endpoints for reading/writing hfs.ini
Run this on the Windows machine: python api_server.py
Then access from phone at:  http://<your-ip>:5001
"""
from flask import Flask, jsonify, request, send_from_directory, abort
from flask_cors import CORS
import base64, gzip, glob, os, re, json, requests
from functools import wraps

app = Flask(__name__, static_folder="mobile_ui")
CORS(app)

# ── Config ────────────────────────────────────────────────────────────────────
HFS_URL      = "http://ww2.wg816.com:82"
HFS_USER     = "wg"
HFS_PASSWORD = "dingdong"
API_PASSWORD = "dingdong"          # password to access THIS API
INI_PATH_HFS = "/HFS/hfs.ini"      # path on the HFS server
VFS_DIR_HFS  = "/HFS/"             # folder on HFS server that contains .vfs files

SKIP_USERS = {"admin", "wg", "beats", "slop"}

# ── Auth decorator ────────────────────────────────────────────────────────────
def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Basic "):
            try:
                decoded = base64.b64decode(auth[6:]).decode()
                _, pw = decoded.split(":", 1)
                if pw == API_PASSWORD:
                    return f(*args, **kwargs)
            except Exception:
                pass
        return jsonify({"error": "Unauthorized"}), 401
    return decorated


# ── HFS helpers ───────────────────────────────────────────────────────────────
def hfs_get(path):
    """GET a file from the HFS server as bytes."""
    url  = HFS_URL + path
    resp = requests.get(url, auth=(HFS_USER, HFS_PASSWORD), timeout=15)
    resp.raise_for_status()
    return resp.content


def hfs_put(path, data_bytes):
    """Upload (overwrite) a file on the HFS server via PUT."""
    url  = HFS_URL + path
    resp = requests.put(url, data=data_bytes,
                        auth=(HFS_USER, HFS_PASSWORD), timeout=30)
    resp.raise_for_status()
    return resp


def hfs_list_vfs():
    """Return list of .vfs filenames from the HFS /HFS/ folder (newest first)."""
    html = hfs_get(VFS_DIR_HFS).decode("utf-8", errors="replace")
    names = re.findall(r'href="([^"]*\.vfs)"', html)
    # decode %20 etc
    names = [requests.utils.unquote(n.split("/")[-1]) for n in names]
    names = sorted(names, reverse=True)   # newest date last in name
    return names


# ── Parsers ───────────────────────────────────────────────────────────────────
def parse_accounts(ini_bytes):
    text  = ini_bytes.decode("utf-8", errors="replace")
    accounts = []
    for line in text.splitlines():
        if not line.startswith("accounts="):
            continue
        data  = line[len("accounts="):]
        parts = data.split("|")
        cur   = {}
        for p in parts:
            if not p:
                continue
            if p.startswith("login:"):
                if cur.get("b64"):
                    accounts.append(_build_acct(cur))
                cur = {"b64": p[6:]}
            elif p.startswith("enabled:"):  cur["enabled"] = p[8:]
            elif p.startswith("group:"):    cur["group"]   = p[6:]
            elif p.startswith("link:"):     cur["link"]    = p[5:].rstrip(";")
        if cur.get("b64"):
            accounts.append(_build_acct(cur))
        break
    return accounts


def _build_acct(raw):
    b64 = raw["b64"]
    try:
        dec = base64.b64decode(b64).decode("utf-8", errors="replace")
    except Exception:
        dec = b64
    user, pw = (dec.split(":", 1) if ":" in dec else (dec, ""))
    return {"username": user, "password": pw,
            "enabled": raw.get("enabled",""), "group": raw.get("group",""),
            "link": raw.get("link",""), "raw_b64": b64}


def parse_vfs(vfs_bytes):
    """Return dict: username_lower -> [folder_name, ...]"""
    try:
        dec = gzip.decompress(vfs_bytes[70:])
    except Exception:
        return {}
    strings = [s.decode("ascii") for s in re.findall(rb"[\x20-\x7E]{2,}", dec)]

    def is_path(s):  return len(s)>3 and s[1]==":" and s[2]==chr(92)
    def is_junk(s):  return len(s)<=3 and not re.match(r"^[a-z0-9]+$",s)
    def is_name(s):  return not is_path(s) and len(s)>=2 and bool(re.search(r"[A-Z]{2,}",s))

    umap = {}
    i = 0
    while i < len(strings)-2:
        s = strings[i]
        if is_path(s):
            j = i+1
            while j < len(strings) and is_junk(strings[j]): j+=1
            if j < len(strings):
                pu = strings[j]
                if re.match(r"^[a-z0-9;]+$", pu) and len(pu)<100:
                    k = j+1
                    while k < len(strings) and is_junk(strings[k]): k+=1
                    if k < len(strings) and is_name(strings[k]):
                        fn = strings[k]
                        for u in pu.split(";"):
                            u = u.strip().lower()
                            if u and u not in SKIP_USERS:
                                umap.setdefault(u,[])
                                if fn not in umap[u]: umap[u].append(fn)
        i+=1
    return umap


# ── Cache (in-memory, refreshed on demand) ────────────────────────────────────
_cache = {"accounts": None, "folder_map": None, "ini_bytes": None}

def refresh_cache():
    ini_bytes  = hfs_get(INI_PATH_HFS)
    vfs_names  = hfs_list_vfs()
    if not vfs_names:
        raise RuntimeError("No VFS files found on server")
    newest_vfs = vfs_names[0]
    vfs_bytes  = hfs_get(f"/HFS/{requests.utils.quote(newest_vfs)}")
    accounts   = parse_accounts(ini_bytes)
    folder_map = parse_vfs(vfs_bytes)
    _cache["accounts"]   = accounts
    _cache["folder_map"] = folder_map
    _cache["ini_bytes"]  = ini_bytes
    return accounts, folder_map


def get_cached():
    if _cache["accounts"] is None:
        refresh_cache()
    return _cache["accounts"], _cache["folder_map"]


def fmt_folders(username, folder_map):
    folders = folder_map.get(username.lower()) or folder_map.get(username) or []
    return " / ".join(folders)


# ── API Routes ────────────────────────────────────────────────────────────────
@app.route("/api/accounts")
@require_auth
def api_accounts():
    q = request.args.get("q","").strip().lower()
    try:
        accounts, folder_map = get_cached()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    results = []
    for a in accounts:
        folder = fmt_folders(a["username"], folder_map)
        if q and not (q in a["username"].lower() or q in folder.lower() or q in a["password"].lower()):
            continue
        results.append({
            "username": a["username"],
            "password": a["password"],
            "folder":   folder,
            "link":     a["link"],
            "enabled":  a["enabled"],
        })
    return jsonify({"accounts": results, "total": len(accounts)})


@app.route("/api/refresh", methods=["POST"])
@require_auth
def api_refresh():
    try:
        accounts, folder_map = refresh_cache()
        return jsonify({"ok": True, "count": len(accounts)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/change_password", methods=["POST"])
@require_auth
def api_change_password():
    body     = request.get_json(force=True)
    username = body.get("username","").strip()
    new_pw   = body.get("new_password","").strip()
    if not username or not new_pw:
        return jsonify({"error": "username and new_password required"}), 400

    try:
        accounts, _ = get_cached()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    target = next((a for a in accounts if a["username"]==username), None)
    if not target:
        return jsonify({"error": f"User '{username}' not found"}), 404

    old_b64   = target["raw_b64"]
    new_b64   = base64.b64encode(f"{username}:{new_pw}".encode()).decode()
    old_token = f"login:{old_b64}"
    new_token = f"login:{new_b64}"

    ini_text = _cache["ini_bytes"].decode("utf-8", errors="replace")
    if old_token not in ini_text:
        return jsonify({"error": "Token not found in ini — file may have changed"}), 409

    new_ini = ini_text.replace(old_token, new_token, 1)

    # ── Auto-backup before writing ────────────────────────────────────────────
    from datetime import datetime
    backup_name = f"/HFS/hfs_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.ini"
    try:
        hfs_put(backup_name, ini_text.encode("utf-8"))
    except Exception as e:
        return jsonify({"error": f"Backup failed, aborting for safety: {e}"}), 500

    try:
        hfs_put(INI_PATH_HFS, new_ini.encode("utf-8"))
    except Exception as e:
        return jsonify({"error": f"Upload failed (backup saved as {backup_name}): {e}"}), 500

    # Update cache
    target["password"] = new_pw
    target["raw_b64"]  = new_b64
    _cache["ini_bytes"] = new_ini.encode("utf-8")

    return jsonify({"ok": True, "username": username, "new_password": new_pw})


# ── Serve mobile UI ───────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory("mobile_ui", "index.html")


@app.route("/<path:filename>")
def static_files(filename):
    return send_from_directory("mobile_ui", filename)


# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import socket
    host = "0.0.0.0"
    port = 5001
    # Print local IPs for easy access
    hostname = socket.gethostname()
    try:
        local_ip = socket.gethostbyname(hostname)
    except Exception:
        local_ip = "127.0.0.1"
    print(f"\n  HFS Password Finder API Server")
    print(f"  -----------------------------")
    print(f"  Local:   http://localhost:{port}")
    print(f"  Network: http://{local_ip}:{port}")
    print(f"  Password: {API_PASSWORD}")
    print(f"  (Point your Android app here)\n")
    app.run(host=host, port=port, debug=False)
