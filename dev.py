#!/usr/bin/env python3
"""Dev helper for the dms claude-usage plugin.

The plugin lives in two places:
  - this repo (where you edit):  ~/github/dms-claude-usage
  - the live dms plugin dir:      ~/.config/DankMaterialShell/plugins/claudeUsage

dms does not follow symlinked plugin dirs, so the live copy must be a real
directory. This script keeps the two in sync and reloads dms.

Usage:
  ./dev.py sync        Copy repo -> live dir and reload dms (fast local loop)
  ./dev.py reload      Just reload the plugin in dms
  ./dev.py logs        Tail recent dms log lines for this plugin
  ./dev.py status      Show plugin status + git state of both copies
  ./dev.py publish -m "msg"
                       git commit -am MSG, push, then pull into the live dir
                       and reload (use for releases)
  ./dev.py link        (Re)create the live dir as a real clone from origin

Run from anywhere; paths are resolved relative to this file / $HOME.
"""
import argparse
import os
import shutil
import subprocess
import sys

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ID = "claudeUsage"
LIVE_DIR = os.path.join(
    os.environ["HOME"],
    ".config/DankMaterialShell/plugins", PLUGIN_ID)
# Files/dirs that make up the plugin (everything else in the repo is dev-only).
PLUGIN_PARTS = ["plugin.json", "ClaudeUsageWidget.qml", "scripts"]


def run(cmd, **kw):
    """Run a command, echoing it; return CompletedProcess."""
    print("  $", " ".join(cmd))
    return subprocess.run(cmd, **kw)


def dms(*args):
    """Call `dms ipc call plugins <args>` and return stdout (stripped)."""
    r = subprocess.run(["dms", "ipc", "call", "plugins", *args],
                       capture_output=True, text=True)
    return (r.stdout + r.stderr).strip()


def reload_plugin():
    print("Reloading plugin in dms…")
    out = dms("reload", PLUGIN_ID)
    print(" ", out)
    if "NOT_FOUND" in out:
        print("  Plugin not registered — restart dms first:")
        print("    systemctl --user restart dms.service")
        print("  then:  dms ipc call plugins enable", PLUGIN_ID)


def cmd_sync(_args):
    """Copy the plugin parts from repo -> live dir, then reload."""
    if not os.path.isdir(LIVE_DIR):
        print(f"Live dir missing: {LIVE_DIR}")
        print("Run `./dev.py link` first to create it.")
        return 1
    print(f"Syncing {REPO_DIR} -> {LIVE_DIR}")
    for part in PLUGIN_PARTS:
        src = os.path.join(REPO_DIR, part)
        dst = os.path.join(LIVE_DIR, part)
        if not os.path.exists(src):
            print(f"  ! missing in repo: {part}")
            continue
        if os.path.isdir(src):
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)
        print(f"  ✓ {part}")
    reload_plugin()
    return 0


def cmd_reload(_args):
    reload_plugin()
    return 0


def cmd_logs(_args):
    print(f"Recent dms logs mentioning the plugin / QML errors:\n")
    r = subprocess.run(
        ["journalctl", "--user", "-u", "dms.service", "--no-pager",
         "-n", "200"],
        capture_output=True, text=True)
    # Match this plugin's own QML file, its id, or generic load/error markers.
    keep = ("claudeusage", "plugin loaded", "plugin unloaded", "plugin error",
            "no such file")
    for line in r.stdout.splitlines():
        low = line.lower()
        hit = (any(k in low for k in keep)
               or ("claudeusagewidget.qml" in low)
               or (("binding loop" in low or "typeerror" in low
                    or "referenceerror" in low) and "claudeusage" in low))
        if hit:
            print(" ", line)
    return 0


def cmd_status(_args):
    print("Plugin status:")
    print(" ", dms("status", PLUGIN_ID) or "(no response)")
    for label, path in (("repo", REPO_DIR), ("live", LIVE_DIR)):
        print(f"\n{label}: {path}")
        if not os.path.isdir(os.path.join(path, ".git")):
            print("  (not a git repo)")
            continue
        head = subprocess.run(["git", "-C", path, "log", "--oneline", "-1"],
                              capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", path, "status", "-s"],
                               capture_output=True, text=True).stdout.strip()
        print("  HEAD:", head)
        print("  dirty:", "yes" if dirty else "no")
    return 0


def cmd_publish(args):
    if not args.message:
        print("publish needs -m/--message")
        return 1
    print("Committing & pushing repo…")
    run(["git", "-C", REPO_DIR, "add", "-A"])
    r = run(["git", "-C", REPO_DIR, "-c", "commit.gpgsign=false",
             "commit", "-m", args.message])
    if r.returncode != 0:
        print("  (nothing to commit, or commit failed — continuing)")
    run(["git", "-C", REPO_DIR, "push", "origin", "HEAD"])
    if os.path.isdir(os.path.join(LIVE_DIR, ".git")):
        print("Pulling into live dir…")
        run(["git", "-C", LIVE_DIR, "pull", "--ff-only"])
    else:
        print("Live dir is not a git clone; syncing files instead.")
        cmd_sync(args)
        return 0
    reload_plugin()
    return 0


def cmd_link(_args):
    """Create the live dir as a real clone of origin (replacing any symlink)."""
    origin = subprocess.run(
        ["git", "-C", REPO_DIR, "remote", "get-url", "origin"],
        capture_output=True, text=True).stdout.strip()
    if os.path.islink(LIVE_DIR):
        print("Removing existing symlink at live dir")
        os.unlink(LIVE_DIR)
    elif os.path.isdir(LIVE_DIR):
        print(f"Live dir already exists: {LIVE_DIR}")
        print("Remove it manually if you want a fresh clone.")
        return 1
    print(f"Cloning {origin} -> {LIVE_DIR}")
    run(["git", "clone", origin, LIVE_DIR])
    print("Now restart dms and enable:")
    print("  systemctl --user restart dms.service")
    print(f"  dms ipc call plugins enable {PLUGIN_ID}")
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sync", help="copy repo -> live dir and reload")
    sub.add_parser("reload", help="reload the plugin in dms")
    sub.add_parser("logs", help="tail recent plugin logs")
    sub.add_parser("status", help="plugin + git status")
    pub = sub.add_parser("publish", help="commit, push, pull into live, reload")
    pub.add_argument("-m", "--message", help="commit message")
    sub.add_parser("link", help="create live dir as a real clone")

    args = p.parse_args()
    handlers = {
        "sync": cmd_sync, "reload": cmd_reload, "logs": cmd_logs,
        "status": cmd_status, "publish": cmd_publish, "link": cmd_link,
    }
    return handlers[args.cmd](args) or 0


if __name__ == "__main__":
    sys.exit(main())
