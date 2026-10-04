import sys

from scns_guard.cli import main

raise SystemExit(main(["verify-receipts", *sys.argv[1:]]))
