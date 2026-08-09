import pytest

from noteropdf.auth import (
    CredentialStore,
    CredentialStoreError,
    resolve_access_token,
)


class FakeKeyring:
    def __init__(self, *, fail: bool = False):
        self.value: str | None = None
        self.fail = fail

    def get_password(self, service: str, username: str) -> str | None:
        if self.fail:
            raise RuntimeError("unavailable")
        return self.value

    def set_password(self, service: str, username: str, value: str) -> None:
        if self.fail:
            raise RuntimeError("unavailable")
        self.value = value

    def delete_password(self, service: str, username: str) -> None:
        if self.fail:
            raise RuntimeError("unavailable")
        self.value = None


class _InsecureBackend:
    pass


_InsecureBackend.__module__ = "keyrings.alt.file"


class FakeKeyringModule(FakeKeyring):
    def __init__(self, backend):
        super().__init__()
        self.backend = backend

    def get_keyring(self):
        return self.backend


def test_token_round_trip_in_operating_system_keyring():
    keyring = FakeKeyring()
    store = CredentialStore(keyring_module=keyring)

    store.save("  ntn-personal-token  ")
    assert store.load() == "ntn-personal-token"
    assert keyring.value == "ntn-personal-token"


def test_empty_token_is_rejected():
    store = CredentialStore(keyring_module=FakeKeyring())

    with pytest.raises(ValueError, match="token is required"):
        store.save("  ")


def test_keyring_read_failure_does_not_fall_back_to_plaintext_storage():
    store = CredentialStore(keyring_module=FakeKeyring(fail=True))

    with pytest.raises(CredentialStoreError, match="Could not read"):
        store.load()


def test_keyring_write_failure_is_reported():
    store = CredentialStore(keyring_module=FakeKeyring(fail=True))

    with pytest.raises(CredentialStoreError, match="Could not save"):
        store.save("ntn-personal-token")


def test_plaintext_keyring_backend_is_rejected():
    store = CredentialStore(keyring_module=FakeKeyringModule(_InsecureBackend()))

    with pytest.raises(CredentialStoreError, match="not secure"):
        store.save("ntn-personal-token")


def test_unknown_keyring_backend_is_rejected():
    store = CredentialStore(keyring_module=FakeKeyringModule(object()))

    with pytest.raises(CredentialStoreError, match="cannot verify"):
        store.load()


def test_saved_token_is_resolved_from_keyring():
    store = CredentialStore(keyring_module=FakeKeyring())
    store.save("saved-token")

    assert resolve_access_token(store) == ("saved-token", "keyring")
