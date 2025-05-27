#
# engines/git/credentials.py
#
"""
Enhanced Git credential management with comprehensive authentication support.
"""

import getpass
import os
from pathlib import Path
from typing import Any

import pygit2
import structlog

log = structlog.get_logger("engines.git.credentials")


class GitCredentialManager:
    """Manages Git authentication credentials with multiple fallback strategies."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.ssh_key_path = config.get("ssh_key_path")
        self.ssh_key_passphrase = config.get("ssh_key_passphrase")
        self.credential_helper = config.get("credential_helper")
        self._log = log.bind(component="CredentialManager")

    def get_credentials(
        self,
        url: str,
        username_from_url: str | None,
        allowed_types: int
    ) -> pygit2.credentials.Keypair | pygit2.credentials.KeypairFromAgent | pygit2.credentials.UserPass | pygit2.credentials.Username | None:
        """
        Comprehensive credential resolution with multiple strategies.

        Args:
            url: Git repository URL
            username_from_url: Username extracted from URL
            allowed_types: Allowed credential types bitmask

        Returns:
            Appropriate credential object or None
        """
        cred_log = self._log.bind(
            url=url,
            username_from_url=username_from_url,
            allowed_types=allowed_types
        )

        # Strategy 1: SSH Key Authentication
        if allowed_types & pygit2.GIT_CREDENTIAL_SSH_KEY:
            credentials = self._try_ssh_key_auth(url, username_from_url, cred_log)
            if credentials:
                return credentials

        # Strategy 2: SSH Agent
        if allowed_types & pygit2.GIT_CREDENTIAL_SSH_KEY:
            credentials = self._try_ssh_agent_auth(url, username_from_url, cred_log)
            if credentials:
                return credentials

        # Strategy 3: Username/Password (Environment Variables)
        if allowed_types & pygit2.GIT_CREDENTIAL_USERPASS_PLAINTEXT:
            credentials = self._try_userpass_auth(url, username_from_url, cred_log)
            if credentials:
                return credentials

        # Strategy 4: Default credentials
        if allowed_types & pygit2.GIT_CREDENTIAL_DEFAULT:
            cred_log.debug("Attempting default credential resolution")
            try:
                return pygit2.credentials.Username(
                    username_from_url or getpass.getuser()
                )
            except Exception as e:
                cred_log.debug("Default credentials failed", error=str(e))

        cred_log.warning("No suitable credentials found")
        return None

    def _try_ssh_key_auth(
        self,
        url: str,
        username_from_url: str | None,
        cred_log: Any
    ) -> pygit2.credentials.Keypair | None:
        """Attempt SSH key authentication."""
        if not self.ssh_key_path:
            return None

        try:
            ssh_user = username_from_url or "git"
            key_path = Path(self.ssh_key_path).expanduser()
            pub_key_path = key_path.with_suffix(key_path.suffix + ".pub")

            if not key_path.exists():
                cred_log.debug("SSH private key not found", path=str(key_path))
                return None

            if not pub_key_path.exists():
                cred_log.debug("SSH public key not found", path=str(pub_key_path))
                return None

            passphrase = self.ssh_key_passphrase or ""
            cred_log.debug("Attempting SSH key authentication",
                          user=ssh_user, key_path=str(key_path))

            return pygit2.credentials.Keypair(
                ssh_user,
                str(pub_key_path),
                str(key_path),
                passphrase
            )

        except Exception as e:
            cred_log.debug("SSH key authentication failed", error=str(e))
            return None

    def _try_ssh_agent_auth(
        self,
        url: str,
        username_from_url: str | None,
        cred_log: Any
    ) -> pygit2.credentials.KeypairFromAgent | None:
        """Attempt SSH agent authentication."""
        try:
            ssh_user = username_from_url or getpass.getuser()
            cred_log.debug("Attempting SSH agent authentication", user=ssh_user)

            return pygit2.credentials.KeypairFromAgent(ssh_user)

        except Exception as e:
            cred_log.debug("SSH agent authentication failed", error=str(e))
            return None

    def _try_userpass_auth(
        self,
        url: str,
        username_from_url: str | None,
        cred_log: Any
    ) -> pygit2.credentials.UserPass | None:
        """Attempt username/password authentication from environment."""
        git_username = os.getenv("GIT_USERNAME")
        git_password = os.getenv("GIT_PASSWORD") or os.getenv("GIT_TOKEN")

        if not git_username or not git_password:
            cred_log.debug("GIT_USERNAME or GIT_PASSWORD/GIT_TOKEN not set")
            return None

        try:
            cred_log.debug("Attempting username/password authentication",
                          username=git_username)
            return pygit2.credentials.UserPass(git_username, git_password)

        except Exception as e:
            cred_log.debug("Username/password authentication failed", error=str(e))
            return None


class RemoteCallbacks(pygit2.RemoteCallbacks):
    """Enhanced remote callbacks with comprehensive credential handling."""

    def __init__(self, credential_manager: GitCredentialManager) -> None:
        super().__init__()
        self.credential_manager = credential_manager
        self._attempts = 0
        self._max_attempts = 3
        self._log = log.bind(component="RemoteCallbacks")

    def credentials(
        self,
        url: str,
        username_from_url: str | None,
        allowed_types: int
    ) -> pygit2.credentials.Keypair | pygit2.credentials.KeypairFromAgent | pygit2.credentials.UserPass | pygit2.credentials.Username | None:
        """Handle credential requests with retry logic."""
        self._attempts += 1

        if self._attempts > self._max_attempts:
            self._log.error("Maximum credential attempts exceeded")
            return None

        self._log.debug("Credential request",
                       attempt=self._attempts,
                       url=url,
                       allowed_types=allowed_types)

        return self.credential_manager.get_credentials(
            url, username_from_url, allowed_types
        )

    def update_tips(self, refname: str, old_oid: pygit2.Oid, new_oid: pygit2.Oid) -> None:
        """Log reference updates."""
        self._log.debug("Reference updated",
                       ref=refname,
                       old_oid=str(old_oid)[:8],
                       new_oid=str(new_oid)[:8])

    def transfer_progress(self, stats) -> None:
        """Log transfer progress - removed TransferProgress type hint to avoid import issues."""
        if hasattr(stats, 'total_objects') and hasattr(stats, 'indexed_objects'):
            if stats.total_objects > 0:
                progress = (stats.indexed_objects / stats.total_objects) * 100
                self._log.debug("Transfer progress",
                               progress=f"{progress:.1f}%",
                               indexed=stats.indexed_objects,
                               total=stats.total_objects)
# 🔐🚀
