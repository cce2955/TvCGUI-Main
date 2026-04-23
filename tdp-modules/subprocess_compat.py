"""
subprocess_compat.py
--------------------
Helper so subprocess launches work correctly in both source and frozen EXE.

In source mode:   launches the companion .py file with sys.executable
In frozen mode:   re-launches the SAME EXE with --mode <name>
"""

import os
import sys


def frozen_exe(script_name: str) -> list[str]:
    """
    Return the correct Popen argv to launch a companion script/mode.

    Parameters
    ----------
    script_name : str
        One of: "master_overlay", "hud_overlay"

    Returns
    -------
    list[str]
        Ready to pass as argv to subprocess.Popen().
    """
    if getattr(sys, 'frozen', False):
        # Frozen: re-invoke the same EXE with --mode argument
        return [sys.executable, "--mode", script_name]
    else:
        # Source: invoke the .py file directly
        script_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            f"{script_name}.py"
        )
        return [sys.executable, script_path]


def base_dir() -> str:
    """
    Return the directory where data files (assets/, CSVs) live.

    In source:  folder containing launcher.py / main.py
    In frozen:  folder containing TvCGUI.exe
    """
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    else:
        return os.path.dirname(os.path.abspath(__file__))
