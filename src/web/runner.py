from __future__ import annotations

import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class RunState:
    running: bool = False
    started_at: str = ""
    finished_at: str = ""
    exit_code: int | None = None
    message: str = "尚未由網站手動執行"
    command: str = ""
    warning: str = ""


class MonitorRunner:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self._lock = threading.Lock()
        self._state = RunState()

    def status(self) -> RunState:
        with self._lock:
            return RunState(**self._state.__dict__)

    def start(self) -> tuple[bool, RunState]:
        with self._lock:
            if self._state.running:
                return False, RunState(**self._state.__dict__)
            python_exe, warning = self._python_executable()
            command = [
                str(python_exe),
                "main.py",
                "--products",
                "data\\AFC商品.csv",
                "--scheduled",
            ]
            self._state = RunState(
                running=True,
                started_at=datetime.now().isoformat(timespec="seconds"),
                message="監控執行中",
                command=" ".join(command),
                warning=warning,
            )
            thread = threading.Thread(target=self._run_command, args=(command,), daemon=True)
            thread.start()
            return True, RunState(**self._state.__dict__)

    def _python_executable(self) -> tuple[Path, str]:
        venv_python = self.project_root / ".venv" / "Scripts" / "python.exe"
        if venv_python.exists():
            return venv_python, ""
        return Path(sys.executable), ".venv Python 不存在，已改用目前 Python 執行"

    def _run_command(self, command: list[str]) -> None:
        log_dir = self.project_root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        dashboard_log = log_dir / "dashboard_run.log"
        with dashboard_log.open("a", encoding="utf-8") as log_file:
            log_file.write(f"[{datetime.now().isoformat(timespec='seconds')}] START\n")
            completed = subprocess.run(
                command,
                cwd=self.project_root,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                text=True,
            )
            log_file.write(
                f"[{datetime.now().isoformat(timespec='seconds')}] END code={completed.returncode}\n"
            )

        with self._lock:
            self._state.running = False
            self._state.finished_at = datetime.now().isoformat(timespec="seconds")
            self._state.exit_code = completed.returncode
            self._state.message = "監控完成" if completed.returncode == 0 else "監控失敗，請查看 log"

