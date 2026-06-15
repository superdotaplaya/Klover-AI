import base64
import glob
import hashlib
import json
import os
import random
import re
import sys
import time
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime
from threading import Thread, Event, Timer
from typing import List, Tuple, Dict, Any, Optional
from playwright.sync_api import sync_playwright
import pynvml
import requests
import tkinter as tk
from tkinter import ttk, messagebox
from queue import Queue
import uuid
from safetensors.torch import safe_open
import subprocess


playwright_requests = Queue()
playwright_results = {}

print("THERE SHOULD ONLY BE ONE MESSAGE NOW IF IT WORKED!")



def playwright_resolver_loop():
    """
    Runs in the main thread (or a dedicated daemon thread started from main),
    owns the Playwright browser, and resolves CivitAI pages to final file URLs.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        while True:
            req_id, page_url = playwright_requests.get()
            final_url: Optional[str] = None
            try:
                print(f"[PLAYWRIGHT] Resolving: {page_url}")
                page.goto(page_url)
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(2000)

                url = page.evaluate("window.location.href")
                for _ in range(20):
                    if "backblaze" in url or "b2" in url:
                        break
                    page.wait_for_timeout(500)
                    url = page.evaluate("window.location.href")

                final_url = url
                print(f"[PLAYWRIGHT] Resolved to: {final_url}")
            except Exception as e:
                print(f"[PLAYWRIGHT ERROR] {e}")
                final_url = None

            playwright_results[req_id] = final_url
            playwright_requests.task_done()



# =========================
# DATA CLASSES / CONFIG
# =========================

@dataclass
class WorkerConfigData:
    idle_time_setting: int = 30
    polling_interval: int = 15
    gpu_id_setting: int = 1

    checkpoint_db: str = "checkpoints.json"
    lora_db: str = "loras.json"

    forge_model_directory: str = r"F:\stable-diffusion-webui-reForge\models\Stable-diffusion"
    lora_model_directory: str = r"F:\stable-diffusion-webui-reForge\models\Lora"

    civit_api_token: str = ""
    worker_id: str = ""
    accepted_job_types: List[str] = None
    max_batch_size: int = 5

    server_url: str = "https://c3bc6e2471d1.ngrok-free.app"
    progress_url: str = "https://superdotaplaya.pythonanywhere.com/api/update_progress"

    auto_dl_lora: bool = True
    auto_dl_checkpoints: bool = True

    output_directory: str = r"F:\stable-diffusion-webui-reForge\outputs\txt2img-images"
    worker_type: str = "forge"
    worker_name: str = "TBD"
    comfy_output_directory: str = ""
    comfy_input_directory: str = ""
    aria2_path: str = ""
    anima_text_encoder_path: str = ""
    anima_vae_path: str = ""

    def __post_init__(self):
        if self.accepted_job_types is None:
            self.accepted_job_types = ["generate", "facefix", "img2img", "upscale", "img2vid"]


class WorkerConfig:
    CONFIG_FILE = "worker_config.json"
    AUTH_FILE = "worker_auth.json"

    def __init__(self):
        self.data = WorkerConfigData()
        self.load_from_disk()

    # ---------- JSON CONFIG ----------
    def load_from_disk(self):
        if not os.path.exists(self.CONFIG_FILE):
            return
        try:
            with open(self.CONFIG_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            for k, v in raw.items():
                if hasattr(self.data, k):
                    setattr(self.data, k, v)
        except Exception as e:
            print(f"[CONFIG] Failed to load config: {e}")

    def save_to_disk(self):
        try:
            with open(self.CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(asdict(self.data), f, indent=4)
            print("[CONFIG] Settings saved successfully.")
        except Exception as e:
            print(f"[CONFIG] Failed to save settings: {e}")
            raise

    # ---------- AUTH ----------
    def load_worker_id(self) -> Optional[str]:
        if not os.path.exists(self.AUTH_FILE):
            return None
        try:
            with open(self.AUTH_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("worker_id")
        except Exception as e:
            print(f"[AUTH] Failed to load worker_auth.json: {e}")
            return None

    def save_worker_id(self, worker_id: str):
        try:
            with open(self.AUTH_FILE, "w", encoding="utf-8") as f:
                json.dump({"worker_id": worker_id}, f, indent=4)
            print(f"[AUTH] Worker ID saved: {worker_id}")
        except Exception as e:
            print(f"[AUTH] Failed to save worker_auth.json: {e}")

    # ---------- UI ----------
    def show_tk_ui_and_run(self, app_factory):
        import tkinter.filedialog as fd

        root = tk.Tk()
        root.title("Forge SD Worker")

        d = self.data

        auto_dl_lora_var = tk.BooleanVar(value=d.auto_dl_lora)
        auto_dl_checkpoints_var = tk.BooleanVar(value=d.auto_dl_checkpoints)

        frm = ttk.Frame(root, padding=10)
        frm.grid()

        # Helper: file picker button
        def pick_file(entry_widget, filetypes):
            path = fd.askopenfilename(filetypes=filetypes)
            if path:
                entry_widget.delete(0, tk.END)
                entry_widget.insert(0, path)

        def pick_folder(entry_widget):
            path = fd.askdirectory()
            if path:
                entry_widget.delete(0, tk.END)
                entry_widget.insert(0, path)

        row = 0

        # Worker Name
        ttk.Label(frm, text="Worker Name:").grid(column=0, row=row, sticky="w")
        worker_name_entry = ttk.Entry(frm, width=80)
        worker_name_entry.insert(0, d.worker_name)
        worker_name_entry.grid(column=1, row=row, sticky="w")
        row += 1

        # Forge Model Directory
        ttk.Label(frm, text="Forge Model Directory:").grid(column=0, row=row, sticky="w")
        forge_dir_entry = ttk.Entry(frm, width=80)
        forge_dir_entry.insert(0, d.forge_model_directory)
        forge_dir_entry.grid(column=1, row=row, sticky="w")
        ttk.Button(frm, text="Browse", command=lambda: pick_folder(forge_dir_entry)).grid(column=2, row=row)
        row += 1

        # LoRA Model Directory
        ttk.Label(frm, text="LoRA Model Directory:").grid(column=0, row=row, sticky="w")
        lora_dir_entry = ttk.Entry(frm, width=80)
        lora_dir_entry.insert(0, d.lora_model_directory)
        lora_dir_entry.grid(column=1, row=row, sticky="w")
        ttk.Button(frm, text="Browse", command=lambda: pick_folder(lora_dir_entry)).grid(column=2, row=row)
        row += 1

        # Server URL
        ttk.Label(frm, text="Server URL:").grid(column=0, row=row, sticky="w")
        server_url_entry = ttk.Entry(frm, width=80)
        server_url_entry.insert(0, d.server_url)
        server_url_entry.grid(column=1, row=row, sticky="w")
        row += 1

        # CivitAI Token
        ttk.Label(frm, text="CivitAI API Token:").grid(column=0, row=row, sticky="w")
        civit_token_entry = ttk.Entry(frm, width=80)
        civit_token_entry.insert(0, d.civit_api_token)
        civit_token_entry.grid(column=1, row=row, sticky="w")
        row += 1

        # Aria2 Path
        ttk.Label(frm, text="Aria2 Executable Path:").grid(column=0, row=row, sticky="w")
        aria2_entry = ttk.Entry(frm, width=80)
        aria2_entry.insert(0, d.aria2_path)
        aria2_entry.grid(column=1, row=row, sticky="w")
        ttk.Button(frm, text="Browse", command=lambda: pick_file(aria2_entry, [("Executable", "*.exe"), ("All", "*.*")])).grid(column=2, row=row)
        row += 1

        # Anima Text Encoder
        ttk.Label(frm, text="Anima Text Encoder Path:").grid(column=0, row=row, sticky="w")
        anima_text_entry = ttk.Entry(frm, width=80)
        anima_text_entry.insert(0, d.anima_text_encoder_path)
        anima_text_entry.grid(column=1, row=row, sticky="w")
        ttk.Button(frm, text="Browse", command=lambda: pick_file(anima_text_entry, [("SafeTensor", "*.safetensors"), ("All", "*.*")])).grid(column=2, row=row)
        row += 1

        # Anima VAE
        ttk.Label(frm, text="Anima VAE Path:").grid(column=0, row=row, sticky="w")
        anima_vae_entry = ttk.Entry(frm, width=80)
        anima_vae_entry.insert(0, d.anima_vae_path)
        anima_vae_entry.grid(column=1, row=row, sticky="w")
        ttk.Button(frm, text="Browse", command=lambda: pick_file(anima_vae_entry, [("SafeTensor", "*.safetensors"), ("All", "*.*")])).grid(column=2, row=row)
        row += 1

        # Output Directory
        ttk.Label(frm, text="Image Output Directory:").grid(column=0, row=row, sticky="w")
        out_dir_entry = ttk.Entry(frm, width=80)
        out_dir_entry.insert(0, d.output_directory)
        out_dir_entry.grid(column=1, row=row, sticky="w")
        ttk.Button(frm, text="Browse", command=lambda: pick_folder(out_dir_entry)).grid(column=2, row=row)
        row += 1

        # Comfy Input Directory
        ttk.Label(frm, text="ComfyUI Input Directory:").grid(column=0, row=row, sticky="w")
        comfy_in_entry = ttk.Entry(frm, width=80)
        comfy_in_entry.insert(0, d.comfy_input_directory)
        comfy_in_entry.grid(column=1, row=row, sticky="w")
        ttk.Button(frm, text="Browse", command=lambda: pick_folder(comfy_in_entry)).grid(column=2, row=row)
        row += 1

        # Comfy Output Directory
        ttk.Label(frm, text="ComfyUI Output Directory:").grid(column=0, row=row, sticky="w")
        comfy_out_entry = ttk.Entry(frm, width=80)
        comfy_out_entry.insert(0, d.comfy_output_directory)
        comfy_out_entry.grid(column=1, row=row, sticky="w")
        ttk.Button(frm, text="Browse", command=lambda: pick_folder(comfy_out_entry)).grid(column=2, row=row)
        row += 1

        # Auto DL Lora
        ttk.Label(frm, text="Auto Download LoRA:").grid(column=0, row=row, sticky="w")
        auto_lora_chk = ttk.Checkbutton(frm, variable=auto_dl_lora_var)
        auto_lora_chk.grid(column=1, row=row, sticky="w")
        row += 1

        # Auto DL Checkpoints
        ttk.Label(frm, text="Auto Download Checkpoints:").grid(column=0, row=row, sticky="w")
        auto_ckpt_chk = ttk.Checkbutton(frm, variable=auto_dl_checkpoints_var)
        auto_ckpt_chk.grid(column=1, row=row, sticky="w")
        row += 1

        # Job Types
        ttk.Label(frm, text="Acceptable Job Types:").grid(column=0, row=row, sticky="nw")
        job_options = ["txt2img", "upscale", "face fix", "img2img", "img2vid"]
        label_to_internal = {
            "txt2img": "generate",
            "upscale": "upscale",
            "face fix": "facefix",
            "img2img": "img2img",
            "img2vid": "img2vid",
        }
        job_listbox = tk.Listbox(frm, selectmode="multiple", height=len(job_options), exportselection=False)
        for opt in job_options:
            job_listbox.insert(tk.END, opt)
        job_listbox.grid(column=1, row=row, sticky="w")

        # Preselect saved job types
        try:
            for idx, label in enumerate(job_options):
                internal = label_to_internal[label]
                if internal in d.accepted_job_types:
                    job_listbox.selection_set(idx)
        except:
            pass

        row += 1

        # Save Settings
        def apply_job_selection():
            sel = job_listbox.curselection()
            selected_labels = [job_listbox.get(i) for i in sel]
            d.accepted_job_types = [label_to_internal[l] for l in selected_labels]

        def save_and_update():
            apply_job_selection()

            d.forge_model_directory = forge_dir_entry.get().strip()
            d.lora_model_directory = lora_dir_entry.get().strip()
            d.server_url = server_url_entry.get().strip()
            d.civit_api_token = civit_token_entry.get().strip()
            d.aria2_path = aria2_entry.get().strip()
            d.output_directory = out_dir_entry.get().strip()
            d.worker_name = worker_name_entry.get().strip()
            d.comfy_input_directory = comfy_in_entry.get().strip()
            d.comfy_output_directory = comfy_out_entry.get().strip()
            d.auto_dl_lora = auto_dl_lora_var.get()
            d.auto_dl_checkpoints = auto_dl_checkpoints_var.get()
            d.anima_text_encoder_path = anima_text_entry.get().strip()
            d.anima_vae_path = anima_vae_entry.get().strip()

            try:
                self.save_to_disk()
                messagebox.showinfo("Saved", "Settings saved successfully.")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to save settings:\n{e}")

        ttk.Button(frm, text="Save Settings", command=save_and_update).grid(column=1, row=row, sticky="w")
        row += 1

        # Start Worker
        def start_worker():
            save_and_update()
            root.destroy()
            app = app_factory(self)
            app.run()

        ttk.Button(frm, text="Start Worker!", command=start_worker).grid(column=1, row=row, sticky="w")
        row += 1

        ttk.Button(frm, text="Quit", command=root.destroy).grid(column=1, row=row, sticky="w")

        root.mainloop()


# =========================
# HASHING / DB
# =========================

class HashDatabase:
    def __init__(self, checkpoint_db: str, lora_db: str):
        self.checkpoint_db = checkpoint_db
        self.lora_db = lora_db

    def _load_hashes_file(self, path: str) -> List[List[str]]:
        if not os.path.exists(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("hashes", [])
        except:
            return []

    def _save_hashes_file(self, path: str, hashes: List[List[str]]):
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"hashes": hashes}, f, indent=4)

    def load_hashes(self, type_: str) -> List[List[str]]:
        if type_ == "checkpoints":
            return self._load_hashes_file(self.checkpoint_db)
        elif type_ == "loras":
            return self._load_hashes_file(self.lora_db)
        return []

    def save_hashes(self, type_: str, hashes: List[List[str]]):
        if type_ == "checkpoints":
            self._save_hashes_file(self.checkpoint_db, hashes)
        elif type_ == "loras":
            self._save_hashes_file(self.lora_db, hashes)

    def add_server_hash(self, server_hash: str, filename: str, type_: str):
        hashes_list = self.load_hashes(type_)

        # Skip if already stored
        for entry in hashes_list:
            if entry[0] == server_hash:
                return

        # Store in unified format
        hashes_list.append([server_hash, filename, "unknown"])
        self.save_hashes(type_, hashes_list)

    def load_checkpoint_map(self):
        hashes = self.load_hashes("checkpoints")
        return {
            h[0]: {
                "filename": h[1],
                "base_model": h[2] if len(h) > 2 else "unknown"
            }
            for h in hashes
        }

    def load_lora_map(self) -> Dict[str, str]:
        hashes = self.load_hashes("loras")
        return {h[0].lower(): h[1] for h in hashes}
    def register_existing_models(self, model_dir: str, type_: str):
        files = [f for f in os.listdir(model_dir) if f.endswith(".safetensors")]

        for filename in files:
            # Skip if already registered
            hashes = self.load_hashes(type_)
            if any(entry[1] == filename for entry in hashes):
                continue

            # Query CivitAI by filename
            try:
                info = requests.get(
                    f"https://civitai.com/api/v1/model-versions?query={filename}",
                    timeout=10
                ).json()

                if not info or "items" not in info or len(info["items"]) == 0:
                    print(f"[REGISTER] No CivitAI match for {filename}")
                    continue

                # Get the server hash
                server_hash = info["items"][0]["modelVersion"]["hash"]
                print(f"[REGISTER] {filename} → {server_hash}")

                # Store it
                hashes.append([server_hash, filename])
                self.save_hashes(type_, hashes)

            except Exception as e:
                print(f"[REGISTER ERROR] {filename}: {e}")

# =========================
# CIVITAI / DOWNLOAD
# =========================

class CivitDownloader:
    def __init__(self, config, hash_db, worker):
        self.config = config
        self.hash_db = hash_db
        self.worker = worker

    def download_missing_loras(self, lora_hash_list: List[str]):
        self._download_by_hash_list(
            lora_hash_list,
            self.config.lora_db,
            self.config.lora_model_directory,
            "http://127.0.0.1:7860/sdapi/v1/refresh-loras",
            "LORA",
        )

    def download_missing_models(self, model_hash_list: List[str]):
        self._download_by_hash_list(
            model_hash_list,
            self.config.checkpoint_db,
            self.config.forge_model_directory,
            "http://127.0.0.1:7860/sdapi/v1/refresh-checkpoints",
            "MODEL",
        )

    def civitai_get_base_model_from_hash(self, model_hash: str) -> str:
        try:
            url = f"https://civitai.com/api/v1/model-versions/by-hash/{model_hash}"
            info = requests.get(url, timeout=10).json()
            return info.get("baseModel", "Unknown")
        except Exception as e:
            print(f"[CIVIT ERROR] {e}")
            return "Unknown"

    def resolve_civitai_download_url(self, url: str) -> str:
        req_id = str(uuid.uuid4())
        playwright_requests.put((req_id, url))

        # Wait for result
        while req_id not in playwright_results:
            time.sleep(0.1)

        return playwright_results.pop(req_id)

    def python_download(self, download_url: str, save_dir: str, filename: str) -> Optional[str]:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "application/octet-stream",
            "Referer": "https://civitai.com/",
        }
        if self.config.civit_api_token:
            headers["Authorization"] = f"Bearer {self.config.civit_api_token}"

        os.makedirs(save_dir, exist_ok=True)
        save_path = os.path.join(save_dir, filename)

        print(f"[DOWNLOAD] Starting download: {filename}")

        for attempt in range(3):
            try:
                with requests.get(download_url, headers=headers, stream=True, timeout=60) as r:
                    if r.status_code != 200:
                        print(f"[DOWNLOAD ERROR] HTTP {r.status_code}, retrying...")
                        time.sleep(2)
                        continue

                    first_chunk = next(r.iter_content(4096))
                    if b"<html" in first_chunk.lower():
                        print("[DOWNLOAD ERROR] HTML received instead of model, retrying...")
                        time.sleep(2)
                        continue

                    with open(save_path, "wb") as f:
                        f.write(first_chunk)
                        for chunk in r.iter_content(8192):
                            if chunk:
                                f.write(chunk)

                size_mb = os.path.getsize(save_path) / (1024 * 1024)
                if size_mb < 5:
                    print(f"[DOWNLOAD ERROR] File too small ({size_mb:.2f} MB), retrying...")
                    time.sleep(2)
                    continue

                print(f"[DOWNLOAD] Completed: {filename} ({size_mb:.2f} MB)")
                return save_path

            except Exception as e:
                print(f"[DOWNLOAD ERROR] Attempt {attempt+1}: {e}")
                time.sleep(2)

        print(f"[DOWNLOAD FAILED] Could not download {filename}")
        return 

    def _download_by_hash_list(
        self,
        hash_list: List[str],
        db_path: str,
        model_dir: str,
        refresh_url: str,
        type_name: str,
        ):
        if not hash_list:
            print(f"[{type_name}] No missing {type_name.lower()}s provided.")
            return

        try:
            with open(db_path, "r", encoding="utf-8") as f:
                db = json.load(f)
        except Exception:
            db = {"hashes": []}

        existing_hashes = {entry[0]: entry[1] for entry in db.get("hashes", [])}

        for h in hash_list:
            h = h.lower().strip()
            if h in existing_hashes:
                print(f"[{type_name}] Already have {h}, skipping.")
                continue

            print(f"[{type_name}] Querying CivitAI for hash {h}...")

            try:
                info = requests.get(
                    f"https://civitai.com/api/v1/model-versions/by-hash/{h}",
                    timeout=10
                ).json()
            except Exception as e:
                print(f"[{type_name} ERROR] Failed to query CivitAI: {e}")
                continue

            version_id = info.get("id")
            if not version_id:
                print(f"[{type_name}] ERROR: No version ID for {h}")
                continue

            files = info.get("files", [])
            target_file = None

            for f in files:
                if f.get("type") == "Model":
                    meta = f.get("metadata", {})
                    if meta.get("format") == "SafeTensor":
                        target_file = f
                        break

            if not target_file:
                print(f"[{type_name}] ERROR: No SafeTensor file found for {h}")
                continue

            file_id = target_file["id"]
            filename = target_file["name"]
            download_url = f"https://civitai.com/api/download/models/{version_id}?fileId={file_id}"

            existing_path = os.path.join(model_dir, filename)

            # -------------------------------------------------------
            # NEW LOGIC: If file exists, DO NOT download it.
            # Query CivitAI for baseModel and register in DB.
            # -------------------------------------------------------
            if os.path.exists(existing_path):
                print(f"[{type_name}] File already exists on disk: {filename}")
                print(f"[{type_name}] Querying CivitAI for baseModel...")

                base_model = self.civitai_get_base_model_from_hash(h)
                print(f"[{type_name}] baseModel = {base_model}")

                db["hashes"].append([h, filename, base_model])

                with open(db_path, "w", encoding="utf-8") as f:
                    json.dump(db, f, indent=4)

                continue

            # -------------------------------------------------------
            # Normal download path
            # -------------------------------------------------------
            saved_path = self.python_download(download_url, model_dir, filename)
            if not saved_path:
                print(f"[{type_name}] ERROR: Failed to download {filename}")
                continue

            # After download, also record baseModel from CivitAI
            base_model = self.civitai_get_base_model_from_hash(h)
            db["hashes"].append([h, filename, base_model])

            with open(db_path, "w", encoding="utf-8") as f:
                json.dump(db, f, indent=4)

            print(f"[{type_name}] Registered {filename} → {h} → {base_model}")

        try:
            self.worker.worker_login()
        except Exception as e:
            print(f"[{type_name}] ERROR reinitializing worker: {e}")


# =========================
# FORGE CLIENT
# =========================

class ForgeClient:
    def __init__(self, config: WorkerConfigData, hash_db: HashDatabase):
        self.config = config
        self.hash_db = hash_db

    @staticmethod
    def str_to_bool(s: Any) -> bool:
        if isinstance(s, bool):
            return s
        return str(s).lower() in ["true", "1", "yes", "y"]

    def convert_model_hash(self, model_hash: str) -> str:
        if not model_hash:
            return model_hash
        model_hash = model_hash.strip()
        model_map = self.hash_db.load_checkpoint_map()
        filename = model_map.get(model_hash)
        if not filename:
            print(f"[MODEL] No filename found for model hash {model_hash}")
            return model_hash
        return filename.replace(".safetensors", "").replace(".ckpt", "")

    def lora_conversion(self, prompt: str) -> str:
        if not prompt:
            return prompt
        lora_map = self.hash_db.load_lora_map()
        pattern = r"<\s*lora\s*:\s*([^:>\s]+)\s*:\s*([0-9.]+)\s*>"

        def repl(match):
            lora_hash = match.group(1).strip()
            weight = match.group(2)
            filename = lora_map.get(lora_hash)
            if not filename:
                print(f"[LORA] No filename found for hash {lora_hash}")
                return match.group(0)
            name_no_ext = filename.replace(".safetensors", "").replace(".ckpt", "")
            return f"<lora:{name_no_ext}:{weight}>"

        return re.sub(pattern, repl, prompt, flags=re.IGNORECASE)

    @staticmethod
    def forge_txt2img(payload: Dict[str, Any]) -> Dict[str, Any]:
        r = requests.post("http://127.0.0.1:7860/sdapi/v1/txt2img", json=payload, timeout=600)
        r.raise_for_status()
        return r.json()

    @staticmethod
    def forge_img2img(payload: Dict[str, Any]) -> Dict[str, Any]:
        r = requests.post("http://127.0.0.1:7860/sdapi/v1/img2img", json=payload, timeout=600)
        r.raise_for_status()
        return r.json()

    @staticmethod
    def forge_upscale(payload: Dict[str, Any]) -> Dict[str, Any]:
        r = requests.post("http://127.0.0.1:7860/sdapi/v1/extra-single-image", json=payload, timeout=600)
        r.raise_for_status()
        return r.json()

    @staticmethod
    def forge_img2vid_stub(payload: Dict[str, Any]) -> Dict[str, Any]:
        # Stub: keep structure, but clearly indicate it's not implemented
        print("[IMG2VID] Stub called. No actual img2vid endpoint implemented.")
        return {"images": []}

    def set_model_option(self, model: str, is_anima: bool):
        opts_url = "http://127.0.0.1:7860/sdapi/v1/options"
        if is_anima:
         
            requests.post(opts_url, json={
                "sd_model_checkpoint": self.convert_model_hash(model),
                "forge_additional_modules": [
                    self.config.anima_text_encoder_path,
                    self.config.anima_vae_path
                ]
            }, timeout=10)

        else:
            try:
                requests.post(opts_url, json={"sd_model_checkpoint": self.convert_model_hash(model), "forge_additional_modules": []}, timeout=10)
            except Exception as e:
                print(f"[FORGE] Failed to set model option: {e}")


# =========================
# RESULT UPLOADER
# =========================

class ResultUploader:
    def __init__(self, config: WorkerConfigData):
        self.config = config
        self.current_progress_thread: Optional[Thread] = None
        self.current_stop_event: Optional[Event] = None
        self.session_start_time = time.time()
        self.jobs_completed = 0
        self.current_model_name = "Unknown"

    def start_progress_thread(self, job_id: str):
        if self.current_progress_thread and self.current_progress_thread.is_alive():
            return
        self.current_stop_event = Event()
        self.current_progress_thread = Thread(
            target=self._progress_loop,
            args=(job_id, self.current_stop_event, self.current_model_name),
            daemon=True,
        )
        self.dashboard_start_time = time.time()
        self.current_progress_thread.start()

    def _progress_loop(self, job_id: str, stop_event: Event, model_name: str):
        import time
        import sys

        bar_length = 40
        start_time = self.dashboard_start_time  # set in start_progress_thread()

        # ANSI colors
        CYAN = "\033[96m"
        GREEN = "\033[92m"
        YELLOW = "\033[93m"
        RESET = "\033[0m"

        # Hide cursor for clean UI
        sys.stdout.write("\033[?25l")
        sys.stdout.flush()

        # Print initial blank lines so we can move up cleanly
        print("\n" * 9)

        while not stop_event.is_set():
            try:
                r = requests.get(
                    "http://127.0.0.1:7860/sdapi/v1/progress?skip_current_image=false",
                    timeout=5,
                )
                data = r.json()

                progress = int(data.get("progress", 0) * 100)
                state = data.get("state", {}) or {}
                step = state.get("sampling_step", 0)
                total_steps = state.get("sampling_steps", 0)

                # ETA
                elapsed = time.time() - start_time
                if progress > 0:
                    est_total = elapsed / (progress / 100)
                    eta = est_total - elapsed
                else:
                    eta = 0

                eta_str = time.strftime("%M:%S", time.gmtime(max(0, eta)))
                elapsed_str = time.strftime("%M:%S", time.gmtime(elapsed))

                # Progress bar
                filled = int(bar_length * (progress / 100))
                bar = GREEN + "█" * filled + RESET + "░" * (bar_length - filled)

                # Move cursor up 9 lines (dashboard height)
                sys.stdout.write("\033[9F")

                # Uptime
                uptime = time.time() - self.session_start_time
                uptime_str = time.strftime("%H:%M:%S", time.gmtime(uptime))

                # Draw dashboard (clear each line first)
                def line(text):
                    sys.stdout.write("\033[K")  # clear the entire line
                    print(text.ljust(70))       

                line(f"{CYAN}┌──────────────────────────────────────────────────────────────┐{RESET}")
                line(f"{CYAN}│{RESET} JOB {job_id:<56}{CYAN}│{RESET}")
                line(f"{CYAN}│{RESET} Model: {model_name:<50}{CYAN}│{RESET}")
                line(f"{CYAN}│{RESET} Steps: {step} / {total_steps:<45}{CYAN}│{RESET}")
                line(f"{CYAN}│{RESET} Progress: [{bar}] {progress:3d}%{' ' * 10}{CYAN}│{RESET}")
                line(f"{CYAN}│{RESET} ETA: {eta_str:<8} Elapsed: {elapsed_str:<8}{' ' * 26}{CYAN}│{RESET}")
                line(f"{CYAN}│{RESET} Uptime: {uptime_str:<10} Jobs Completed: {self.jobs_completed:<5}{' ' * 14}{CYAN}│{RESET}")
                line(f"{CYAN}└──────────────────────────────────────────────────────────────┘{RESET}")
                line("")  # spacer line

                # Send progress to server
                preview = data.get("current_image")
                requests.post(
                    self.config.progress_url,
                    json={
                        "job_id": job_id,
                        "progress": progress,
                        "status": "InProgress",
                        "step": step,
                        "total_steps": total_steps,
                        "preview": preview,
                    },
                    timeout=5,
                )

            except Exception as e:
                print(f"[PROGRESS] Error: {e}")

            time.sleep(1)

        # Show cursor again
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()

        print()  # clean newline after dashboard

        # ============================
        # JOB COMPLETED BANNER
        # ============================

        GREEN = "\033[92m"
        CYAN = "\033[96m"
        RESET = "\033[0m"

        banner = f"""
    {GREEN}┌──────────────────────────────────────────────────────────────┐
    │{RESET}                      {CYAN}JOB COMPLETED!{RESET}                          {GREEN}│
    │{RESET}   Worker finished job {CYAN}{job_id}{RESET} successfully.                     {GREEN}│
    │{RESET}   Total jobs this session: {CYAN}{self.jobs_completed}{RESET}                     {GREEN}│
    └──────────────────────────────────────────────────────────────┘{RESET}
    """

        print

    def stop_progress_thread(self):
        if self.current_stop_event is not None:
            self.current_stop_event.set()
        if self.current_progress_thread is not None:
            self.current_progress_thread.join()
        self.current_stop_event = None
        self.current_progress_thread = None

    def submit_results(
        self,
        image_files: List[Any],   # Can be file paths or (filename, bytes)
        batch_size: int,
        channel_id: str,
        requester: str,
        job_id: str,
        prompt: str,
        model: str,
        worker_id: str,
    ):
        if not image_files:
            print("[UPLOAD ERROR] No image files provided.")
            return

        normalized: List[Tuple[str, bytes]] = []
        file_paths: List[str] = []  # Track actual disk paths for deletion

        # -----------------------------
        # NORMALIZE INPUT
        # -----------------------------
        for item in image_files:

            # Case 1: raw bytes (no filename)
            if isinstance(item, bytes):
                normalized.append(("image.png", item))
                continue

            # Case 2: (filename, bytes)
            if isinstance(item, tuple) and len(item) == 2 and isinstance(item[1], bytes):
                normalized.append((item[0], item[1]))
                continue

            # Case 3: (filename, (filename, bytes))
            if isinstance(item, tuple) and len(item) == 2 and isinstance(item[1], tuple):
                inner = item[1]
                if len(inner) == 2 and isinstance(inner[1], bytes):
                    normalized.append((inner[0], inner[1]))
                    continue

            # Case 4: actual file path (string)
            if isinstance(item, str) and os.path.exists(item):
                try:
                    with open(item, "rb") as f:
                        data = f.read()
                    normalized.append((os.path.basename(item), data))
                    file_paths.append(item)
                    continue
                except Exception as e:
                    print(f"[UPLOAD ERROR] Could not read file {item}: {e}")
                    continue

            print(f"[UPLOAD WARNING] Unrecognized image format: {item}")

        if not normalized:
            print("[UPLOAD ERROR] No valid image bytes found.")
            return

        # Limit to batch size
        selected = normalized[:batch_size]

        print(f"[INFO] Uploading {len(selected)} images for job {job_id}")

        # -----------------------------
        # BUILD PAYLOAD
        # -----------------------------
        payload = {
            "worker_id": worker_id,
            "requester": requester,
            "channel": channel_id,
            "job_id": job_id,
            "prompt": prompt,
            "model": model,
        }

        files = []
        for filename, data in selected:
            files.append(("images", (filename, data)))

        # -----------------------------
        # START DASHBOARD PROGRESS THREAD
        # -----------------------------
        self.current_model_name = model  # used by dashboard
        self.current_model_name = model
        self.start_progress_thread(job_id)

        # -----------------------------
        # UPLOAD
        # -----------------------------
        try:
            response = requests.post(
                f"{self.config.server_url}/api/upload",
                data=payload,
                files=files,
                timeout=60,
            )
            print("\n[UPLOAD RESPONSE]", response.status_code, response.text)

            # Mark job complete
            try:
                requests.post(
                    self.config.progress_url,
                    json={
                        "job_id": job_id,
                        "progress": 100,
                        "job_status": "Completed",
                    },
                    timeout=5,
                )
            except Exception:
                pass

            # Stop dashboard
            self.stop_progress_thread()

            if response.status_code == 200:
                self.jobs_completed += 1
                print(f"✅ Job ID {job_id} completed — {len(selected)} images uploaded.")
                print(f"📊 Jobs completed this session: {self.jobs_completed}")

        except Exception as e:
            print(f"[UPLOAD ERROR] Failed to upload images: {e}")
            self.stop_progress_thread()
            return

        # -----------------------------
        # CLEANUP LOCAL FILES
        # -----------------------------
        print("[CLEANUP] Removing local image files...")

        for path in file_paths:
            try:
                if os.path.exists(path):
                    os.remove(path)
                    print(f"[CLEANUP] Deleted {path}")
            except Exception as e:
                print(f"[CLEANUP ERROR] Could not delete {path}: {e}")


# =========================
# JOB ROUTER
# =========================

class JobRouter:
    def __init__(self, config, forge, uploader, hash_db):
        self.config = config
        self.forge = forge
        self.uploader = uploader
        self.hash_db = hash_db

    def _parse_resolution(self, resolution: Optional[str]) -> Tuple[int, int]:
        if resolution is None:
            resolution = "1024x1024"
        try:
            w, h = resolution.lower().split("x")
            width = min(int(w), 1536)
            height = min(int(h), 1536)
        except Exception:
            width, height = 1024, 1024
        return width, height

    

    def handle_generate(self, job: Dict[str, Any], worker_id: str):
        # -----------------------------
        # 1. Extract job fields
        # -----------------------------
        channel_id = job.get("channel")
        prompt = job.get("requested_prompt") or ""
        neg_prompt = job.get("negative_prompt") or ""
        resolution = job.get("resolution")
        batch_size = int(job.get("batch_size", 1))
        cfg_scale = job.get("config_scale") or 7
        steps = job.get("steps") or 20
        job_id = job.get("job_id")
        requester = job.get("requester") or ""
        sampler = job.get("sampler") or "DPM++ 2M Karras"
        clip_skip = int(job.get("clip_skip") or 1)
        face_fix = job.get("face_fix")
        hires_fix = job.get("hires_fix")
        model_hash = job.get("model") or ""

        # -----------------------------
        # 2. Resolve model hash → filename + base_model
        # -----------------------------
        model_map = self.hash_db.load_checkpoint_map()
        entry = model_map.get(model_hash)

        if not entry:
            raise ValueError(f"Unknown model hash: {model_hash}")

        filename = entry["filename"]
        base_model = entry["base_model"].lower()

        # -----------------------------
        # 3. Detect model type
        # -----------------------------
        is_anima = (base_model == "anima")
        is_flux = (base_model == "flux")
        is_sdxl = (base_model == "sdxl")
        is_sd15 = (base_model == "sd 1.5")

        print(f"[MODEL] {filename} → baseModel={base_model}")

        # -----------------------------
        # 4. ANIMA module injection
        # -----------------------------
        anima_modules = []
        if is_anima:
            anima_modules = [
                self.config.anima_text_encoder_path,
                self.config.anima_vae_path
            ]
            print("[ANIMA] Injecting ANIMA text encoders")
        
        # -----------------------------
        # 5. Convert LoRA tags
        # -----------------------------
        prompt = self.forge.lora_conversion(prompt)

        # -----------------------------
        # 6. Parse resolution
        # -----------------------------
        width, height = self._parse_resolution(resolution)

        # -----------------------------
        # 7. Build payload
        # -----------------------------
        payload = {
            "prompt": prompt,
            "negative_prompt": neg_prompt,
            "sampler_index": sampler,
            "steps": steps,
            "cfg_scale": cfg_scale,
            "batch_size": batch_size,
            "width": width,
            "height": height,

            "sd_model_checkpoint": filename.replace(".safetensors", ""),
            "model": filename.replace(".safetensors", ""),

            "enable_hr": self.forge.str_to_bool(hires_fix),
            "hr_scale": 1.5,
            "hr_second_pass_steps": 15,
            "denoising_strength": 0.4,
            "hr_upscaler": "ESRGAN_4x",
            "hr_additional_modules": [],
            "alwayson_scripts": {
                "ADetailer": {
                    "args": [
                        self.forge.str_to_bool(face_fix),
                        self.forge.str_to_bool(face_fix),
                        {
                            "ad_model": "face_yolov8s.pt",
                            "ad_prompt": prompt,
                            "ad_negative_prompt": neg_prompt,
                            "ad_confidence": 0.3,
                            "ad_mask_blur": 4,
                            "ad_denoising_strength": 0.4,
                            "ad_inpaint_only_masked": True,
                            "ad_inpaint_only_masked_padding": 32,
                        },
                    ]
                }
            },

            "clip_skip": clip_skip,
            "save_images": True,
            "filter_nsfw": False,
        }

        # -----------------------------
        # 8. Inject ANIMA modules
        # -----------------------------
        if is_anima:
            payload["hr_additional_modules"] = anima_modules
            payload["scheduler"] = "Beta"
        else:
            payload["scheduler"] = "Automatic"

        # -----------------------------
        # 9. Set model in Forge
        # -----------------------------
        self.forge.set_model_option(filename.replace(".safetensors", ""),is_anima)

        # -----------------------------
        # 10. Start progress tracking
        # -----------------------------
        self.uploader.start_progress_thread(job_id)

        # -----------------------------
        # 11. Run Forge
        # -----------------------------
        r_json = self.forge.forge_txt2img(payload)

        # -----------------------------
        # 12. Extract images
        # -----------------------------
        images = []
        raw_images = r_json.get("images", [])

        for i, img_b64 in enumerate(raw_images[1:], start=1):
            img_data = base64.b64decode(img_b64.split(",", 1)[-1])
            outname = f"{job_id}_{i}.png"
            outpath = os.path.join(self.config.output_directory, outname)
            os.makedirs(self.config.output_directory, exist_ok=True)
            with open(outpath, "wb") as f:
                f.write(img_data)
            with open(outpath, "rb") as f:
                images.append((outname, f.read()))

        # -----------------------------
        # 13. Upload results
        # -----------------------------
        self.uploader.submit_results(
            images,
            batch_size,
            channel_id,
            requester,
            job_id,
            prompt,
            filename,
            worker_id,
        )

    def handle_img2img(self, job: Dict[str, Any], worker_id: str):
        channel_id = job.get("channel")
        model = job.get("model") or ""
        prompt = job.get("requested_prompt") or ""
        neg_prompt = job.get("negative_prompt") or ""
        resolution = job.get("resolution")
        batch_size = int(job.get("batch_size", 1))
        job_id = job.get("job_id")
        requester = job.get("requester") or ""
        image_link = job.get("image_link")

        if not image_link:
            print("[IMG2IMG] No image_link provided.")
            return

        width, height = self._parse_resolution(resolution)

        img_resp = requests.get(image_link, timeout=30)
        img_resp.raise_for_status()
        img_b64 = base64.b64encode(img_resp.content).decode("utf-8")

        payload = {
            "init_images": [img_b64],
            "prompt": prompt,
            "negative_prompt": neg_prompt,
            "width": width,
            "height": height,
            "override_settings": {
                "sd_model_checkpoint": model or "",
            },
        }

        self.forge.set_model_option(model)
        self.uploader.start_progress_thread(job_id)

        r_json = self.forge.forge_img2img(payload)
        images: List[Tuple[str, bytes]] = []

        for i, out_b64 in enumerate(r_json.get("images", [])):
            img_data = base64.b64decode(out_b64.split(",", 1)[-1])
            filename = f"{job_id}_img2img_{i}.png"
            filepath = os.path.join(self.config.output_directory, filename)
            os.makedirs(self.config.output_directory, exist_ok=True)
            with open(filepath, "wb") as f:
                f.write(img_data)
            with open(filepath, "rb") as f:
                img_bytes = f.read()
            images.append((filename, img_bytes))

        self.uploader.submit_results(
            images,
            batch_size,
            channel_id,
            requester,
            job_id,
            prompt,
            model or "",
            worker_id,
        )

    def handle_upscale(self, job: Dict[str, Any], worker_id: str):
        channel_id = job.get("channel")
        job_id = job.get("job_id")
        requester = job.get("requester") or ""
        scale = float(job.get("config_scale") or 2.0)
        image_link = job.get("image_link")

        if not image_link:
            print("[UPSCALE] No image_link provided.")
            return

        # ---------------------------------------------------------
        # 1. Download image
        # ---------------------------------------------------------
        try:
            img_resp = requests.get(image_link, timeout=30)
            img_resp.raise_for_status()
            img_bytes = img_resp.content
            img_b64 = base64.b64encode(img_bytes).decode("utf-8")
            print("[UPSCALE] Downloaded input image")
        except Exception as e:
            print(f"[UPSCALE] Failed to download input image: {e}")
            return

        # ---------------------------------------------------------
        # 2. Build Forge Neo upscale payload
        # ---------------------------------------------------------
        payload = {
            "resize_mode": 0,
            "upscaling_resize": scale,
            "upscaler_1": "R-ESRGAN 4x+",   # You can change this
            "image": img_b64
        }

        self.uploader.start_progress_thread(job_id)

        # ---------------------------------------------------------
        # 3. Send upscale request to Forge Neo
        # ---------------------------------------------------------
        try:
            resp = requests.post(
                "http://127.0.0.1:7860/sdapi/v1/extra-single-image",
                json=payload,
                timeout=120
            )
            resp.raise_for_status()
            r_json = resp.json()
        except Exception as e:
            print(f"[UPSCALE] Forge Neo upscale failed: {e}")
            self.uploader.stop_progress_thread()
            return

        # ---------------------------------------------------------
        # 4. Extract returned image
        # ---------------------------------------------------------
        try:
            out_b64 = r_json["image"]
            out_bytes = base64.b64decode(out_b64.split(",", 1)[-1])
        except Exception as e:
            print(f"[UPSCALE] Failed to decode Forge output: {e}")
            self.uploader.stop_progress_thread()
            return

        # ---------------------------------------------------------
        # 5. Save + upload result
        # ---------------------------------------------------------
        filename = f"{job_id}_upscaled.png"
        filepath = os.path.join(self.config.output_directory, filename)
        os.makedirs(self.config.output_directory, exist_ok=True)

        with open(filepath, "wb") as f:
            f.write(out_bytes)

        with open(filepath, "rb") as f:
            final_bytes = f.read()

        images = [(filename, final_bytes)]

        self.uploader.submit_results(
            images,
            len(images),
            channel_id,
            requester,
            job_id,
            f"Upscaled {scale}x",
            "upscale",
            worker_id,
        )

    def handle_facefix(self, job: Dict[str, Any], worker_id: str):
        # Treat as img2img with face-fix model
        print("[FACEFIX] Delegating to img2img handler.")
        self.handle_img2img(job, worker_id)

    def handle_img2vid(self, job: Dict[str, Any], worker_id: str):
        channel_id = job.get("channel")
        prompt = job.get("requested_prompt") or ""
        job_id = job.get("job_id")
        requester = job.get("requester") or ""
        cfg_scale = job.get("config_scale") or 7
        image_link = job.get("image_link")

        if not image_link:
            print("[IMG2VID] No image_link provided.")
            return

        # ---------------------------------------------------------
        # 1. Download image → save to ComfyUI input directory
        # ---------------------------------------------------------
        comfy_input_dir = self.config.comfy_input_directory
        os.makedirs(comfy_input_dir, exist_ok=True)

        filename = f"{job_id}_input.png"
        full_path = os.path.join(comfy_input_dir, filename)

        try:
            img_resp = requests.get(image_link, timeout=30)
            img_resp.raise_for_status()
            with open(full_path, "wb") as f:
                f.write(img_resp.content)
            print(f"[IMG2VID] Saved input image to {full_path}")
        except Exception as e:
            print(f"[IMG2VID] Failed to download input image: {e}")
            return

        # ---------------------------------------------------------
        # 2. Load workflow JSON
        # ---------------------------------------------------------
        try:
            with open("Klover_img2vid.json", "r", encoding="utf-8") as f:
                workflow = json.load(f)
        except Exception as e:
            print(f"[IMG2VID] Failed to load workflow JSON: {e}")
            return

        # ---------------------------------------------------------
        # 3. Inject values into workflow
        # ---------------------------------------------------------
        workflow["52"]["inputs"]["image"] = filename
        workflow["90"]["inputs"]["text"] = prompt
        workflow["50"]["inputs"]["length"] = int(cfg_scale)*32


        # ---------------------------------------------------------
        # 4. Send workflow to ComfyUI
        # ---------------------------------------------------------
        payload = {
            "prompt": workflow,
            "client_id": "klover_worker",
            "output_node": "77"
        }

        self.uploader.start_progress_thread(job_id)

        try:
            resp = requests.post(
                "http://127.0.0.1:8188/prompt",
                json=payload,
                timeout=10
            )
            resp.raise_for_status()
        except Exception as e:
            print(f"[IMG2VID] Failed to send workflow to ComfyUI: {e}")
            self.uploader.stop_progress_thread()
            return

        prompt_id = resp.json().get("prompt_id")
        print(f"[IMG2VID] Prompt ID: {prompt_id}")

        # ---------------------------------------------------------
        # 5. Poll for completion
        # ---------------------------------------------------------
        while True:
            time.sleep(1)
            try:
                hist = requests.get(f"http://127.0.0.1:8188/history/{prompt_id}").json()
                if prompt_id in hist and "outputs" in hist[prompt_id]:
                    break
            except Exception:
                pass

        # ---------------------------------------------------------
        # 6. Find most recent .mp4 in ComfyUI output directory
        # ---------------------------------------------------------
        output_dir = self.config.comfy_output_directory
        print(f"[IMG2VID] Scanning output directory: {output_dir}")

        latest_mp4 = None
        latest_time = 0

        for root, dirs, files in os.walk(output_dir):
            for file in files:
                if file.lower().endswith(".mp4"):
                    full_path = os.path.join(root, file)
                    mtime = os.path.getmtime(full_path)
                    print(f"[IMG2VID] Found mp4: {full_path} (mtime={mtime})")

                    if mtime > latest_time:
                        latest_time = mtime
                        latest_mp4 = full_path

        if not latest_mp4:
            print("[IMG2VID] ERROR: No mp4 files found in output directory!")
            print("[IMG2VID] Double-check this path:", output_dir)
            self.uploader.stop_progress_thread()
            return

        print(f"[IMG2VID] Using most recent mp4: {latest_mp4}")

        # ---------------------------------------------------------
        # 7. Upload results
        # ---------------------------------------------------------
        try:
            with open(latest_mp4, "rb") as f:
                video_bytes = f.read()

            images = [(os.path.basename(latest_mp4), video_bytes)]

            self.uploader.submit_results(
                images,
                len(images),
                channel_id,
                requester,
                job_id,
                prompt,
                "img2vid",
                worker_id,
            )

        except Exception as e:
            print(f"[IMG2VID] Failed to upload mp4: {e}")
            self.uploader.stop_progress_thread()


# =========================
# WORKER LOOP / LOGIN / JOBS
# =========================

class WorkerApp:
    def __init__(self, config: WorkerConfig):
        self.config_obj = config
        self.config = config.data

        # Core components
        self.hash_db = HashDatabase(self.config.checkpoint_db, self.config.lora_db)
        self.downloader = CivitDownloader(self.config, self.hash_db, self)
        self.forge = ForgeClient(self.config, self.hash_db)
        self.uploader = ResultUploader(self.config)
        self.router = JobRouter(config.data, self.forge, self.uploader, self.hash_db)

        # Auto-update: load commit at startup
        self.current_commit = self.get_current_commit()

        # Worker state
        self.worker_id: Optional[str] = None
        self.gpu_idle_timer = self.config.idle_time_setting
        self.worker_main_loop: Optional[Timer] = None

    # ---------------------------------------------------------
    # AUTO-UPDATE SUPPORT
    # ---------------------------------------------------------
    def get_current_commit(self):
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True,
                text=True
            )
            commit = result.stdout.strip()

            print(f"[UPDATE] Current commit: {commit}")
            return commit
        except Exception as e:
            print(f"[UPDATE] Failed to read commit: {e}")
            return "unknown"

    def update_and_restart(self):
        try:
            print("[UPDATE] Pulling latest code...")
            result = subprocess.run(
                ["git", "pull"],
                capture_output=True,
                text=True
            )
            print(result.stdout)

            print("[UPDATE] Restarting worker...")
            print(sys.executable, [sys.executable] + sys.argv)
            os.execv(sys.executable, [sys.executable] + sys.argv)

        except Exception as e:
            print(f"[UPDATE] Failed to update: {e}")

    # ---------------------------------------------------------
    # GPU CHECK
    # ---------------------------------------------------------
    def is_gpu_idle(self, gpu_id: int, threshold: int = 10) -> bool:
        pynvml.nvmlInit()
        try:
            handle = pynvml.nvmlDeviceGetHandleByIndex(gpu_id)
            utilization = pynvml.nvmlDeviceGetUtilizationRates(handle)
            if utilization.gpu > threshold:
                self.gpu_idle_timer = self.config.idle_time_setting
                return False
            return True
        finally:
            pynvml.nvmlShutdown()

    # ---------------------------------------------------------
    # LOGIN
    # ---------------------------------------------------------
    def worker_login(self):
        print("🛜 Attempting to login...")

        checkpoint_hashes_list = self.hash_db.load_hashes("checkpoints")
        lora_hashes_list = self.hash_db.load_hashes("loras")

        def bool_val(v):
            return bool(v)

        existing_id = self.config_obj.load_worker_id()

        # Try existing worker ID
        if existing_id:
            self.worker_id = existing_id
            for _ in range(3):
                try:
                    resp = requests.post(
                        f"{self.config.server_url}/api/init",
                        json={
                            "worker_id": self.worker_id,
                            "checkpoints": checkpoint_hashes_list,
                            "acceptable_job_types": self.config.accepted_job_types,
                            "loras": lora_hashes_list,
                            "dl_lora": bool_val(self.config.auto_dl_lora),
                            "dl_checkpoint": bool_val(self.config.auto_dl_checkpoints),
                            "worker_name": self.config.worker_name,
                            "old_worker_id": "N/A"
                        },
                        timeout=5,
                    )

                    if resp.status_code in (401, 403):
                        raise ValueError("Invalid worker ID")

                    resp.raise_for_status()
                    print("[AUTH] Worker authenticated successfully!")
                    return

                except ValueError:
                    print("[AUTH] Server rejected worker_id — registering new one.")
                    break

                except Exception as e:
                    print(f"[AUTH] Temporary failure: {e}, retrying...")
                    time.sleep(15)

            print("[AUTH] Existing worker_id failed — registering new one.")

        # Register new worker
        while True:
            try:
                resp = requests.post(
                    f"{self.config.server_url}/api/init",
                    json={
                        "worker_id": "N/A",
                        "checkpoints": checkpoint_hashes_list,
                        "acceptable_job_types": self.config.accepted_job_types,
                        "loras": lora_hashes_list,
                        "dl_lora": bool_val(self.config.auto_dl_lora),
                        "dl_checkpoint": bool_val(self.config.auto_dl_checkpoints),
                        "worker_name": self.config.worker_name,
                        "old_worker_id": self.worker_id
                    },
                    timeout=5,
                )
                resp.raise_for_status()

                self.worker_id = resp.json().get("worker_id")
                self.config_obj.save_worker_id(self.worker_id)

                print(f"[AUTH] New worker registered: {self.worker_id}")
                return

            except Exception as e:
                print(f"[AUTH] Failed to register worker: {e}. Retrying...")
                time.sleep(15)

    # ---------------------------------------------------------
    # JOB FETCHING
    # ---------------------------------------------------------
    def get_job(self):
        if not self.worker_id:
            print("[JOB] No worker_id, skipping job fetch.")
            return

        try:
            resp = requests.get(
                f"{self.config.server_url}/api/get-job",
                params={
                    "worker_id": self.worker_id,
                    "commit": self.current_commit
                },
                timeout=10
            )
        except Exception as e:
            print(f"[JOB ERROR] Failed to contact server: {e}")
            time.sleep(2)
            self.worker_login()
            return

        try:
            job = resp.json()
        except Exception:
            print(f"[JOB ERROR] Invalid JSON from server: {resp.text[:200]}")
            time.sleep(2)
            return

        # UPDATE SIGNAL
        if job.get("status") == "update":
            print("[UPDATE] Server requested worker update.")
            self.update_and_restart()
            return

        # AUTH FAILURE
        if resp.status_code == 401:
            print("[AUTH ERROR] Worker failed authentication")
            time.sleep(3)
            return

        # MODEL / LORA DOWNLOADS
        if resp.status_code == 201:
            missing = job.get("download_models", [])
            print("[MODEL DOWNLOAD REQUIRED]", missing)
            self.downloader.download_missing_models(missing)
            self.worker_login()
            return

        if resp.status_code == 202:
            missing = job.get("download_loras", [])
            print("[LORA DOWNLOAD REQUIRED]", missing)
            self.downloader.download_missing_loras(missing)
            self.worker_login()
            return

        # NO JOBS
        if resp.status_code == 500:
            print("[QUEUE EMPTY] No Jobs Found")
            time.sleep(2)
            return

        # SERVER ERROR
        if resp.status_code != 200:
            print(f"[JOB ERROR] Unexpected status {resp.status_code}: {resp.text}")
            time.sleep(2)
            return

        # VALID JOB
        if "job_id" not in job:
            print("[JOB] No job available. Waiting...")
            time.sleep(2)
            return

        job_id = job["job_id"]
        job_type = job.get("request_type") or job.get("job_type")
        print(f"[JOB] Received job {job_id} ({job_type})")

        # ROUTE JOB
        if job_type == "generate":
            self.router.handle_generate(job, self.worker_id)
        elif job_type == "img2img":
            self.router.handle_img2img(job, self.worker_id)
        elif job_type == "upscale":
            self.router.handle_upscale(job, self.worker_id)
        elif job_type == "facefix":
            self.router.handle_facefix(job, self.worker_id)
        elif job_type == "img2vid":
            self.router.handle_img2vid(job, self.worker_id)

    # ---------------------------------------------------------
    # MAIN LOOP
    # ---------------------------------------------------------
    def main_loop(self):
        if self.is_gpu_idle(self.config.gpu_id_setting) and self.gpu_idle_timer > 0:
            self.gpu_idle_timer -= self.config.polling_interval
            print(f"[GPU] Time remaining until idle: {self.gpu_idle_timer} seconds")
        elif self.gpu_idle_timer <= 0:
            self.get_job()

        self.worker_main_loop = Timer(self.config.polling_interval, self.main_loop)
        self.worker_main_loop.daemon = True
        self.worker_main_loop.start()

    # ---------------------------------------------------------
    # RUN
    # ---------------------------------------------------------
    def run(self):
        self.worker_login()
        self.main_loop()

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("[MAIN] Shutting down worker...")
            if self.worker_main_loop:
                self.worker_main_loop.cancel()
            self.uploader.stop_progress_thread()


# =========================
# ENTRYPOINT
# =========================

def start_tk_ui():
    config = WorkerConfig()
    app_factory = lambda cfg: WorkerApp(cfg)
    config.show_tk_ui_and_run(app_factory)

if __name__ == "__main__":
    # 1. Start Playwright resolver loop IN THE MAIN THREAD
    Thread(target=playwright_resolver_loop, daemon=True).start()

    # 2. Start Tkinter UI in a SEPARATE thread
    Thread(target=start_tk_ui, daemon=False).start()

    # 3. Keep main thread alive forever
    while True:
        time.sleep(1)