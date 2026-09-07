from .settings import *

DATABASES = {
    "default": {
        "ENGINE": "config.sqlite_test_backend",
        "NAME": ":memory:",
    }
}
