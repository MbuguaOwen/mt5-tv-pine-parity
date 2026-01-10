from __future__ import annotations

import argparse
from .bot_engine import run

def main() -> int:
    ap = argparse.ArgumentParser(prog="mt5_tv_pine_parity_bot")
    ap.add_argument("--config", default="configs/default.yaml", help="Path to YAML config")
    args = ap.parse_args()
    return run(args.config)

if __name__ == "__main__":
    raise SystemExit(main())
