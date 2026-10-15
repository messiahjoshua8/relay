#!/usr/bin/env python3
"""Sample application whose commands become agent skills."""

import argparse
import sys
import time


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ledger", description="Record and list entries.")
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add", help="Add an entry")
    add.add_argument("text", nargs="?", help="Entry text")
    add.add_argument("--from", dest="source", help="Read the entry text from a file")
    listing = sub.add_parser("list", help="List entries")
    listing.add_argument("--follow", action="store_true", help="Keep watching for new entries")
    args = parser.parse_args(argv)
    if args.command == "add":
        text = args.text
        if args.source:
            with open(args.source, encoding="utf-8") as handle:
                text = handle.read().strip()
        if not text:
            parser.error("add needs entry text or --from")
        print(f"added {text}")
        return 0
    print("entries: none")
    while args.follow:
        time.sleep(1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
