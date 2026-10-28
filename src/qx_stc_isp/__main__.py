"""`python -m qx_stc_isp` 入口。"""

if __name__ == "__main__":
    from multiprocessing import freeze_support
    freeze_support()
    from .gui import main
    raise SystemExit(main())
