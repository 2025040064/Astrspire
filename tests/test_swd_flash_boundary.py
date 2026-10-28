from tools.run_f103_swd_experiment import flash_arguments


def test_flash_and_restore_never_use_hotplug_connection():
    args = flash_arguments("TEST123", "backup.bin")
    assert "mode=NORMAL" in args
    assert "mode=HOTPLUG" not in args
    assert args[-2:] == ["-v", "-rst"]
