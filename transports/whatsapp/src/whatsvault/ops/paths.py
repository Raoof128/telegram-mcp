"""Filesystem layout (5x-A). WHATSVAULT_HOME (default ~/.whatsvault) holds the two
SQLCipher databases, attachment blobs, runtime sockets, and logs. No secret material
lives here in plaintext — keys are Keychain-only."""

import os


class Paths:
    def __init__(self, home: str):
        self.home = home

    @property
    def vault_db(self):
        return os.path.join(self.home, "vault.db")

    @property
    def control_db(self):
        return os.path.join(self.home, "control.db")

    @property
    def blobs_dir(self):
        return os.path.join(self.home, "blobs")

    @property
    def run_dir(self):
        return os.path.join(self.home, "run")

    @property
    def logs_dir(self):
        return os.path.join(self.home, "logs")

    def all_dirs(self):
        return [self.home, self.blobs_dir, self.run_dir, self.logs_dir]

    def secret_files(self):
        return [self.vault_db, self.control_db]


def from_env(env=None) -> Paths:
    env = env if env is not None else os.environ
    home = env.get("WHATSVAULT_HOME") or os.path.join(os.path.expanduser("~"), ".whatsvault")
    return Paths(home)
