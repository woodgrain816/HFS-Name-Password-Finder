import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import base64
import gzip
import glob
import json
import os
import re


# ---------------------------------------------------------------------------
# Config persistence
# ---------------------------------------------------------------------------
CONFIG_DIR  = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "HFSPasswordFinder")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

def load_config():
    try:
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def save_config(data):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# VFS parser  (username -> [list of share folder display names])
# ---------------------------------------------------------------------------
def parse_vfs_folder_map(vfs_path):
    """Return dict: username (lower) -> list of share-folder display names."""
    try:
        with open(vfs_path, "rb") as f:
            data = f.read()
        decompressed = gzip.decompress(data[70:])
    except Exception:
        return {}

    strings = re.findall(rb"[\x20-\x7E]{2,}", decompressed)
    decoded  = [s.decode("ascii") for s in strings]

    def is_drive_path(s):
        return len(s) > 3 and s[1] == ":" and s[2] == chr(92)

    def is_junk(s):
        if len(s) <= 3:
            return not re.match(r"^[a-z0-9]+$", s)
        return False

    def is_folder_name(s):
        return (not is_drive_path(s)
                and len(s) >= 2
                and bool(re.search(r"[A-Z]{2,}", s)))

    SKIP_USERS = {"admin", "wg", "beats", "slop"}
    user_to_folders = {}   # username -> [folder1, folder2, …]

    i = 0
    while i < len(decoded) - 2:
        s = decoded[i]
        if is_drive_path(s):
            j = i + 1
            while j < len(decoded) and is_junk(decoded[j]):
                j += 1
            if j < len(decoded):
                potential_users = decoded[j]
                if re.match(r"^[a-z0-9;]+$", potential_users) and len(potential_users) < 100:
                    k = j + 1
                    while k < len(decoded) and is_junk(decoded[k]):
                        k += 1
                    if k < len(decoded) and is_folder_name(decoded[k]):
                        folder_name = decoded[k]
                        for user in potential_users.split(";"):
                            user = user.strip().lower()
                            if user and user not in SKIP_USERS:
                                folders = user_to_folders.setdefault(user, [])
                                if folder_name not in folders:
                                    folders.append(folder_name)
        i += 1

    return user_to_folders


def find_newest_vfs(directory):
    """Return path of the most-recently-modified .vfs file in directory."""
    vfs_files = glob.glob(os.path.join(directory, "*.vfs"))
    return max(vfs_files, key=os.path.getmtime) if vfs_files else None


