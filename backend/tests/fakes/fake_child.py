"""Standalone child process used by process-manager tests."""

from __future__ import annotations

import argparse
import sys
import time


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exit", type=int, default=0)
    parser.add_argument("--sleep", type=float, default=0.0)
    parser.add_argument("--noisy", action="store_true")
    args = parser.parse_args()
    if args.noisy:
        for _ in range(2000):
            print("noise" * 20, flush=True)
    print("READY", flush=True)
    if args.sleep:
        time.sleep(args.sleep)
    sys.exit(args.exit)


if __name__ == "__main__":
    main()
