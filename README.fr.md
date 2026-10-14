# Memory Desk

[Русский](README.md) · [English](README.en.md) · **Français**

Un bureau de réunions IA entièrement local pour macOS. Il reçoit l’audio d’un PLAUD ou de tout autre enregistreur, le transcrit, en extrait décisions, tâches, risques et questions ouvertes, range les notes dans Obsidian et répond aux questions sur toute votre archive d’enregistrements. **L’audio, les transcriptions et les notes ne quittent jamais votre ordinateur** : transcription, analyse et recherche tournent sur des modèles locaux.

![L’écran « Aujourd’hui »](docs/img/today.png)

> Toutes les images de ce dépôt ont été réalisées avec les données fictives de `tests/make_fixture.py`. L’application a été écrite pour des réunions en russe : les notes et transcriptions restent en russe, seule l’interface est traduite (russe, anglais, français, allemand, espagnol — traductions automatiques à faire relire par un locuteur natif).

![Du fichier à la réponse : file, enregistrement, compte rendu, chat](docs/img/workflow.gif)

*Le parcours « je dépose un fichier, j’obtiens le compte rendu » à travers les écrans (diapositives sur données fictives, pas l’enregistrement d’un vrai traitement ; les légendes de l’animation sont en russe).*

## Pourquoi ce projet

J’ai acheté un enregistreur PLAUD — un excellent appareil, mais je voulais faire le mien : **en local, sur un MacBook, sans abonnement ni service cloud**. L’idée est simple : vous déposez un fichier dans un dossier et vous obtenez à peu près la même chose — transcription, résumé, décisions, tâches, risques et questions — gratuitement et sans que les enregistrements quittent votre ordinateur. Memory Desk est un projet indépendant, sans lien avec PLAUD ; il fonctionne avec les fichiers audio de n’importe quel enregistreur.

## Ce que cela apporte

- **De l’enregistrement à la note terminée, sans travail manuel.** Déposez un fichier dans un dossier et obtenez une transcription horodatée, un titre, un résumé, des décisions, des tâches (avec responsable et échéance), des risques et des questions. La structure de la note vient d’un modèle : « Réunion avec décisions », « Point d’équipe », « Entretien individuel », « Interview », « Cours », et des modèles ciblés (« Décisions seulement », « Tâches par personne », « Questions au client », « Risques », « Cinq lignes pour la direction ») ; vous pouvez aussi créer le vôtre à partir d’une description.
- **Un seul bureau au lieu d’un tas de notes.** La page d’une personne ou d’un projet rassemble engagements, décisions, questions et évolution ; les fiches de personnes peuvent être créées à la main (elles vivent dans Obsidian). Un point du matin (« ce qui est prévu aujourd’hui, ce qui est en retard, ce qu’on vous a promis »), un registre des tâches et décisions avec brouillons à confirmer, les tâches bloquées, les séries de réunions récurrentes, et un avertissement quand une nouvelle décision contredit une ancienne.
- **Pièces jointes.** Photos de tableau, de diapositive ou de feuille et fichiers texte se joignent à un enregistrement ; le texte des photos est reconnu sur votre ordinateur (Vision de macOS), modifiable, et retrouvé par la recherche et le chat.
- **Contrôle et confidentialité.** Un journal d’activité (ce qui a changé et quand, sans le contenu des enregistrements, avec contrôle d’intégrité), « effacer entièrement un enregistrement » avec liste et confirmation, un verrouillage par PIN avec écran sans données. Le PIN ne chiffre pas les fichiers : c’est le rôle de FileVault.
- **Ligne de commande et MCP.** `./memory-desk ask | search | list --since 7d | ingest | status` et un serveur MCP pour Claude Desktop : recherche avec sources, enregistrements, tâches, dossiers. Accès uniquement par un jeton créé dans les Réglages (lecture seule par défaut, l’écriture s’active séparément) ; chaque appel figure dans le journal.
- **Suggestions après la réunion.** Les accords repérés dans l’enregistrement (« on s’appelle jeudi », « envoie le compte rendu », « rappelle-moi avant vendredi ») s’affichent en cartes ; un événement du Calendrier, un rappel ou un brouillon de courriel n’est créé que si vous appuyez sur le bouton.
- **Langues de l’interface.** Russe, anglais, français, allemand, espagnol (traductions automatiques, relecture nécessaire). Notes et transcriptions restent en russe.
- **Qualité de la reconnaissance.** Choix du modèle Whisper, indice composé du dictionnaire, des noms de personnes et de projets, préparation du son ; les passages douteux de la transcription sont signalés « à vérifier à l’oreille » ; les mots que vous avez corrigés sont proposés pour le dictionnaire ; `scripts/asr_benchmark.py` mesure ce qui convient le mieux à vos propres enregistrements.
- **Signets.** La touche B du lecteur pose un repère coloré avec un commentaire ; les signets sont dans la note elle-même et se retrouvent par la recherche.
- **Questions sur l’archive** en langage courant. La réponse n’est construite qu’à partir de vos enregistrements et cite ses sources avec horodatage ; dessous, des suggestions de questions suivantes et un bouton « Enregistrer dans Obsidian ». Sans preuve, le chat le dit.
- **Documents à la demande.** Compte rendu de réunion, carte mentale, synthèse par projet, note de service, brief et brouillon de courriel sont assemblés à partir des faits extraits et fonctionnent même sans modèle ; chaque point de la note, du brief et du courriel porte l’horodatage de son passage dans l’enregistrement.
- **Export.** Compte rendu et réponses du chat en PDF, Word, HTML et Markdown ; carte en PNG. Le cyrillique dans les PDF ne dépend pas des polices du système.
- **Obsidian reste une archive lisible.** Notes et transcriptions sont de simples fichiers Markdown ; SQLite garde les états de travail, Qdrant la recherche sémantique.
- **Entièrement local.** Aucune API cloud, aucun compte, le serveur n’écoute que sur `127.0.0.1`.

