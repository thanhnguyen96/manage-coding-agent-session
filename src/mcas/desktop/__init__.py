"""Desktop lifecycle integration for mcas (Windows tray, autostart, update, uninstall)."""

from .autostart import get_start_menu_lnk_path, set_autostart, test_autostart
from .launcher import open_ui, stop_ui, uninstall, update

__all__ = [
    "get_start_menu_lnk_path",
    "open_ui",
    "set_autostart",
    "stop_ui",
    "test_autostart",
    "uninstall",
    "update",
]
