"""AutoClip : application de bureau pour régler et piloter le pipeline.

- Tableau de bord : compteurs, coût Claude du jour, derniers moments (double-clic = vidéo).
- Paramètres : tous les réglages du .env, groupés, validés avant enregistrement.
- Prompts : les consignes données à Claude (décision et composition).
- Journal : les logs du pipeline en direct.

Le pipeline tourne dans un processus séparé (python -m autoclip du venv du projet).
"""

import json
import os
import sqlite3
import subprocess
import sys
import tkinter as tk
from datetime import UTC, datetime
from pathlib import Path
from tkinter import messagebox, ttk

import customtkinter as ctk
from pydantic import ValidationError

from .envfile import read_env, write_env

# --- Emplacement du projet --------------------------------------------------------


def find_root() -> Path:
    start = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
    for d in (start, *start.parents):
        if (d / "pyproject.toml").exists() and (d / "src" / "autoclip").is_dir():
            return d
    return start


ROOT = find_root()
os.chdir(ROOT)  # chemins relatifs (data/, .env) et lecture du .env par Settings

from ..config import Settings
from ..procutil import LOG_FILE, running_pid

ENV_FILE = ROOT / ".env"
PROMPTS = {
    "Décision (garder / couper)": ROOT / "src/autoclip/editor/prompt.md",
    "Composition (montage vidéo)": ROOT / "src/autoclip/editor/compose_prompt.md",
}
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# --- Apparence ----------------------------------------------------------------------

ACCENT = "#7C5CFF"
ACCENT_HOVER = "#6A4BEB"
BG = "#0F1117"
PANEL = "#171A23"
CARD = "#1E2230"
BORDER = "#2A2F40"
TEXT = "#E8EAF2"
MUTED = "#8A90A6"
GREEN = "#3DDC97"
RED = "#FF5C7A"
AMBER = "#FFB547"

STATUS_LABELS = {
    "detected": ("Détecté", MUTED),
    "clipped": ("Clip trouvé", MUTED),
    "downloaded": ("Téléchargé", MUTED),
    "analyzed": ("Analysé", MUTED),
    "decided": ("Décidé", AMBER),
    "rendered": ("Rendu", AMBER),
    "pending_review": ("Sur Telegram", AMBER),
    "approved": ("Approuvé", GREEN),
    "published": ("Publié", GREEN),
    "rejected": ("Rejeté", RED),
    "failed": ("Échec", RED),
}

# --- Description des paramètres ---------------------------------------------------
# (champ, libellé, aide, type, options). Types : text, secret, int, float, bool, choice, combo.

EFFORTS = ["low", "medium", "high", "xhigh", "max"]
MODELS = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-4-5", "claude-fable-5-1"]
LANGS = ["en", "fr", "es", "de", "pt", "it", "ja", "ko"]

