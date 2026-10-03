# Auto Clip

Surveille les plus gros streams Twitch (et Kick), détecte les moments forts dans le chat,
monte les clips en 9:16 avec Claude et HyperFrames, puis les publie après validation.

Il n'y a pas de templates : Claude décide du montage, puis écrit lui-même toute la vidéo
(mise en page, sous-titres, animations) sous forme de composition HyperFrames.

## Pipeline

Chaque moment est une ligne de la table `moments` (SQLite) qui avance de statut en statut :

```
detected -> clipped -> downloaded -> analyzed -> decided -> rendered
         -> pending_review -> approved -> published
                           -> rejected
(3 échecs sur une étape -> failed)
```

| Étape | Module | État |
|---|---|---|
| Top streams + chat Twitch | `monitor/twitch.py` | fait |
| Détection de pics | `monitor/detector.py` | fait, testé |
| Récupération / création du clip | `monitor/twitch.py`, `pipeline.py` | fait |
| Téléchargement | `fetch/downloader.py` | fait (yt-dlp) |
| Transcription + frames | `analysis/` | fait (faster-whisper, FFmpeg) |
| Décisions de montage | `editor/claude.py`, `editor/prompt.md` | fait |
| Composition écrite par Claude | `editor/claude.py`, `editor/compose_prompt.md` | fait |
| Lint + rendu | `render/hyperframes.py` | fait |
| Validation Telegram | `review/telegram.py` | fait (boutons Publier / Rejeter) |
| Publication YouTube / TikTok | `publish/` | à faire |
| Kick | `monitor/kick.py` | à faire |

## Installation

Python 3.11 à 3.13 recommandé : faster-whisper n'a pas forcément de wheels pour les versions
plus récentes. Il faut aussi `ffmpeg`, `ffprobe` et `yt-dlp` dans le PATH, et Node.js 22+ pour HyperFrames.

```bash
uv venv --python 3.12
uv pip install -e ".[transcribe,dev]"
cp .env.example .env   # puis remplir les clés
npm install             # CLI HyperFrames (version fixée dans package.json)
```

## Application AutoClip.exe

Double-clique sur `AutoClip.exe` à la racine du projet :

- **Tableau de bord** : moments du jour, vidéos en attente, coût Claude du jour, espace disque ;
  double-clic sur une ligne pour ouvrir la vidéo.
- **Paramètres** : tous les réglages (clés API, détection, Claude, Telegram, rendu), enregistrés dans `.env`.
- **Prompts** : les consignes données à Claude, modifiables sans toucher au code.
- **Journal** : les logs en direct, avec un filtre « erreurs seulement ».
- **Démarrer / Arrêter / Redémarrer** le pipeline en bas à gauche.

L'exe doit rester dans le dossier du projet : il utilise le Python du `.venv`. Pour le reconstruire
après une modification de l'interface :

```bash
uv pip install -e ".[gui]"
.venv\Scripts\python scripts\build_exe.py
```

## Lancer

```bash
python -m autoclip      # tout le pipeline
pytest                  # tests
```

Pour régler les seuils, laisse tourner la détection quelques jours, puis compare les moments
détectés (table `moments`) avec ce que tu juges être de vrais moments forts.
Ajuste ensuite `SPIKE_RATIO` et les paramètres de `SpikeDetector`.

## Points d'attention

- **Disque** : les dossiers des moments rejetés (par Claude ou sur Telegram) ou abandonnés sont supprimés automatiquement ; après validation, seul `final.mp4` est gardé.
- **Coût Claude** : chaque appel est loggé avec son coût estimé (`Claude décision : ... ≈ $0.0xx`).

- **Quota YouTube** : environ 6 uploads par jour par défaut ; vidéos privées tant que l'app n'est pas auditée.
- **TikTok** : publication en privé (`SELF_ONLY`) tant que l'app n'est pas auditée.
- **Droits** : travaille avec l'accord des streamers et apporte une vraie valeur éditoriale.
- **RAM** : `MAX_PARALLEL_RENDERS=1` par défaut, car Chrome headless consomme beaucoup.
