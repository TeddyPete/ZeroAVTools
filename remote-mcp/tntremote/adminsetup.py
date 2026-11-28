"""One-time Admin (sudo) setup.

The store package installs no sudo rule (store apps may not run as root). The
owner grants it once, either from the app (types the pi password, used once and
never saved) or from a terminal:

    sudo <cmd> --enable-admin      add the rule
    sudo <cmd> --disable-admin     take it away again

The rule lets this user run only the app's own root helper without a password;
the server still uses it only while Permissions > Admin (sudo) is switched on.
"""
from __future__ import annotations

import getpass
import os
import subprocess
import tempfile

from . import brand

ROOTRUN = brand.ROOTRUN
RULE_FILE = "/etc/sudoers.d/" + brand.CMD


def needed() -> bool:
    """True when this build ships without the rule (the owner sets it up)."""
    return bool(getattr(brand, "ADMIN_SETUP", False))


def enabled() -> bool:
    """The sudo rule is in place: the helper runs without a password."""
    if not os.path.exists(ROOTRUN):
        return False
    try:
        return subprocess.run(["sudo", "-n", "-l", ROOTRUN], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _rule(user: str) -> str:
    return (f"# {brand.APP_NAME}: Admin (sudo) set up by the owner. Lets {user} run only the\n"
            f"# {brand.APP_NAME} root helper without a password; used only while Admin is switched on.\n"
            f"# Remove with: sudo {brand.CMD} --disable-admin\n"
            f"{user} ALL=(root) NOPASSWD: {ROOTRUN}\n")


def _install_cmd(src: str) -> str:
    """Copy in as root under a dotted name (sudo ignores those), check it, then move it into place."""
    new = "/etc/sudoers.d/." + brand.CMD + ".new"
    return (f"install -m 0440 -o root -g root '{src}' '{new}' && visudo -cqf '{new}' "
            f"&& mv -f '{new}' '{RULE_FILE}' && visudo -cq || {{ rm -f '{new}'; exit 1; }}")


def enable(password: str | None = None) -> str:
    """Add the rule. As root (sudo --enable-admin) no password is needed; from the
    app the pi password is passed to sudo once on stdin and not kept. Returns '' or an error."""
    if not os.path.exists(ROOTRUN):
        return "the root helper is missing; reinstall " + brand.CMD
    user = os.environ.get("SUDO_USER") if os.geteuid() == 0 else getpass.getuser()
    if not user or user == "root":
        return "run it as: sudo " + brand.CMD + " --enable-admin (from the pi account)"
    fd, tmp = tempfile.mkstemp(prefix="admin-rule-")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(_rule(user))
        os.chmod(tmp, 0o644)
        cmd = _install_cmd(tmp)
        if os.geteuid() == 0:
            p = subprocess.run(["/bin/sh", "-c", cmd], capture_output=True, text=True, timeout=30)
        else:
            if not password:
                return "type the password first"
            # -k: always ask (ignore a cached sudo login); -S: read it from stdin; -p '': no prompt text
            p = subprocess.run(["sudo", "-k", "-S", "-p", "", "/bin/sh", "-c", cmd],
                               input=password + "\n", capture_output=True, text=True, timeout=30)
        if p.returncode != 0:
            err = (p.stderr or p.stdout or "").strip()
            if "incorrect password" in err or "Sorry, try again" in err or "no password was provided" in err:
                return "wrong password"
            if "not in the sudoers" in err or "may not run sudo" in err:
                return "this account is not allowed to use sudo"
            return err.splitlines()[-1] if err else f"failed ({p.returncode})"
        return "" if enabled() or os.geteuid() == 0 else "the rule was added but sudo does not accept it yet"
    except subprocess.TimeoutExpired:
        return "sudo did not answer"
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def disable() -> str:
    """Remove the rule. Uses the helper itself (allowed by the rule), or root directly."""
    cmd = f"rm -f '{RULE_FILE}'"
    try:
        if os.geteuid() == 0:
            p = subprocess.run(["/bin/sh", "-c", cmd], capture_output=True, text=True, timeout=30)
        elif enabled():
            p = subprocess.run(["sudo", "-n", ROOTRUN, cmd], capture_output=True, text=True, timeout=30)
        else:
            return ""   # nothing to remove (or not ours to reach)
    except (OSError, subprocess.SubprocessError) as e:
        return str(e)
    if p.returncode != 0:
        return (p.stderr or "").strip() or f"failed ({p.returncode})"
    return ""
