"""Explicit, schema-aware queue maintenance migrations."""

from controlplane.app import migrate_queue_indexes

if __name__ == "__main__":
    migrate_queue_indexes()
    print("queue indexes checked")
