import os

from whatsvault.crypto import atrest
from whatsvault.crypto.keystore import MemoryKeyStore
from whatsvault.db import connection as C

SENTINEL = b"WV_PLAINTEXT_SENTINEL_9e1f7c"


def test_db_file_does_not_contain_plaintext(tmp_path):
    ks = MemoryKeyStore()
    p = str(tmp_path / "v.db")
    conn = C.provision_db("vault", p, ks)
    conn.execute("CREATE TABLE t(x TEXT)")
    conn.execute("INSERT INTO t(x) VALUES(?)", (SENTINEL.decode(),))
    conn.commit()
    conn.close()
    for suffix in ("", "-wal", "-shm"):
        fp = p + suffix
        if os.path.exists(fp):
            with open(fp, "rb") as fh:
                assert SENTINEL not in fh.read(), f"plaintext sentinel leaked into {fp}"


def test_attachment_blob_is_ciphertext_on_disk(tmp_path):
    key = MemoryKeyStore().provision(atrest.ATTACHMENT_KEY_NAME, 32)
    blob = atrest.seal_blob(key, SENTINEL + b" media payload")
    fp = tmp_path / "att.bin"
    fp.write_bytes(blob)
    assert SENTINEL not in fp.read_bytes()
