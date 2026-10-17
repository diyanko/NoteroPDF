from __future__ import annotations

from typing import Any
from uuid import uuid4

from .profiles import normalize_profile, validate_credential_id

NOTION_TOKEN_PAGE_URL = "https://www.notion.so/developers/tokens"
KEYRING_SERVICE = "noteropdf"
KEYRING_USERNAME = "notion-personal-access-token"


class CredentialStoreError(RuntimeError):
    pass


class CredentialStore:
    """Store one Notion personal access token in the OS credential store."""

    def __init__(
        self,
        *,
        service: str = KEYRING_SERVICE,
        username: str = KEYRING_USERNAME,
        keyring_module: Any | None = None,
        profile: str | None = None,
        credential_id: str | None = None,
    ):
        profile = normalize_profile(profile)
        self._service = service
        self._username = f"{username}:profile:{profile}" if profile else username
        self._base_username = self._username
        if credential_id is not None:
            self._username += f":credential:{validate_credential_id(credential_id)}"
        self._keyring = keyring_module

    def _keyring_module(self) -> Any:
        if self._keyring is not None:
            return self._keyring
        try:
            import keyring  # type: ignore
        except Exception as exc:
            raise CredentialStoreError(
                "The operating-system credential store is unavailable."
            ) from exc
        return keyring

    def _validated_keyring_module(self) -> Any:
        module = self._keyring_module()
        get_keyring = getattr(module, "get_keyring", None)
        if not callable(get_keyring):
            # Small injected test doubles expose the password methods directly.
            return module
        try:
            backend = get_keyring()
        except Exception as exc:
            raise CredentialStoreError(
                "The operating-system credential store is unavailable."
            ) from exc
        self._reject_insecure_backend(backend)
        return module

    @classmethod
    def _reject_insecure_backend(cls, backend: Any) -> None:
        backend_type = type(backend)
        qualified_name = (
            f"{backend_type.__module__}.{backend_type.__name__}"
        ).casefold()
        insecure_markers = (
            "keyring.backends.fail",
            "keyring.backends.null",
            "keyrings.alt",
            "plaintextkeyring",
        )
        if any(marker in qualified_name for marker in insecure_markers):
            raise CredentialStoreError(
                "NoteroPDF requires Keychain, Credential Manager, Secret Service, "
                "or KWallet; the selected "
                f"keyring backend is not secure ({qualified_name})."
            )

        nested = getattr(backend, "backends", ())
        if "keyring.backends.chainer" in qualified_name and isinstance(
            nested, (list, tuple)
        ):
            if not nested:
                raise CredentialStoreError(
                    "No secure operating-system credential store is available."
                )
            for candidate in nested:
                cls._reject_insecure_backend(candidate)
            return

        supported_backends = (
            "keyring.backends.macos",
            "keyring.backends.windows",
            "keyring.backends.secretservice",
            "keyring.backends.libsecret",
            "keyring.backends.kwallet",
        )
        if not any(marker in qualified_name for marker in supported_backends):
            raise CredentialStoreError(
                "NoteroPDF cannot verify that the selected keyring backend uses the "
                f"operating-system credential store ({qualified_name})."
            )

    def load(self) -> str | None:
        try:
            keyring = self._validated_keyring_module()
            token = keyring.get_password(self._service, self._username)
        except CredentialStoreError:
            raise
        except Exception as exc:
            raise CredentialStoreError(
                "Could not read the Notion token from the operating-system "
                "credential store. Make sure Keychain, Credential Manager, or "
                "Secret Service/KWallet is available."
            ) from exc
        if not token:
            return None
        cleaned = token.strip()
        return cleaned or None

    def save(self, token: str) -> None:
        cleaned = token.strip()
        if not cleaned:
            raise ValueError("A Notion personal access token is required.")
        try:
            self._validated_keyring_module().set_password(
                self._service, self._username, cleaned
            )
        except CredentialStoreError:
            raise
        except Exception as exc:
            raise CredentialStoreError(
                "Could not save the Notion token in the operating-system "
                "credential store. Make sure Keychain, Credential Manager, or "
                "Secret Service/KWallet is available."
            ) from exc

    def stage(self, token: str) -> str:
        """Save a new generation without changing the active credential account."""
        credential_id = uuid4().hex
        try:
            self._generation(credential_id).save(token)
        except BaseException:
            try:
                self.discard(credential_id)
            except CredentialStoreError:
                pass
            raise
        return credential_id

    def discard(self, credential_id: str) -> None:
        """Remove only an uncommitted generation created by this setup run."""
        staged = self._generation(credential_id)
        try:
            staged._validated_keyring_module().delete_password(
                staged._service, staged._username
            )
        except Exception as exc:
            raise CredentialStoreError(
                "Could not remove the unused token from the credential store. "
                "The previous connection remains selected."
            ) from exc

    def _generation(self, credential_id: str) -> CredentialStore:
        return CredentialStore(
            service=self._service,
            username=self._base_username,
            keyring_module=self._keyring,
            credential_id=credential_id,
        )


def resolve_access_token(store: CredentialStore) -> tuple[str, str]:
    token = store.load()
    if token is None:
        return "", "missing"
    return token, "keyring"
