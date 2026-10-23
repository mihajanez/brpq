"""Entry point of the packaged Windows app: starts the GUI and opens a browser."""
import sys

from gui.server import main

if __name__ == "__main__":
    if "--open" not in sys.argv:
        sys.argv.append("--open")
    main()
