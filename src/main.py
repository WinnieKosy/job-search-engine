"""Start here.

    python src/main.py            -> opens the web interface (same as `jobagent ui`)
    python src/main.py apply URL  -> any `jobagent` command works here too

The real entry point is jobagent/cli.py:main(); `jobagent` in the terminal calls it.
"""
import sys

from jobagent.cli import main

if __name__ == "__main__":
    if len(sys.argv) == 1:
        sys.argv.append("ui")
    main()