# ---------------------------------------------------------------------------
# Main app
# ---------------------------------------------------------------------------
class HFSPasswordFinder:

    SORT_ARROWS = {"asc": "  ▲", "desc": "  ▼", None: ""}

    def __init__(self, root):
        self.root = root
        self.root.title("HFS Name & Password Finder")
        self.root.geometry("960x650")
        self.root.minsize(800, 500)
        self.root.configure(bg="#1e1e2e")

        self.ini_path        = ""
        self.accounts        = []
        self.folder_map      = {}   # username -> [folder, …]
        self.selected_account = None

        # Sort state: col name -> "asc" | "desc" | None
        self.sort_col = None
        self.sort_dir = "asc"

        self.setup_styles()
        self.build_ui()

        # Restore last ini path and auto-load
        cfg = load_config()
        saved_path = cfg.get("ini_path", "")
        if saved_path and os.path.isfile(saved_path):
            self.path_var.set(saved_path)
            self.load_file()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ------------------------------------------------------------------
    def setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")

        self.bg           = "#1e1e2e"
        self.surface      = "#2a2a3d"
        self.accent       = "#7c3aed"
        self.accent_hover = "#6d28d9"
        self.text_primary = "#e2e8f0"
        self.text_secondary = "#94a3b8"
        self.success      = "#22c55e"
        self.border       = "#3f3f5a"

        style.configure("TFrame",    background=self.bg)
        style.configure("TLabel",    background=self.bg, foreground=self.text_primary,   font=("Segoe UI", 10))
        style.configure("Title.TLabel",    background=self.bg, foreground=self.text_primary,   font=("Segoe UI", 16, "bold"))
        style.configure("Subtitle.TLabel", background=self.bg, foreground=self.text_secondary, font=("Segoe UI", 9))
        style.configure("Count.TLabel",    background=self.bg, foreground=self.accent,         font=("Segoe UI", 10, "bold"))

        style.configure("Treeview",
                         background=self.surface, foreground=self.text_primary,
                         fieldbackground=self.surface, borderwidth=0,
                         font=("Segoe UI", 10), rowheight=28)
        style.configure("Treeview.Heading",
                         background="#353550", foreground=self.text_primary,
                         font=("Segoe UI", 10, "bold"), borderwidth=0, padding=6)
        style.map("Treeview",
                  background=[("selected", self.accent)],
                  foreground=[("selected", "white")])

    # ------------------------------------------------------------------
    def build_ui(self):
        main = ttk.Frame(self.root, padding=20)
        main.pack(fill=tk.BOTH, expand=True)

        # ── Header ──────────────────────────────────────────────────────
        header = ttk.Frame(main)
        header.pack(fill=tk.X, pady=(0, 15))
        ttk.Label(header, text="HFS Name & Password Finder", style="Title.TLabel").pack(side=tk.LEFT)
        self.status_label = ttk.Label(header, text="No file loaded", style="Subtitle.TLabel")
        self.status_label.pack(side=tk.RIGHT)

        # ── File row ────────────────────────────────────────────────────
        file_frame = ttk.Frame(main)
        file_frame.pack(fill=tk.X, pady=(0, 12))

        self.path_var = tk.StringVar()
        tk.Entry(file_frame, textvariable=self.path_var, font=("Segoe UI", 10),
                 bg=self.surface, fg=self.text_primary, insertbackground=self.text_primary,
                 relief="flat", bd=0, highlightthickness=1,
                 highlightbackground=self.border, highlightcolor=self.accent
                 ).pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=6, padx=(0, 8))

        for label, color, hover, cmd in [
            ("Browse…", self.accent,  self.accent_hover, self.browse_file),
            ("Load",    "#059669",    "#047857",          self.load_file),
        ]:
            tk.Button(file_frame, text=label, font=("Segoe UI", 10, "bold"),
                      bg=color, fg="white", activebackground=hover, activeforeground="white",
                      relief="flat", bd=0, padx=16, pady=6, cursor="hand2", command=cmd
                      ).pack(side=tk.LEFT, padx=(0, 8))

        # ── Search row ──────────────────────────────────────────────────
        search_frame = ttk.Frame(main)
        search_frame.pack(fill=tk.X, pady=(0, 12))

        ttk.Label(search_frame, text="Search:", font=("Segoe UI", 10, "bold")).pack(side=tk.LEFT, padx=(0, 8))

        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self.filter_accounts())
        tk.Entry(search_frame, textvariable=self.search_var, font=("Segoe UI", 11),
                 bg=self.surface, fg=self.text_primary, insertbackground=self.text_primary,
                 relief="flat", bd=0, highlightthickness=1,
                 highlightbackground=self.border, highlightcolor=self.accent
                 ).pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=6, padx=(0, 8))

        self.count_label = ttk.Label(search_frame, text="", style="Count.TLabel")
        self.count_label.pack(side=tk.RIGHT)

        # ── Table ───────────────────────────────────────────────────────
        tree_frame = ttk.Frame(main)
        tree_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 12))

        columns = ("username", "password", "share_folder", "group", "enabled")
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings", selectmode="browse")

        # Column definitions  (id, label, width, sortable)
        col_defs = [
            ("username",    "Username",     150, True),
            ("password",    "Password",     150, False),
            ("share_folder","Share Folder", 280, True),
            ("group",       "Group",        100, False),
            ("enabled",     "Enabled",       70, False),
        ]
        for cid, label, width, sortable in col_defs:
            anchor = tk.CENTER if cid == "enabled" else tk.W
            if sortable:
                self.tree.heading(cid, text=label, anchor=tk.W,
                                  command=lambda c=cid: self.toggle_sort(c))
            else:
                self.tree.heading(cid, text=label, anchor=tk.W)
            self.tree.column(cid, width=width, minwidth=60, anchor=anchor)

        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.tree.bind("<<TreeviewSelect>>", self.on_select)

        # ── Detail panel ────────────────────────────────────────────────
        detail_frame = tk.Frame(main, bg=self.surface, bd=0,
                                highlightthickness=1, highlightbackground=self.border)
        detail_frame.pack(fill=tk.X)
        detail_inner = tk.Frame(detail_frame, bg=self.surface, padx=16, pady=12)
        detail_inner.pack(fill=tk.X)

        info_row = tk.Frame(detail_inner, bg=self.surface)
        info_row.pack(fill=tk.X, pady=(0, 8))
        tk.Label(info_row, text="Selected:", font=("Segoe UI", 10, "bold"),
                 bg=self.surface, fg=self.text_secondary).pack(side=tk.LEFT)
        self.selected_label = tk.Label(info_row, text="None",
                                        font=("Segoe UI", 11, "bold"),
                                        bg=self.surface, fg=self.text_primary)
        self.selected_label.pack(side=tk.LEFT, padx=(8, 0))

        btn_row = tk.Frame(detail_inner, bg=self.surface)
        btn_row.pack(fill=tk.X)

        self.copy_btn = tk.Button(btn_row, text="Copy Username & Password",
                                   font=("Segoe UI", 10, "bold"),
                                   bg=self.accent, fg="white",
                                   activebackground=self.accent_hover, activeforeground="white",
                                   relief="flat", bd=0, padx=16, pady=8,
                                   cursor="hand2", command=self.copy_credentials, state=tk.DISABLED)
        self.copy_btn.pack(side=tk.LEFT, padx=(0, 8))

        self.copy_status = tk.Label(btn_row, text="", font=("Segoe UI", 9),
                                     bg=self.surface, fg=self.success)
        self.copy_status.pack(side=tk.LEFT, padx=(0, 16))

        tk.Frame(btn_row, bg=self.border, width=1).pack(side=tk.LEFT, fill=tk.Y, padx=12, pady=2)

        tk.Label(btn_row, text="New Password:", font=("Segoe UI", 10),
                 bg=self.surface, fg=self.text_secondary).pack(side=tk.LEFT, padx=(0, 6))

        self.new_pw_var = tk.StringVar()
        self.pw_entry = tk.Entry(btn_row, textvariable=self.new_pw_var,
                                  font=("Segoe UI", 10),
                                  bg="#1e1e2e", fg=self.text_primary,
                                  insertbackground=self.text_primary,
                                  relief="flat", bd=0, highlightthickness=1,
                                  highlightbackground=self.border, highlightcolor=self.accent,
                                  width=20)
        self.pw_entry.pack(side=tk.LEFT, ipady=4, padx=(0, 8))
        self.pw_entry.bind("<Return>", lambda e: self.change_password())

        self.change_btn = tk.Button(btn_row, text="Change Password",
                                     font=("Segoe UI", 10, "bold"),
                                     bg="#dc2626", fg="white",
                                     activebackground="#b91c1c", activeforeground="white",
                                     relief="flat", bd=0, padx=16, pady=8,
                                     cursor="hand2", command=self.change_password, state=tk.DISABLED)
        self.change_btn.pack(side=tk.LEFT)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def on_close(self):
        save_config({"ini_path": self.ini_path or self.path_var.get().strip()})
        self.root.destroy()

    # ------------------------------------------------------------------
    # File loading
    # ------------------------------------------------------------------
    def browse_file(self):
        path = filedialog.askopenfilename(
            title="Select HFS ini file",
            filetypes=[("INI files", "*.ini"), ("All files", "*.*")])
        if path:
            self.path_var.set(path)
            self.load_file()

    def load_file(self):
        path = self.path_var.get().strip()
        if not path:
            messagebox.showwarning("No File", "Please select or enter a path to hfs.ini")
            return
        if not os.path.isfile(path):
            messagebox.showerror("File Not Found", f"Cannot find:\n{path}")
            return

        self.ini_path = path
        self.accounts.clear()
        self.folder_map.clear()

        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if line.strip().startswith("accounts="):
                        self.parse_accounts_line(line.strip())
                        break
        except Exception as e:
            messagebox.showerror("Error", f"Failed to read ini file:\n{e}")
            return

        # Auto-pick newest VFS from same directory
        ini_dir  = os.path.dirname(path)
        vfs_path = find_newest_vfs(ini_dir)
        vfs_status = ""
        if vfs_path:
            self.folder_map = parse_vfs_folder_map(vfs_path)
            vfs_status = f"  |  {len(self.folder_map)} folders from {os.path.basename(vfs_path)}"

        self.status_label.config(text=f"{len(self.accounts)} accounts loaded{vfs_status}")
        self.filter_accounts()

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------
    def parse_accounts_line(self, line):
        data  = line[len("accounts="):]
        parts = data.split("|")
        current = {}
        for part in parts:
            if not part:
                continue
            if part.startswith("login:"):
                if current.get("login_b64"):
                    self.finalize_account(current)
                current = {"login_b64": part[len("login:"):]}
            elif part.startswith("enabled:"):
                current["enabled"] = part[len("enabled:"):]
            elif part.startswith("group:"):
                current["group"]   = part[len("group:"):]
            elif part.startswith("no-limits:"):
                current["no_limits"] = part[len("no-limits:"):]
            elif part.startswith("link:"):
                current["link"]    = part[len("link:"):].rstrip(";")
        if current.get("login_b64"):
            self.finalize_account(current)

    def finalize_account(self, raw):
        b64 = raw.get("login_b64", "")
        try:
            decoded = base64.b64decode(b64).decode("utf-8", errors="replace")
        except Exception:
            decoded = b64
        if ":" in decoded:
            username, password = decoded.split(":", 1)
        else:
            username, password = decoded, ""
        self.accounts.append({
            "username":  username,
            "password":  password,
            "enabled":   raw.get("enabled", ""),
            "group":     raw.get("group", ""),
            "no_limits": raw.get("no_limits", ""),
            "link":      raw.get("link", ""),
            "raw_b64":   b64,
        })

    # ------------------------------------------------------------------
    # Folder lookup  (returns "FOLDER A / FOLDER B" for multiple)
    # ------------------------------------------------------------------
    def get_share_folder(self, username):
        folders = (self.folder_map.get(username.lower())
                   or self.folder_map.get(username)
                   or [])
        return " / ".join(folders)

    # ------------------------------------------------------------------
    # Sorting
    # ------------------------------------------------------------------
    def toggle_sort(self, col):
        """Cycle: no-sort → asc → desc → asc … for the clicked column."""
        if self.sort_col == col:
            self.sort_dir = "desc" if self.sort_dir == "asc" else "asc"
        else:
            self.sort_col = col
            self.sort_dir = "asc"
        self._update_heading_arrows()
        self.filter_accounts()

    def _update_heading_arrows(self):
        labels = {
            "username":    "Username",
            "share_folder":"Share Folder",
        }
        for cid, base in labels.items():
            arrow = ""
            if self.sort_col == cid:
                arrow = "  ▲" if self.sort_dir == "asc" else "  ▼"
            self.tree.heading(cid, text=base + arrow)

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------
    def filter_accounts(self):
        query = self.search_var.get().strip().lower()
        self.tree.delete(*self.tree.get_children())

        matches = []
        for acct in self.accounts:
            folder = self.get_share_folder(acct["username"])
            if query:
                if not (query in acct["username"].lower()
                        or query in acct["password"].lower()
                        or query in folder.lower()
                        or query in acct["link"].lower()):
                    continue
            matches.append((acct, folder))

        # Sort if a column is active
        if self.sort_col == "username":
            matches.sort(key=lambda x: x[0]["username"].lower(),
                         reverse=(self.sort_dir == "desc"))
        elif self.sort_col == "share_folder":
            matches.sort(key=lambda x: x[1].lower(),
                         reverse=(self.sort_dir == "desc"))

        for acct, folder in matches:
            masked_pw = "\u2022" * len(acct["password"])
            self.tree.insert("", tk.END, values=(
                acct["username"], masked_pw, folder,
                acct["link"], acct["enabled"],
            ))

        self.count_label.config(text=f"{len(matches)} of {len(self.accounts)} accounts")

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------
    def _find_account(self, username):
        for acct in self.accounts:
            if acct["username"] == username:
                return acct
        return None

    def on_select(self, event):
        sel = self.tree.selection()
        if not sel:
            self.selected_account = None
            self.selected_label.config(text="None")
            self.copy_btn.config(state=tk.DISABLED)
            self.change_btn.config(state=tk.DISABLED)
            return

        values = self.tree.item(sel[0], "values")
        username, folder = values[0], values[2]
        acct = self._find_account(username)
        self.selected_account = acct

        if acct:
            display = f"{username}  /  {acct['password']}"
            if folder:
                display += f"  /  {folder}"
            self.selected_label.config(text=display)

        self.copy_btn.config(state=tk.NORMAL)
        self.change_btn.config(state=tk.NORMAL)
        self.copy_status.config(text="")

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def copy_credentials(self):
        if not self.selected_account:
            return
        text = f"{self.selected_account['username']} {self.selected_account['password']}"
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.copy_status.config(text="Copied!")
        self.root.after(2000, lambda: self.copy_status.config(text=""))

    def change_password(self):
        if not self.selected_account:
            return
        new_pw = self.new_pw_var.get().strip()
        if not new_pw:
            messagebox.showwarning("Empty Password", "Please enter a new password.")
            return

        target       = self.selected_account
        old_username = target["username"]
        old_password = target["password"]

        if not messagebox.askyesno(
                "Confirm Password Change",
                f"Change password for '{old_username}'?\n\n"
                f"Old: {old_password}\nNew: {new_pw}\n\n"
                f"This will modify:\n{self.ini_path}"):
            return

        old_b64   = target["raw_b64"]
        new_b64   = base64.b64encode(f"{old_username}:{new_pw}".encode()).decode()
        old_token = f"login:{old_b64}"
        new_token = f"login:{new_b64}"

        try:
            with open(self.ini_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            if old_token not in content:
                messagebox.showerror("Error",
                    "Could not find the account entry in the ini file.\n"
                    "The file may have been modified externally.")
                return
            content = content.replace(old_token, new_token, 1)
            with open(self.ini_path, "w", encoding="utf-8") as f:
                f.write(content)

            target["password"] = new_pw
            target["raw_b64"]  = new_b64

            self.filter_accounts()
            self.new_pw_var.set("")
            folder  = self.get_share_folder(old_username)
            display = f"{old_username}  /  {new_pw}"
            if folder:
                display += f"  /  {folder}"
            self.selected_label.config(text=display)
            self.copy_status.config(text="Password changed!")
            self.root.after(3000, lambda: self.copy_status.config(text=""))

        except Exception as e:
            messagebox.showerror("Error", f"Failed to write file:\n{e}")


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    root = tk.Tk()
    app  = HFSPasswordFinder(root)
    root.mainloop()
