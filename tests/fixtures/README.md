# Safe Offline Fixtures

Fixture ini hanya untuk automated/offline validation. Tidak ada credential, cookie,
Stockbit session, Telegram token, akun, password, atau browser profile.

Production run default tetap memakai:

```text
DATA_SOURCE = LIVE
```

Fixture hanya boleh dipakai melalui argument/config eksplisit:

```text
--test-fixture
DATA_SOURCE = FIXTURE
```

