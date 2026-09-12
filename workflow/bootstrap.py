"""Create or migrate a paused installation without harvesting or submitting jobs."""
import json
import os


def initialize_state():
    from controlplane.app import initialize,settings
    from workflow.schema import migrate_queue,migrate_control
    from workflow.farm_control import write_paused
    cfg=settings()
    fresh=not cfg.queue_db.exists() and not cfg.control_db.exists()
    for directory in {cfg.queue_db.parent,cfg.control_db.parent,cfg.vault_key.parent}:
        directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    if cfg.control_db.exists() and cfg.control_db.stat().st_size and not cfg.vault_key.exists():
        raise RuntimeError('Existing control database has no vault key. Restore its original key before setup.')
    if fresh:
        write_paused('New installation: approve profile, resume, policy and portal sessions before resuming')
    queue_versions=migrate_queue(cfg.queue_db)
    control_versions=migrate_control(cfg.control_db)
    initialize()
    for path in (cfg.queue_db,cfg.control_db,cfg.vault_key):
        os.chmod(path,0o600)
    return {'initialized':True,'fresh_installation':fresh,'queue_migrations':queue_versions,'control_migrations':control_versions}


if __name__=='__main__':
    print(json.dumps(initialize_state()))
