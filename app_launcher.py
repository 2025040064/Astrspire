"""PyInstaller entry point for the safe preview desktop application."""

if __name__ == "__main__":
    from multiprocessing import freeze_support
    freeze_support()
    from qx_stc_isp.gui import main
    raise SystemExit(main())