La liste détaillée des fonctions par écran est dans [docs/FEATURES.md](docs/FEATURES.md) (en russe), l’architecture dans [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Fonctionnement

```text
PLAUD / enregistreur ─► dossier d’enregistrements ─► MLX Whisper ─► Ollama (qwen3:4b) ─► note + transcription dans Obsidian
                                                      transcription   décisions, tâches,        │
                                                      horodatée       risques, questions         ▼
                                                                                  SQLite (états, liens)
                                                                                  Qdrant (recherche sémantique)
                                                                                          │
                                                                        FastAPI :8502 ◄───┘───► interface React
```

| Étape | Outil |
| --- | --- |
| Transcription | `mlx-whisper`, modèle `whisper-large-v3-turbo`, filtre des « hallucinations » sur la musique et le bruit |
| Analyse et chat | Ollama, `qwen3:4b` (instance propre sur `127.0.0.1:11435`) |
| Recherche vectorielle | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` + Qdrant Local |
| Serveur | FastAPI + SSE (progression en direct sans recharger la page) |
| Interface | React 18 + TypeScript + Vite, thèmes clair et sombre |
| Stockage | SQLite (WAL), notes Obsidian, Qdrant Local |

## Prérequis

- Un Mac **Apple Silicon** (M1 ou plus récent), macOS 13 ou ultérieur.
- **16 Go** de mémoire recommandés ; avec moins, l’installateur vous prévient et les longs enregistrements peuvent être lents.
- Environ **12 Go** d’espace libre : modèles Whisper, Ollama et d’embeddings, environnement Python.
- Internet uniquement pendant l’installation (paquets et modèles). Ensuite le bureau fonctionne hors ligne.

Les Mac Intel ne sont pas pris en charge : MLX Whisper ne fonctionne que sur Apple Silicon.

## Installation

Il y a deux voies, au même résultat : le cabinet tourne sur votre Mac et vos données ne quittent pas l’ordinateur.

| Voie | Pour qui | Comment |
| --- | --- | --- |
| **Navigateur** | vous voulez maîtriser chaque étape | télécharger le dépôt et lancer le script (ci-dessous) ; le cabinet s’ouvre dans le navigateur |
| **Application Mac** | vous voulez une application ordinaire | compiler ou télécharger `MemoryDesk.app` ([`macos/`](macos/README.md)) ; au premier lancement, l’application propose de s’installer |

Dans les deux cas, l’**assistant de premier lancement** du cabinet vérifie ce qui manque (paquets Python, ffmpeg, Ollama), vous laisse choisir les modèles d’analyse et de reconnaissance vocale et les télécharge **sur votre clic** : avec les tailles, la progression et un bouton d’arrêt. Les dossiers des enregistrements et des notes et l’accès au Calendrier et aux Rappels s’y règlent aussi. Plus tard, l’assistant s’ouvre depuis « Réglages → Données → Vérification du système ». `install.command --skip-models` n’installe que l’environnement et laisse les modèles à l’assistant ; sans cet indicateur, il télécharge tout lui-même.

En une commande (récupère le code dans `~/memory-desk` et lance l’installateur) :

```bash
curl -fsSL https://raw.githubusercontent.com/vgerasin/Memory-Desk/main/scripts/bootstrap.sh | zsh
```

Ou manuellement :

```bash
git clone https://github.com/vgerasin/Memory-Desk.git memory-desk
cd memory-desk
./install.command
```

Si `git` n’est pas encore installé, macOS propose d’installer les Command Line Tools — acceptez et relancez la commande.

Si vous avez téléchargé le projet en ZIP, macOS met les fichiers en quarantaine — levez l’indicateur avant de lancer :

```bash
xattr -dr com.apple.quarantine .
chmod +x *.command meeting-assistant meetings-process
./install.command
```

L’installateur (`install.command`) effectue successivement :

1. la vérification du système : Apple Silicon, version de macOS, espace libre, mémoire ;
2. l’installation via Homebrew de `python@3.12`, `ffmpeg` et `ollama` s’ils manquent ;
3. la création de l’environnement `.venv` et l’installation des paquets de `requirements.txt` ;
4. la création des dossiers de travail (`logs`, `runtime`, `webui/data`, `work`) et la levée de la quarantaine ;
5. le démarrage de sa propre instance d’Ollama et le téléchargement de `qwen3:4b` dans `ollama-models/` à l’intérieur du projet ;
6. le téléchargement anticipé du modèle d’embeddings et du modèle Whisper dans `huggingface-cache/` pour travailler ensuite hors ligne ;
7. l’affichage d’un bilan et l’ouverture du bureau.

Un second lancement est sans danger : les étapes terminées sont ignorées et les données de l’utilisateur ne sont pas touchées.

| Option | Effet |
| --- | --- |
| `--check` | vérifier seulement l’état, ne rien modifier |
| `--skip-models` | ne pas télécharger les modèles (reconfiguration sans réseau) |
| `--no-open` | ne pas ouvrir le bureau à la fin |
| `--help` | aide |

Journal d’installation : `logs/install.log`.

Après l’installation, ouvrez l’engrenage → « Réglages » et choisissez le dossier d’enregistrements, le dossier Obsidian et la durée de conservation de l’audio.

> **L’installateur n’a été testé jusqu’ici que sur des simulations** (voir [« Ce qui a été vérifié »](#ce-qui-a-été-vérifié)) : l’ordre des étapes, l’idempotence, le refus sur Intel et en cas d’espace insuffisant sont couverts par `tests/test_installer.py`, mais il n’a pas encore tourné sur un Mac vierge avec de vrais téléchargements. En cas de problème, envoyez `logs/install.log`.

## Utilisation quotidienne

1. Déposez les fichiers audio dans le dossier d’enregistrements choisi (ou activez le traitement automatique dans les réglages).
2. Lancez `start.command` — le bureau s’ouvre sur `http://127.0.0.1:8502`.
3. Cliquez sur « Traiter les nouveaux » (pour chaque fichier de la file, vous pouvez choisir un modèle de note). La progression (12 étapes, pourcentage, temps restant) s’affiche en direct.
4. Ouvrez l’enregistrement terminé dans « Enregistrements » : attribuez un projet, un type et des participants, confirmez les décisions et tâches trouvées, publiez-le dans Obsidian. Tant que les fichiers attendent d’être traités, ils figurent dans le bloc « Nouveaux et en cours » en haut du même écran ; le modèle de note s’y choisit aussi.
5. Posez des questions sur l’archive : `⌘K`.

MP3, WAV, M4A, AAC, FLAC, OGG et MP4 sont pris en charge. Les doublons sont détectés par SHA-256. En cas d’échec, la transcription et les résultats intermédiaires sont conservés et le traitement reprend à l’étape terminée.

## Données et confidentialité

Toutes les données se trouvent dans le dossier du projet et dans `~/Library/Application Support/MemoryDesk/` :

| Données | Chemin |
| --- | --- |
| Réglages | `~/Library/Application Support/MemoryDesk/config.json` |
| SQLite | `webui/data/meetings.db` (ou `data/` si le dossier n’existe pas) |
| Qdrant | `webui/data/qdrant` |
| Sauvegardes de la base | `webui/data/backups` |
| Journaux | `logs/` |
| Résultats intermédiaires | `work/<nom-de-l-enregistrement>` |
| Modèles | `ollama-models/`, `huggingface-cache/` |

Le dossier d’enregistrements et le coffre Obsidian se choisissent dans les réglages et peuvent se trouver n’importe où.

- Le serveur n’écoute que sur `127.0.0.1`, n’accepte que les requêtes dont l’hôte est `127.0.0.1`/`localhost`, et exige l’en-tête `X-Memory-Desk` pour toute requête modifiant des données : un site quelconque dans votre navigateur ne peut pas piloter le bureau.
- Les empreintes vocales (fonction facultative « Par la voix ») sont des **données biométriques**. Elles ne sont stockées qu’en local, ajoutées par une action de la personne avec confirmation du consentement, et supprimées dans « Personnes ».
- `.gitignore` exclut l’audio, les transcriptions, les bases, l’index de recherche, les modèles, les journaux, les sauvegardes et les réglages locaux. **Ne publiez pas vos données** : le dépôt ne contient que du code, et les données de test sont fictives, créées par un script.

## Développement

```bash
# environnement de développement et de tests
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest httpx pypdf

# serveur (port 8502)
.venv/bin/python -m memory_desk

# interface avec rechargement à chaud (port 5173, relaie /api vers 8502)
cd frontend && npm install && npm run dev

# compilation de l’interface dans memory_desk/web
cd frontend && npm run build

# tests (le modèle d’embeddings est remplacé par une simulation)
MEMORY_DESK_FAKE_EMBED=1 .venv/bin/python -m pytest -q tests
```

Jeux de données de test :

```bash
python tests/make_fixture.py /tmp/demo     # un petit jeu « humain »
python tests/make_stress.py  /tmp/stress   # ~55 enregistrements avec des cas limites
```

Les deux commandes affichent des lignes `export` avec les chemins — exécutez-les et lancez `python -m memory_desk` pour voir le bureau sur des données fictives.

Structure du dépôt :

```text
memory_desk/       serveur : API, file, recherche, synthèses, intégrations
frontend/          sources de l’interface (React + Vite)
memory_desk/web/   interface compilée (Node.js inutile pour l’exécution)
macos/             ébauche de l’application macOS (Swift) — jamais compilée
meetings_process.py   chaîne de traitement d’un enregistrement (CLI)
tests/             tests du serveur et de l’installateur, générateurs de données
docs/              fonctions, architecture, état d’avancement
install.command    installateur ; start.command — lancement du bureau
```

## Ce qui a été vérifié

- **Automatiquement** (`pytest`, 518 tests) : API et protection, filtres et recherche, fiche d’enregistrement, publication, tâches et décisions, analytique, synthèses du chat, compte rendu et carte, note de service, brief et courriel avec contrôle des horodatages, synthèse par projet, arbre des décisions, intégration du calendrier, point du matin, tâches bloquées et séries, modèles de notes et leur retour arrière, signets, suggestions de questions, export (PDF, DOCX, HTML, Markdown, PNG), robustesse des notes face aux modifications « hostiles », EventKit et encodeur vocal simulés, scénarios de l’installateur sur simulations, journal d’activité, verrouillage par PIN, dossiers vivants, langues de l’interface.
- **Dans un navigateur** (Chromium, données fictives) : tous les écrans en thème clair et sombre, un jeu de charge d’environ 55 enregistrements sans erreur JavaScript ni réponse 500 ; PDF, DOCX et images de carte examinés à l’œil (DOCX ouvert dans LibreOffice) ; la page du téléphone dans une fenêtre de 390×844.

**Non vérifié sur du vrai matériel** (l’environnement de développement n’a pas d’Apple Silicon) : transcription MLX Whisper et analyse Ollama via la nouvelle interface, qualité des réponses et synthèses de `qwen3:4b`, chargement du vrai modèle d’embeddings, lecture du Calendrier et des Rappels via EventKit, modèle vocal ECAPA et seuil de similarité, filtre Whisper sur de la vraie musique et du vrai bruit, ouverture des DOCX dans Word et Pages, installation sur un Mac vierge, coque de l’application macOS (jamais compilée), qualité des traductions. Faites le premier lancement sur un seul fichier court.

La liste complète des limites est dans [docs/STATUS.md](docs/STATUS.md) (en russe).

## Et ensuite

Le plan détaillé avec critères d’acceptation est dans [docs/ROADMAP.md](docs/ROADMAP.md) (en russe). Prochainement :

- un assistant de premier lancement et d’installation (choix et téléchargement des modèles, vérification des dépendances) ;
- l’**application macOS** (fenêtre, icône dans la barre des menus, Raccourcis) : une ébauche est dans [`macos/`](macos/README.md) ; elle n’a pas été compilée et attend une vérification sur Mac ; signature, notarisation et mise à jour automatique ne sont pas faites.

## Licence

[MIT](LICENSE).
