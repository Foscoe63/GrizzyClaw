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

# Ensure Homebrew paths are in PATH (bundled app may not inherit shell PATH)
import os
_paths = os.environ.get("PATH", "")
for _p in ("/opt/homebrew/bin", "/usr/local/bin"):
    if _p and _p not in _paths:
        _paths = _p + os.pathsep + _paths
os.environ["PATH"] = _paths

# Configure SSL certificates early (fixes API calls in PyInstaller-frozen macOS app)
import ssl
import sys
_ssl_cert_path = None
try:
    import certifi
    cert_path = certifi.where()
    if cert_path and os.path.exists(cert_path):
        _ssl_cert_path = os.path.abspath(cert_path)
    elif getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        bundle_cert = os.path.join(sys._MEIPASS, "certifi", "cacert.pem")
        if os.path.exists(bundle_cert):
            _ssl_cert_path = bundle_cert
    if _ssl_cert_path:
        os.environ["SSL_CERT_FILE"] = _ssl_cert_path
        os.environ["REQUESTS_CA_BUNDLE"] = _ssl_cert_path
        _path = _ssl_cert_path
        ssl._create_default_https_context = lambda p=_path: ssl.create_default_context(
            cafile=p
        )
except ImportError:
    pass

import sys
from pathlib import Path


from grizzyclaw.cli import main

if __name__ == "__main__":
    main()