SECTIONS = [
    ("Twitch", "🎮", [
        ("twitch_client_id", "Client ID", "dev.twitch.tv/console > ton application", "text", None),
        ("twitch_client_secret", "Client secret", "Secret de l'application Twitch", "secret", None),
        ("twitch_user_token", "Token utilisateur", "Scope clips:edit, seulement pour créer les clips soi-même", "secret", None),
        ("auto_create_clips", "Créer les clips automatiquement", "Sinon, on attend qu'un viewer clippe le moment", "bool", None),
        ("top_n_streams", "Streams surveillés", "Nombre de plus gros streams suivis en même temps", "int", None),
        ("stream_language", "Langue des streams", "Code ISO (en, fr…). Vide = toutes les langues", "combo", ["", *LANGS]),
        ("poll_interval_s", "Rafraîchissement du top (s)", "Fréquence de mise à jour de la liste des streams", "int", None),
    ]),
    ("Détection des moments forts", "⚡", [
        ("spike_ratio", "Seuil de pic (x moyenne)", "Plus haut = moins de moments, mais plus forts", "float", None),
        ("detector_min_activity", "Activité minimale", "Ignore les pics sur les chats presque vides", "float", None),
        ("detector_warmup", "Observation initiale (fenêtres de 10 s)", "30 = 5 minutes avant le premier pic possible", "int", None),
        ("detector_alpha", "Vitesse d'adaptation", "Petit = la moyenne de référence évolue lentement", "float", None),
        ("dedup_window_s", "Délai entre deux moments (s)", "Pas deux moments du même streamer dans ce délai", "int", None),
        ("clip_poll_s", "Recherche du clip toutes les (s)", "Intervalle de recherche d'un clip créé par les viewers", "int", None),
        ("clip_max_wait_s", "Attente maximale du clip (s)", "Au-delà, le moment est abandonné", "int", None),
        ("max_clip_searches", "Recherches simultanées", "Moments dont on attend le clip en même temps", "int", None),
    ]),
    ("Claude", "🤖", [
        ("claude_backend", "Source Claude", "api = facturé à l'usage · subscription = ton abonnement via Claude Code", "choice", ["api", "subscription"]),
        ("anthropic_api_key", "Clé API", "Seulement en mode api (platform.claude.com > API keys)", "secret", None),
        ("anthropic_workspace_id", "Workspace ID", "wrkspc_… (si la clé n'est rattachée à aucun workspace)", "text", None),
        ("claude_model", "Modèle (composition)", "Écrit la vidéo : le plus créatif est le meilleur choix", "combo", MODELS),
        ("decide_model", "Modèle (décision)", "Garder / couper. Vide = même modèle. Sonnet = 2x moins cher", "combo", ["", *MODELS]),
        ("decide_effort", "Effort de la décision", "Plus haut = meilleur jugement, plus cher", "choice", EFFORTS),
        ("compose_effort", "Effort de la composition", "Plus haut = montages plus travaillés, plus cher", "choice", EFFORTS),
        ("clip_language", "Langue du public", "Accroche, titre, hashtags et textes à l'écran", "combo", LANGS),
        ("min_transcript_words", "Mots minimum", "En dessous, le clip est rejeté sans appeler Claude", "int", None),
        ("max_lint_fixes", "Corrections maximum", "Essais de correction si la composition est invalide", "int", None),
    ]),
    ("Telegram", "✈️", [
        ("telegram_bot_token", "Token du bot", "Donné par @BotFather", "secret", None),
        ("telegram_chat_id", "Chat ID", "Conversation où arrivent les vidéos à valider", "text", None),
    ]),
    ("Transcription et rendu", "🎬", [
        ("whisper_model", "Modèle Whisper", "Plus gros = plus précis mais plus lent", "choice", ["tiny", "base", "small", "medium", "large-v3"]),
        ("render_quality", "Qualité du rendu", "draft = rapide, delivery = meilleure qualité", "choice", ["draft", "looks", "delivery"]),
        ("max_parallel_renders", "Rendus simultanés", "Chaque rendu lance un Chrome : 1 ou 2 maximum", "int", None),
    ]),
    ("Kick (bientôt)", "🟩", [
        ("kick_client_id", "Client ID", "Pas encore utilisé par le pipeline", "text", None),
        ("kick_client_secret", "Client secret", "Pas encore utilisé par le pipeline", "secret", None),
    ]),
]


def default_of(field: str) -> str:
    value = Settings.model_fields[field].default
    if isinstance(value, bool):
        return "true" if value else "false"
    return "" if value is None else str(value)


# --- Composants -------------------------------------------------------------------


def font(size=13, weight="normal"):
    return ctk.CTkFont(family="Segoe UI", size=size, weight=weight)


class StatCard(ctk.CTkFrame):
    def __init__(self, master, title: str, color: str = TEXT):
        super().__init__(master, fg_color=CARD, corner_radius=14, border_width=1, border_color=BORDER)
        ctk.CTkLabel(self, text=title, font=font(12), text_color=MUTED).pack(anchor="w", padx=16, pady=(14, 0))
        self.value = ctk.CTkLabel(self, text="–", font=font(28, "bold"), text_color=color)
        self.value.pack(anchor="w", padx=16, pady=(0, 14))

    def set(self, text: str):
        self.value.configure(text=text)


class Toast(ctk.CTkLabel):
    def __init__(self, master):
        super().__init__(master, text="", corner_radius=10, fg_color=CARD, text_color=TEXT,
                         font=font(13), height=38)
        self._job = None

    def show(self, text: str, color: str = GREEN, ms: int = 4000):
        self.configure(text=f"  {text}  ", text_color=color)
        self.place(relx=0.5, rely=0.97, anchor="s")
        self.lift()
        if self._job:
            self.after_cancel(self._job)
        self._job = self.after(ms, self.place_forget)


