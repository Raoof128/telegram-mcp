"""Storage: migrations, open path with integrity gates, closed settings."""

from comms.transports.telegram.storage.db import (
    SqliteCursorStore,
    StorageError,
    bind_cursor_store,
    bind_epoch_state,
    open_db,
    purge_expired_cursors,
    require_foreign_keys,
    save_epoch_state,
    startup_gc,
)
from comms.transports.telegram.storage.migrations import (
    MIGRATIONS,
    REQUIRED_INDEXES,
    SCHEMA_TABLES,
    SCHEMA_VERSION,
    Migration,
    current_version,
    migrate,
)
from comms.transports.telegram.storage.settings import (
    SETTINGS_REGISTRY,
    SettingSpec,
    all_settings,
    get_setting,
    set_setting,
    validate_setting,
)

__all__ = [
    "MIGRATIONS",
    "REQUIRED_INDEXES",
    "SCHEMA_TABLES",
    "SCHEMA_VERSION",
    "SETTINGS_REGISTRY",
    "Migration",
    "SettingSpec",
    "SqliteCursorStore",
    "StorageError",
    "all_settings",
    "bind_cursor_store",
    "bind_epoch_state",
    "current_version",
    "get_setting",
    "migrate",
    "open_db",
    "purge_expired_cursors",
    "require_foreign_keys",
    "save_epoch_state",
    "set_setting",
    "startup_gc",
    "validate_setting",
]
