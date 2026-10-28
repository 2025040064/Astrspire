"""PyInstaller entry point for the safe preview desktop application."""

import os
import sys


def load_gui_main():
    """Load Qt with bundled/system DLLs, then preserve external tool discovery."""
    isolated = sys.platform == "win32" and getattr(sys, "frozen", False)
    previous_path = os.environ.get("PATH")
    if isolated:
        bundle = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        os.environ["PATH"] = os.pathsep.join((bundle, os.path.join(bundle, "PySide6"),
                                             os.path.join(system_root, "System32"), system_root))
    try:
        # Keep the explicit import so the packager discovers the full GUI.
        from qx_stc_isp.gui import main
        return main
    finally:
        if isolated:
            if previous_path is None:
                os.environ.pop("PATH", None)
            else:
                os.environ["PATH"] = previous_path

if __name__ == "__main__":
    from multiprocessing import freeze_support
    freeze_support()
    main = load_gui_main()
    raise SystemExit(main())
