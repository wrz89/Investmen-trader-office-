"""Certificato per il login Betfair "non interattivo" (quello che su betfair.it funziona senza abilitazioni).

Crea in runtime/betfair/ una chiave RSA 2048 e un certificato autofirmato (10 anni), li collega alle impostazioni
del bot e dice dove caricare il .crt sul conto Betfair. La chiave privata non lascia mai il PC.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .config import RUNTIME_DIR

DIR = RUNTIME_DIR / "betfair"


def create(overwrite: bool = False) -> dict:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    from . import local_settings
    DIR.mkdir(parents=True, exist_ok=True)
    crt, key = DIR / "client-2048.crt", DIR / "client-2048.key"
    if crt.exists() and key.exists() and not overwrite:
        created = False
    else:
        k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Bet_bot"),
                          x509.NameAttribute(NameOID.COUNTRY_NAME, "IT")])
        now = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(k.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=1))
                .not_valid_after(now + timedelta(days=3650))
                .add_extension(x509.KeyUsage(digital_signature=True, key_encipherment=True, content_commitment=False,
                                             data_encipherment=False, key_agreement=False, key_cert_sign=False,
                                             crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
                .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
                .sign(k, hashes.SHA256()))
        key.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                        serialization.NoEncryption()))
        crt.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        created = True
    s = local_settings.load()
    s["betfair"].update(cert_file=str(crt), key_file=str(key))
    local_settings.save(s)
    return {"created": created, "crt": str(crt), "key": str(key)}
