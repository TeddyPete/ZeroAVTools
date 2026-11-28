"""Runs one TNT AV Net Tools function and prints its screen output as text.
Used by nettools.py:  python3 -m tntremote.netrun <module> <function> '<json kwargs>'"""
import importlib
import json
import sys
import threading


def main() -> int:
    module, func, kwargs = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
    from tntnet import ui
    mod = importlib.import_module(f"tntnet.{module}")
    out = ui.Output()
    err = []

    def run():
        try:
            getattr(mod, func)(out, **kwargs)
        except Exception as e:   # report, don't crash
            err.append(f"{type(e).__name__}: {e}")

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join()
    text = out.text()
    if out.status:
        text += f"\n[{out.status}]"
    if err:
        text += "\n[error] " + err[0]
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
