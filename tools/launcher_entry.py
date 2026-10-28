"""PyInstaller entry for the stable launcher."""

if __name__ == "__main__":
    from qx_stc_isp.updates.launcher import main

    raise SystemExit(main())
