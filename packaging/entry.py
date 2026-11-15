"""Entry point for the installed (PyInstaller) build of SophiCut."""
import multiprocessing
import os
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    # The installed app has no console window: send output to a log file instead.
    if sys.stdout is None or sys.stderr is None:
        logdir = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "SophiCut")
        os.makedirs(logdir, exist_ok=True)
        log = open(os.path.join(logdir, "sophicut.log"), "w", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = log

    from sophicut.app import main

    main()
