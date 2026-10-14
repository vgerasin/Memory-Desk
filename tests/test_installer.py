"""Установщик: логика проверяется на заглушках системных команд (macOS и Homebrew не нужны)."""
from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "install.command"
pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="нужен bash")


def _script(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/bash\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _sandbox(tmp: Path, arch: str = "arm64", ollama_has_model: bool = False, venv: bool = True, free_gb: int = 100) -> tuple[Path, Path, dict]:
    """Папка «проекта» с копией установщика и каталог заглушек; возвращает (проект, журнал вызовов, окружение)."""
    project = tmp / "project"
    project.mkdir()
    shutil.copy(INSTALLER, project / "install.command")
    (project / "install.command").chmod(0o755)
    (project / "requirements.txt").write_text("fastapi\n")
    calls = tmp / "calls.log"
    calls.write_text("")
    bin_dir = tmp / "bin"
    state = tmp / "state"
    state.mkdir()
    for name in ("start.command", "meeting-assistant", "meetings-process"):
        _script(project / name, f'echo "{name} $@" >> "{calls}"\n')
    _script(bin_dir / "uname", f'[ "$1" = "-m" ] && echo {arch} || echo Darwin\n')
    _script(bin_dir / "sw_vers", 'echo 14.5\n')
    _script(bin_dir / "sysctl", 'echo 17179869184\n')
    _script(bin_dir / "df", f'echo "Filesystem 1G-blocks Used Available"; echo "x 500 400 {free_gb}"\n')
    _script(bin_dir / "xattr", f'echo "xattr $@" >> "{calls}"\n')
    _script(bin_dir / "open", f'echo "open $@" >> "{calls}"\n')
    _script(bin_dir / "curl", f'[ -f "{state}/ollama-up" ] && exit 0 || exit 22\n')
    brew = (f'echo "brew $@" >> "{calls}"\n'
            f'case "$1" in\n'
            f'  list) [ -f "{state}/brew-$2" ] ;;\n'
            f'  install) touch "{state}/brew-$2" ;;\n'
            f'  --prefix) echo "{tmp}/brewprefix" ;;\n'
            f'  shellenv) ;;\n'
            f'esac\n')
    _script(bin_dir / "brew", brew)
    _script(tmp / "brewprefix" / "bin" / "python3.12",
            f'echo "python3.12 $@" >> "{calls}"\n'
            f'if [ "$1" = "-m" ] && [ "$2" = "venv" ]; then\n'
            f'  mkdir -p "$3/bin"; cp "{tmp}/venv-python" "$3/bin/python"; chmod +x "$3/bin/python"; fi\n')
    # python внутри .venv: все проверки импорта «успешны», загрузки моделей создают каталоги кеша
    _script(tmp / "venv-python",
            f'echo "venv-python $@" >> "{calls}"\n'
            f'if [ "$1" = "-m" ] && [ "$2" = "pip" ] && [ "$3" = "install" ]; then touch "{state}/pkgs"; fi\n'
            f'if [ "$1" = "-c" ] && echo "$2" | grep -q "import fastapi"; then [ -f "{state}/pkgs" ] && exit 0 || exit 1; fi\n'
            f'if [ "$1" = "-c" ] && echo "$2" | grep -q "import EventKit"; then [ -f "{state}/pkgs" ] && exit 0 || exit 1; fi\n'
            f'if [ "$1" = "-c" ] && echo "$2" | grep -q SentenceTransformer; then mkdir -p "$HF_CACHE/models--sentence-transformers--paraphrase-multilingual-MiniLM-L12-v2/snapshots/x"; fi\n'
            f'if [ "$1" = "-c" ] && echo "$2" | grep -q snapshot_download; then mkdir -p "$HF_HOME/hub/models--mlx-community--whisper-large-v3-turbo/snapshots/x"; fi\n'
            f'exit 0\n')
    if venv:
        (project / ".venv" / "bin").mkdir(parents=True)
        shutil.copy(tmp / "venv-python", project / ".venv" / "bin" / "python")
        (project / ".venv" / "bin" / "python").chmod(0o755)
    models_env = f'echo "ollama OLLAMA_HOST=$OLLAMA_HOST OLLAMA_MODELS=$OLLAMA_MODELS $@" >> "{calls}"\n'
    _script(bin_dir / "ollama", models_env +
            f'case "$1" in\n'
            f'  serve) touch "{state}/ollama-up"; sleep 0.2 ;;\n'
            f'  list) echo "NAME ID SIZE"; [ -f "{state}/model" ] && echo "qwen3:4b abc 2GB"; exit 0 ;;\n'
            f'  pull) touch "{state}/model"; echo pulled ;;\n'
            f'esac\n')
    if ollama_has_model:
        (state / "model").touch()
        (state / "ollama-up").touch()
    env = {**os.environ, "PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp)}
    return project, calls, env


def _run(project: Path, env: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(project / "install.command"), *args], cwd=project, env=env, text=True, capture_output=True, timeout=60)


def test_models_in_installer_match_code():
    text = INSTALLER.read_text(encoding="utf-8")
    from memory_desk import knowledge
    import meetings_process as mp
    # значения по умолчанию берём из исходника: другие тесты задают MEMORY_DESK_OLLAMA_URL в окружении
    cfg = (ROOT / "memory_desk" / "config.py").read_text(encoding="utf-8")
    default_url = re.search(r'"MEMORY_DESK_OLLAMA_URL",\s*"([^"]+)"', cfg).group(1)
    default_model = re.search(r'"MEMORY_DESK_OLLAMA_MODEL",\s*"([^"]+)"', cfg).group(1)
    assert f'EMBED_MODEL="{knowledge.EMBEDDING_MODEL}"' in text
    assert f'WHISPER_MODEL="{mp.WHISPER_MODEL}"' in text
    assert f'LLM_MODEL="{default_model}"' in text and mp.OLLAMA_MODEL == default_model
    port = re.search(r":(\d+)$", default_url).group(1)
    assert f'OLLAMA_ADDR="127.0.0.1:{port}"' in text
    # каталог моделей Ollama тот же, что у запускающих скриптов
    assert 'MODELS_DIR="$SCRIPT_DIR/ollama-models"' in text
    launcher = ROOT / "meeting-assistant"
    if launcher.exists():
        assert 'OLLAMA_MODELS="$SCRIPT_DIR/ollama-models"' in launcher.read_text(encoding="utf-8")


def test_fresh_install_runs_all_steps(tmp_path):
    project, calls, env = _sandbox(tmp_path, venv=False)
    result = _run(project, env, "--no-open")
    log = calls.read_text()
    assert result.returncode == 0, result.stdout + result.stderr
    for pkg in ("python@3.12", "ffmpeg", "ollama"):
        assert f"brew install {pkg}" in log
    assert "python3.12 -m venv" in log and (project / ".venv" / "bin" / "python").exists()
    assert "-m pip install -r" in log
    # Ollama поднимается на своём порту и с каталогом моделей проекта; модель тянется туда же
    assert f"OLLAMA_HOST=127.0.0.1:11435 OLLAMA_MODELS={project}/ollama-models serve" in log
    assert f"OLLAMA_HOST=127.0.0.1:11435 OLLAMA_MODELS= pull qwen3:4b" in log or "pull qwen3:4b" in log
    # модель поиска и распознавания лежат в кеше проекта
    assert (project / "huggingface-cache" / "models--sentence-transformers--paraphrase-multilingual-MiniLM-L12-v2" / "snapshots").exists()
    assert (project / "huggingface-cache" / "hub" / "models--mlx-community--whisper-large-v3-turbo" / "snapshots").exists()
    for folder in ("logs", "runtime", "webui/data", "work"):
        assert (project / folder).is_dir()
    assert "Готово" in result.stdout and "start.command" not in log  # --no-open


def test_second_run_skips_finished_work(tmp_path):
    project, calls, env = _sandbox(tmp_path, venv=False)
    assert _run(project, env, "--no-open").returncode == 0
    calls.write_text("")
    again = _run(project, env, "--no-open")
    log = calls.read_text()
    assert again.returncode == 0
    assert "brew install" not in log and "pull qwen3:4b" not in log and "-m venv" not in log and "-m pip install -r" not in log
    assert "уже загружена" in again.stdout


def test_install_opens_cabinet_at_the_end(tmp_path):
    project, calls, env = _sandbox(tmp_path, venv=False)
    result = _run(project, env)
    assert result.returncode == 0 and "start.command" in calls.read_text()


def test_check_mode_changes_nothing_and_reports(tmp_path):
    project, calls, env = _sandbox(tmp_path, venv=False)
    result = _run(project, env, "--check")
    log = calls.read_text()
    assert result.returncode == 1
    assert "brew install" not in log and "pull" not in log and "serve" not in log
    assert not (project / ".venv").exists() and not (project / "webui").exists()
    assert "✗" in result.stdout


def test_refuses_intel_and_low_disk(tmp_path):
    project, _, env = _sandbox(tmp_path, arch="x86_64")
    result = _run(project, env, "--no-open")
    assert result.returncode == 1 and "Apple Silicon" in result.stdout
    project2, _, env2 = _sandbox(tmp_path / "b", free_gb=3) if (tmp_path / "b").mkdir() is None else (None, None, None)
    result2 = _run(project2, env2, "--no-open")
    assert result2.returncode == 1 and "ГБ" in result2.stdout


def test_skip_models_flag(tmp_path):
    project, calls, env = _sandbox(tmp_path)
    result = _run(project, env, "--skip-models", "--no-open")
    log = calls.read_text()
    assert "pull" not in log and "snapshot_download" not in log
    assert result.returncode == 0 and "мастер первого запуска" in result.stdout   # моделей нет: это не ошибка, их скачает мастер в кабинете
    checked = _run(project, env, "--check")
    assert checked.returncode == 1 and "не загружена" in checked.stdout          # а проверка без изменений по-прежнему считает это проблемой
