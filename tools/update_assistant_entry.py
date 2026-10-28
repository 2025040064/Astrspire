"""PyInstaller entry for the standalone update assistant."""

if __name__ == "__main__":
    from qx_stc_isp.updates.assistant import main

    raise SystemExit(main())