# --- Pages ------------------------------------------------------------------------


class DashboardPage(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        header(self, "Tableau de bord", "Ce que fait le pipeline aujourd'hui")

        grid = ctk.CTkFrame(self, fg_color="transparent")
        grid.pack(fill="x", padx=28, pady=(4, 12))
        self.cards = {
            "detected": StatCard(grid, "Moments détectés (aujourd'hui)"),
            "rendered": StatCard(grid, "Vidéos rendues (aujourd'hui)", AMBER),
            "review": StatCard(grid, "En attente sur Telegram", AMBER),
            "approved": StatCard(grid, "Approuvées (total)", GREEN),
            "cost": StatCard(grid, "Coût Claude (aujourd'hui)", ACCENT),
            "disk": StatCard(grid, "Espace disque utilisé"),
        }
        for i, card in enumerate(self.cards.values()):
            card.grid(row=i // 3, column=i % 3, sticky="nsew", padx=6, pady=6)
        for c in range(3):
            grid.grid_columnconfigure(c, weight=1)

        box = ctk.CTkFrame(self, fg_color=CARD, corner_radius=14, border_width=1, border_color=BORDER)
        box.pack(fill="both", expand=True, padx=34, pady=(4, 24))
        top = ctk.CTkFrame(box, fg_color="transparent")
        top.pack(fill="x", padx=16, pady=(12, 6))
        ctk.CTkLabel(top, text="Derniers moments", font=font(15, "bold")).pack(side="left")
        ctk.CTkLabel(top, text="Double-clic : ouvrir la vidéo ou le dossier", font=font(12),
                     text_color=MUTED).pack(side="right")

        style = ttk.Style()
        style.theme_use("default")
        style.configure("AC.Treeview", background=CARD, fieldbackground=CARD, foreground=TEXT,
                        rowheight=30, borderwidth=0, font=("Segoe UI", 11))
        style.configure("AC.Treeview.Heading", background=PANEL, foreground=MUTED,
                        relief="flat", font=("Segoe UI", 10, "bold"))
        style.map("AC.Treeview", background=[("selected", ACCENT)])
        style.layout("AC.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])

        cols = ("id", "time", "channel", "score", "status", "title")
        self.tree = ttk.Treeview(box, columns=cols, show="headings", style="AC.Treeview")
        for col, label, width, anchor in [
            ("id", "#", 60, "center"), ("time", "Heure", 110, "center"),
            ("channel", "Streamer", 150, "w"), ("score", "Pic", 70, "center"),
            ("status", "Statut", 130, "w"), ("title", "Titre / raison", 520, "w"),
        ]:
            self.tree.heading(col, text=label)
            self.tree.column(col, width=width, anchor=anchor, stretch=(col == "title"))
        for status, (_, color) in STATUS_LABELS.items():
            self.tree.tag_configure(status, foreground=color)
        self.tree.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.tree.bind("<Double-1>", self.open_selected)

    def refresh(self):
        db = ROOT / "data/autoclip.db"
        midnight = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        since = midnight.astimezone(UTC).isoformat()
        rows, counts, approved, review = [], {}, 0, 0
        if db.exists():
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            try:
                counts = dict(con.execute(
                    "SELECT status, count(*) FROM moments WHERE detected_at >= ? GROUP BY status",
                    (since,)).fetchall())
                approved = con.execute(
                    "SELECT count(*) FROM moments WHERE status IN ('approved','published')").fetchone()[0]
                review = con.execute(
                    "SELECT count(*) FROM moments WHERE status = 'pending_review'").fetchone()[0]
                rows = con.execute(
                    "SELECT id, detected_at, channel, score, status, decision_json, error "
                    "FROM moments ORDER BY id DESC LIMIT 60").fetchall()
            except sqlite3.Error:
                pass
            finally:
                con.close()

        done = ("rendered", "pending_review", "approved", "published")
        self.cards["detected"].set(str(sum(counts.values())))
        self.cards["rendered"].set(str(sum(counts.get(s, 0) for s in done)))
        self.cards["review"].set(str(review))
        self.cards["approved"].set(str(approved))
        self.cards["cost"].set(f"${claude_cost_today():.2f}")
        self.cards["disk"].set(human_size(dir_size(ROOT / "data/work")))

        self.tree.delete(*self.tree.get_children())
        for id_, detected, channel, score, status, decision, error in rows:
            title = ""
            if decision:
                try:
                    title = json.loads(decision).get("title", "")
                except ValueError:
                    pass
            if status in ("rejected", "failed") and error:
                title = error
            local = datetime.fromisoformat(detected).astimezone().strftime("%d/%m %H:%M")
            label = STATUS_LABELS.get(status, (status, TEXT))[0]
            self.tree.insert("", "end", iid=str(id_), tags=(status,),
                             values=(id_, local, channel, f"x{score:.1f}", label, title[:140]))

    def open_selected(self, _event):
        sel = self.tree.selection()
        if not sel:
            return
        dirs = list((ROOT / "data/work").glob(f"{int(sel[0]):06d}_*"))
        if not dirs:
            self.app.toast.show("Fichiers supprimés (moment rejeté ou abandonné)", MUTED)
            return
        video = dirs[0] / "final.mp4"
        os.startfile(video if video.exists() else dirs[0])


class SettingsPage(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        bar = header(self, "Paramètres", "Enregistrés dans le fichier .env du projet")
        ctk.CTkButton(bar, text="Enregistrer", width=140, height=36, font=font(13, "bold"),
                      fg_color=ACCENT, hover_color=ACCENT_HOVER, command=self.save).pack(side="right")
        ctk.CTkButton(bar, text="Annuler les modifications", width=190, height=36, font=font(13),
                      fg_color=CARD, hover_color=BORDER, command=self.load).pack(side="right", padx=8)

        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=20, pady=(0, 16))
        self.vars: dict[str, tk.Variable] = {}

        for title, icon, fields in SECTIONS:
            card = ctk.CTkFrame(scroll, fg_color=CARD, corner_radius=14, border_width=1, border_color=BORDER)
            card.pack(fill="x", padx=8, pady=8)
            ctk.CTkLabel(card, text=f"{icon}  {title}", font=font(16, "bold")).pack(anchor="w", padx=20, pady=(16, 6))
            for field, label, help_, kind, options in fields:
                self._row(card, field, label, help_, kind, options)
            ctk.CTkFrame(card, height=10, fg_color="transparent").pack()

    def _row(self, card, field, label, help_, kind, options):
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=20, pady=5)
        row.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(row, text=label, font=font(13, "bold"), anchor="w").grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(row, text=help_, font=font(11), text_color=MUTED, anchor="w").grid(row=1, column=0, sticky="w")

        if kind == "bool":
            var = tk.BooleanVar()
            ctk.CTkSwitch(row, text="", variable=var, progress_color=ACCENT).grid(row=0, column=1, rowspan=2, sticky="e")
        else:
            var = tk.StringVar()
            common = {"width": 320, "height": 34, "font": font(13)}
            if kind == "choice":
                w = ctk.CTkOptionMenu(row, values=options, variable=var, fg_color=PANEL, button_color=BORDER,
                                      button_hover_color=ACCENT, **common)
            elif kind == "combo":
                w = ctk.CTkComboBox(row, values=options, variable=var, fg_color=PANEL, border_color=BORDER,
                                    button_color=BORDER, button_hover_color=ACCENT, **common)
            else:
                w = ctk.CTkEntry(row, textvariable=var, fg_color=PANEL, border_color=BORDER,
                                 show="•" if kind == "secret" else "", **common)
            w.grid(row=0, column=1, rowspan=2, sticky="e")
            if kind == "secret":
                eye = ctk.CTkButton(row, text="👁", width=34, height=34, fg_color=PANEL, hover_color=BORDER)
                eye.configure(command=lambda e=w: e.configure(show="" if e.cget("show") else "•"))
                eye.grid(row=0, column=2, rowspan=2, padx=(6, 0))
        if kind != "secret":
            # Même largeur que le bouton 👁 : tous les champs restent alignés.
            ctk.CTkFrame(row, width=40, height=1, fg_color="transparent").grid(row=0, column=2)
        self.vars[field] = var

    def load(self):
        env = read_env(ENV_FILE)
        for field, var in self.vars.items():
            raw = env.get(field.upper(), default_of(field))
            if isinstance(var, tk.BooleanVar):
                var.set(raw.strip().lower() in ("1", "true", "yes", "on"))
            else:
                var.set(raw)

    def save(self):
        env = read_env(ENV_FILE)
        values = {}
        for field, var in self.vars.items():
            value = var.get()
            values[field] = ("true" if value else "false") if isinstance(value, bool) else str(value).strip()
        try:
            Settings(_env_file=None, **values)  # validation (nombres, booléens…)
        except ValidationError as e:
            err = e.errors()[0]
            messagebox.showerror("Valeur invalide", f"{err['loc'][0]} : {err['msg']}")
            return
        # On n'écrit que ce qui diffère des valeurs par défaut ou existe déjà dans le .env.
        updates = {f.upper(): v for f, v in values.items()
                   if f.upper() in env or v != default_of(f)}
        write_env(ENV_FILE, updates)
        self.app.settings_saved()


class PromptsPage(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        bar = header(self, "Prompts", "Les consignes données à Claude, en texte libre")
        ctk.CTkButton(bar, text="Enregistrer", width=140, height=36, font=font(13, "bold"),
                      fg_color=ACCENT, hover_color=ACCENT_HOVER, command=self.save).pack(side="right")
        ctk.CTkButton(bar, text="Recharger", width=120, height=36, font=font(13),
                      fg_color=CARD, hover_color=BORDER, command=self.load).pack(side="right", padx=8)

        tabs = ctk.CTkTabview(self, fg_color=CARD, segmented_button_selected_color=ACCENT,
                              segmented_button_selected_hover_color=ACCENT_HOVER, corner_radius=14)
        tabs.pack(fill="both", expand=True, padx=28, pady=(0, 24))
        self.boxes = {}
        for name in PROMPTS:
            tab = tabs.add(name)
            box = ctk.CTkTextbox(tab, font=ctk.CTkFont(family="Consolas", size=13), fg_color=PANEL,
                                 wrap="word", corner_radius=10)
            box.pack(fill="both", expand=True, padx=6, pady=6)
            self.boxes[name] = box

    def load(self):
        for name, path in PROMPTS.items():
            box = self.boxes[name]
            box.delete("1.0", "end")
            box.insert("1.0", path.read_text(encoding="utf-8") if path.exists() else "")

    def save(self):
        for name, path in PROMPTS.items():
            path.write_text(self.boxes[name].get("1.0", "end").rstrip() + "\n", encoding="utf-8")
        self.app.settings_saved("Prompts enregistrés")


class LogsPage(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        bar = header(self, "Journal", "Logs du pipeline en direct (data/autoclip.log)")
        self.follow = tk.BooleanVar(value=True)
        ctk.CTkSwitch(bar, text="Suivre", variable=self.follow, progress_color=ACCENT,
                      font=font(13)).pack(side="right")
        self.errors_only = tk.BooleanVar(value=False)
        ctk.CTkSwitch(bar, text="Erreurs seulement", variable=self.errors_only, progress_color=RED,
                      font=font(13), command=lambda: self.refresh(force=True)).pack(side="right", padx=16)

        self.box = ctk.CTkTextbox(self, font=ctk.CTkFont(family="Consolas", size=12), fg_color=CARD,
                                  wrap="none", corner_radius=14, border_width=1, border_color=BORDER)
        self.box.pack(fill="both", expand=True, padx=28, pady=(0, 24))
        self.box.tag_config("error", foreground=RED)
        self.box.tag_config("cost", foreground=ACCENT)
        self.box.tag_config("good", foreground=GREEN)
        self._last = None

    def refresh(self, force=False):
        text = tail(ROOT / LOG_FILE, 120_000)
        key = (len(text), text[-200:], self.errors_only.get())
        if key == self._last and not force:
            return
        self._last = key
        lines = text.splitlines()
        if self.errors_only.get():
            lines = [ln for ln in lines if " ERROR " in ln or "Traceback" in ln or ln.startswith(("  ", "anthropic.", "httpx", "RuntimeError"))]
        self.box.configure(state="normal")
        self.box.delete("1.0", "end")
        for ln in lines[-1500:]:
            tag = ("error" if " ERROR " in ln or ln.startswith(("Traceback", "RuntimeError"))
                   else "cost" if "Claude " in ln and "$" in ln
                   else "good" if "Telegram pour validation" in ln or "approuvé" in ln else None)
            self.box.insert("end", ln + "\n", tag)
        self.box.configure(state="disabled")
        if self.follow.get():
            self.box.see("end")


# --- Utilitaires --------------------------------------------------------------------


def header(page, title: str, subtitle: str):
    bar = ctk.CTkFrame(page, fg_color="transparent")
    bar.pack(fill="x", padx=34, pady=(26, 14))
    left = ctk.CTkFrame(bar, fg_color="transparent")
    left.pack(side="left")
    ctk.CTkLabel(left, text=title, font=font(24, "bold")).pack(anchor="w")
    ctk.CTkLabel(left, text=subtitle, font=font(13), text_color=MUTED).pack(anchor="w")
    return bar


def tail(path: Path, max_bytes: int) -> str:
    if not path.exists():
        return "Aucun log pour l'instant : démarre le pipeline."
    with path.open("rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - max_bytes))
        data = f.read()
    text = data.decode("utf-8", errors="replace")
    return text.split("\n", 1)[1] if size > max_bytes and "\n" in text else text


def claude_cost_today() -> float:
    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    total = 0.0
    for p in sorted((ROOT / "data").glob("autoclip.log*")):
        try:
            for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
                if ln.startswith(today) and "≈ $" in ln:
                    try:
                        total += float(ln.rsplit("≈ $", 1)[1].split()[0])
                    except ValueError:
                        pass
        except OSError:
            pass
    return total


def dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def human_size(n: int) -> str:
    for unit in ("o", "Ko", "Mo", "Go"):
        if n < 1024 or unit == "Go":
            return f"{n:.0f} {unit}" if unit in ("o", "Ko") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} o"


# --- Fenêtre principale ---------------------------------------------------------------


class App(ctk.CTk):
    def __init__(self):
        super().__init__(fg_color=BG)
        ctk.set_appearance_mode("dark")
        self.title("AutoClip")
        self.geometry("1240x800")
        self.minsize(1040, 680)
        # Icône : embarquée dans l'exe (sys._MEIPASS) ou à côté du module en développement.
        icon = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2])) / "autoclip/gui/autoclip.ico"
        if icon.exists():
            if sys.platform == "win32":
                # Sinon Windows regroupe la fenêtre sous l'icône de Python dans la barre des tâches.
                import ctypes

                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("AutoClip.Control")
            self.after(250, lambda: self.iconbitmap(str(icon)))  # après CTk, qui pose la sienne

        side = ctk.CTkFrame(self, width=230, fg_color=PANEL, corner_radius=0)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        ctk.CTkLabel(side, text="✂ AutoClip", font=font(22, "bold"), text_color=TEXT).pack(anchor="w", padx=24, pady=(28, 2))
        ctk.CTkLabel(side, text="Clips Twitch → Shorts", font=font(12), text_color=MUTED).pack(anchor="w", padx=24, pady=(0, 24))

        content = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        content.pack(side="left", fill="both", expand=True)
        self.pages = {
            "dashboard": DashboardPage(content, self),
            "settings": SettingsPage(content, self),
            "prompts": PromptsPage(content, self),
            "logs": LogsPage(content, self),
        }
        self.nav = {}
        for key, label in [("dashboard", "📊  Tableau de bord"), ("settings", "⚙️  Paramètres"),
                           ("prompts", "✍️  Prompts"), ("logs", "📜  Journal")]:
            b = ctk.CTkButton(side, text=label, anchor="w", height=42, font=font(14), corner_radius=10,
                              fg_color="transparent", hover_color=CARD, text_color=TEXT,
                              command=lambda k=key: self.show(k))
            b.pack(fill="x", padx=14, pady=3)
            self.nav[key] = b

        # Contrôle du pipeline, en bas de la barre latérale.
        ctrl = ctk.CTkFrame(side, fg_color=CARD, corner_radius=14, border_width=1, border_color=BORDER)
        ctrl.pack(side="bottom", fill="x", padx=14, pady=18)
        ctk.CTkLabel(ctrl, text="PIPELINE", font=font(11, "bold"), text_color=MUTED).pack(anchor="w", padx=14, pady=(12, 0))
        self.status_label = ctk.CTkLabel(ctrl, text="● Arrêté", font=font(14, "bold"), text_color=MUTED)
        self.status_label.pack(anchor="w", padx=14)
        self.toggle_btn = ctk.CTkButton(ctrl, text="▶  Démarrer", height=38, font=font(13, "bold"),
                                        fg_color=ACCENT, hover_color=ACCENT_HOVER, command=self.toggle)
        self.toggle_btn.pack(fill="x", padx=12, pady=(8, 6))
        self.restart_btn = ctk.CTkButton(ctrl, text="⟳  Redémarrer", height=32, font=font(12),
                                         fg_color=PANEL, hover_color=BORDER, command=self.restart)
        self.restart_btn.pack(fill="x", padx=12, pady=(0, 12))

        self.toast = Toast(self)
        self.pages["settings"].load()
        self.pages["prompts"].load()
        self.current = None
        self.show("dashboard")
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.tick()

    # Navigation
    def show(self, key: str):
        if self.current:
            self.pages[self.current].pack_forget()
            self.nav[self.current].configure(fg_color="transparent")
        self.pages[key].pack(fill="both", expand=True)
        self.nav[key].configure(fg_color=CARD)
        self.current = key
        self.refresh_page()

    def refresh_page(self):
        page = self.pages[self.current]
        if hasattr(page, "refresh"):
            page.refresh()

    def tick(self):
        """Toutes les 2 s : état du pipeline et page affichée (sauf formulaires)."""
        pid = running_pid(ROOT)
        if pid:
            self.status_label.configure(text=f"● En marche  (PID {pid})", text_color=GREEN)
            self.toggle_btn.configure(text="■  Arrêter", fg_color=RED, hover_color="#E04866")
        else:
            self.status_label.configure(text="● Arrêté", text_color=MUTED)
            self.toggle_btn.configure(text="▶  Démarrer", fg_color=ACCENT, hover_color=ACCENT_HOVER)
        self.restart_btn.configure(state="normal" if pid else "disabled")
        if self.current in ("dashboard", "logs"):
            self.refresh_page()
        self.after(2000, self.tick)

    # Pipeline
    def start(self) -> bool:
        python = ROOT / ".venv/Scripts/python.exe"
        if not python.exists():
            messagebox.showerror("Python introuvable", f"Environnement virtuel absent :\n{python}")
            return False
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
        subprocess.Popen([str(python), "-m", "autoclip"], cwd=ROOT, env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=NO_WINDOW)
        self.toast.show("Pipeline démarré : suis-le dans le Journal")
        return True

    def stop(self) -> None:
        pid = running_pid(ROOT)
        if pid:
            # /T : arrête aussi les rendus en cours (Node, Chrome).
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, creationflags=NO_WINDOW, check=False)
            self.toast.show("Pipeline arrêté", AMBER)

    def toggle(self):
        if running_pid(ROOT):
            if messagebox.askyesno("Arrêter le pipeline",
                                   "Arrêter le pipeline ? Un rendu en cours sera interrompu."):
                self.stop()
        else:
            self.start()
        self.after(800, self.tick_once)

    def restart(self):
        self.stop()
        self.after(1500, self.start)
        self.after(2500, self.tick_once)

    def tick_once(self):
        pid = running_pid(ROOT)
        self.status_label.configure(text=f"● En marche  (PID {pid})" if pid else "● Arrêté",
                                    text_color=GREEN if pid else MUTED)

    def settings_saved(self, message: str = "Paramètres enregistrés"):
        if running_pid(ROOT):
            if messagebox.askyesno("Redémarrer ?", f"{message}.\n\nRedémarrer le pipeline maintenant "
                                   "pour les appliquer ?"):
                self.restart()
                return
            self.toast.show(f"{message} : appliqués au prochain démarrage", AMBER)
        else:
            self.toast.show(message)

    def on_close(self):
        if running_pid(ROOT) and messagebox.askyesno(
                "Quitter", "Le pipeline tourne. L'arrêter aussi ?\n(Non = il continue en arrière-plan)"):
            self.stop()
        self.destroy()


def main():
    App().mainloop()


if __name__ == "__main__":
    main()
