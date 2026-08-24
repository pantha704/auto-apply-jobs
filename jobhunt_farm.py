#!/usr/bin/env python3
"""Stop or resume the apply farm until an operator reactivates it.

  python jobhunt_farm.py status
  python jobhunt_farm.py pause
  python jobhunt_farm.py resume
  python jobhunt_farm.py shutdown   # workers + dashboard + probes

Does not delete queue, sessions, or proof.
"""
from __future__ import annotations

import argparse
import json
import sys

from workflow import farm_ops


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("status", "pause", "resume", "shutdown"))
    args = parser.parse_args()
    if args.command == "status":
        data = farm_ops.status()
    elif args.command == "pause":
        data = farm_ops.pause()
    elif args.command == "resume":
        data = farm_ops.resume()
    else:
        data = farm_ops.shutdown()
    print(json.dumps(data, indent=2))
    if data.get("paused") and args.command == "resume":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
