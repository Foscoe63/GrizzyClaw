#!/usr/bin/env python3
"""GrizzyClaw - 24/7 AI Assistant

Usage:
    grizzyclaw                    # Launch GUI (default)
    grizzyclaw gui                # Launch GUI explicitly
    grizzyclaw daemon start       # Start daemon service
    grizzyclaw daemon stop        # Stop daemon service
    grizzyclaw daemon status      # Check daemon status
    grizzyclaw daemon install     # Install as system service
    grizzyclaw daemon uninstall   # Uninstall system service
    grizzyclaw daemon run         # Run daemon in foreground (debug)
"""

import sys
from pathlib import Path


from grizzyclaw.cli import main

if __name__ == "__main__":
    main()
