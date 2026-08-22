from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .models import SubmissionEvidence


@dataclass(frozen=True)
class RecoveryAction:
    action_type: str
    intent: str
    input_ref: str | None
    target_kind: str
    postcondition_verified: bool


@dataclass(frozen=True)
class Recipe:
    id: str
    version: int
    adapter: str
    manifest_version: int
    document_json: str
    sha256: str
    status: str


class RecipeCompiler:
    """Promote sanitized successful recovery traces into immutable recipes."""

    def __init__(self, queue_path: str | Path):
        self.queue_path = str(queue_path)

    def promote(
        self,
        adapter: str,
        manifest_version: int,
        actions: list[RecoveryAction],
        *,
        evidence: SubmissionEvidence | None,
    ) -> Recipe:
        if evidence is None:
            raise ValueError("verified submission evidence is required")
        if not actions or not all(action.postcondition_verified for action in actions):
            raise ValueError("every recipe action requires a verified postcondition")
        if not adapter.strip() or manifest_version < 1:
            raise ValueError("adapter and positive manifest version are required")
        document = {
            "adapter": adapter,
            "manifest_version": manifest_version,
            "actions": [
                {
                    "type": action.action_type,
                    "intent": action.intent,
                    "input_ref": action.input_ref,
                    "target_kind": action.target_kind,
                    "postcondition": "verified",
                }
                for action in actions
            ],
            "submission_verifier": {
                "success_text": bool(evidence.success_text),
                "application_id": bool(evidence.application_id),
            },
        }
        document_json = json.dumps(document, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(document_json.encode()).hexdigest()
        recipe_id = f"{adapter}:default"
        db = sqlite3.connect(self.queue_path, timeout=30)
        try:
            db.execute("BEGIN IMMEDIATE")
            version = db.execute(
                "SELECT COALESCE(MAX(version),0)+1 FROM recipes WHERE id=?",
                (recipe_id,),
            ).fetchone()[0]
            db.execute(
                """INSERT INTO recipes
                (id,version,adapter,manifest_version,document_json,sha256,status,verified_at)
                VALUES(?,?,?,?,?,?,?,?)""",
                (
                    recipe_id,
                    version,
                    adapter,
                    manifest_version,
                    document_json,
                    digest,
                    "verified",
                    evidence.observed_at,
                ),
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        return Recipe(
            id=recipe_id,
            version=version,
            adapter=adapter,
            manifest_version=manifest_version,
            document_json=document_json,
            sha256=digest,
            status="verified",
        )
