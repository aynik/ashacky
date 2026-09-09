"""Background children whose lifetime belongs to one VM session."""
import os
from pathlib import Path
import signal
import subprocess
import time


class UserServices:
    def __init__(self, commands, environment, logs):
        self.commands = commands
        self.environment = environment
        self.logs = Path(logs)
        self.logs.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.processes = {}
        self.cleaned = {}
        self.retries = {name: [] for name in commands}

    def start(self, name):
        with (self.logs / (name + '.log')).open('ab', buffering=0) as log:
            process = subprocess.Popen(self.commands[name], env=self.environment,
                stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        self.processes[name] = process

    def start_all(self):
        for name in self.commands:
            self.start(name)

    @staticmethod
    def signal_group(process, number):
        try:
            os.killpg(process.pid, number)
        except ProcessLookupError:
            pass

    def poll(self):
        now = time.monotonic()
        for name, process in list(self.processes.items()):
            if process.poll() is None:
                continue
            # Clean up a failed child's SSH descendants before replacing it.
            if self.cleaned.get(name) is not process:
                self.signal_group(process, signal.SIGTERM)
                self.cleaned[name] = process
            retries = self.retries[name] = [t for t in self.retries[name] if now - t < 60]
            if len(retries) >= 3 or (retries and now - retries[-1] < 10):
                continue
            retries.append(now)
            print(f'Restarting session service {name}: exit {process.returncode}', flush=True)
            self.start(name)

    def stop(self):
        for name, process in self.processes.items():
            if self.cleaned.get(name) is not process:
                self.signal_group(process, signal.SIGTERM)
        deadline = time.monotonic() + 5
        for process in self.processes.values():
            try:
                process.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                self.signal_group(process, signal.SIGKILL)
                process.wait()
        self.processes.clear()
