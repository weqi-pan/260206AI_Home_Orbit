import base64
import binascii
import os
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class InvalidMasterKey(RuntimeError):
    pass


@dataclass(frozen=True)
class EncryptedCredential:
    ciphertext: bytes
    nonce: bytes
    key_version: int
    last_four: str


class CredentialCipher:
    def __init__(self, encoded_key: str | None, key_version: int = 1):
        try:
            key = base64.b64decode(encoded_key or "", validate=True)
        except (binascii.Error, ValueError) as exc:
            raise InvalidMasterKey("LLM_CREDENTIAL_MASTER_KEY must be valid Base64") from exc
        if len(key) != 32:
            raise InvalidMasterKey("LLM_CREDENTIAL_MASTER_KEY must decode to exactly 32 bytes")
        self._cipher = AESGCM(key)
        self.key_version = key_version

    @staticmethod
    def aad(endpoint_id: str, protocol: str, key_version: int) -> bytes:
        return f"{endpoint_id}{protocol}{key_version}".encode("utf-8")

    def encrypt(self, endpoint_id: str, protocol: str, key_version: int, api_key: str) -> EncryptedCredential:
        nonce = os.urandom(12)
        ciphertext = self._cipher.encrypt(nonce, api_key.encode("utf-8"), self.aad(endpoint_id, protocol, key_version))
        return EncryptedCredential(ciphertext, nonce, key_version, api_key[-4:])

    def decrypt(self, endpoint_id: str, protocol: str, credential) -> str:
        plaintext = self._cipher.decrypt(
            credential.nonce,
            credential.ciphertext,
            self.aad(endpoint_id, protocol, credential.key_version),
        )
        return plaintext.decode("utf-8")


def master_key_is_valid(encoded_key: str | None) -> bool:
    try:
        CredentialCipher(encoded_key)
    except InvalidMasterKey:
        return False
    return True
