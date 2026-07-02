#!/usr/bin/env python3
"""codecompass-fs CLI — multi-session code understanding agent.

Usage:
    python cli.py                       # interactive: list sessions → chat
    python cli.py --mode mock           # force mock model (no API key needed)
    python cli.py --session <id>        # jump straight to a session
    python cli.py --new "title here"    # create a new session then enter
    python cli.py --recover <id>        # recover a session (snapshot replay)
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

# Ensure the arf binding is importable when running this file directly.
_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "py-arf" / "python"))
sys.path.insert(0, str(_THIS_DIR))

from app import CodecompassApp


HELP_TEXT = """
Commands (during chat):
  /sessions         list all sessions
  /switch <id>      switch to a different session
  /delete <id>      delete a session
  /compact          run compaction on current session
  /delegate <task>  delegate a task to a subagent
  /peer <id> <msg>  send a peer_message to another session
  /quit             save snapshot and exit
  /help             show this help

Just type to chat.
"""


async def list_sessions_interactive(app: CodecompassApp) -> str | None:
    """Show the session list and let the user pick or create one."""
    sessions = await app.list_sessions()
    print("\n" + "=" * 70)
    print(" SESSIONS ".center(70, "─"))
    print("=" * 70)
    if not sessions:
        print("(no sessions yet)")
    else:
        for i, s in enumerate(sessions, 1):
            status = s.get("status", "active")
            rounds = s.get("round_count", 0)
            sid = s["session_id"]
            title = s.get("title", "(untitled)")
            print(f"  [{i:2d}] {sid[:24]:24s}  {title[:30]:30s}  rounds={rounds:3d}  {status}")
    print("  [N] + new session")
    print("  [Q] quit")
    print()

    while True:
        try:
            choice = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        if not choice:
            continue
        low = choice.lower()
        if low == "q":
            return None
        if low == "n":
            try:
                title = input("  title: ").strip() or "untitled"
            except (EOFError, KeyboardInterrupt):
                return None
            import uuid
            sid = f"sess-{uuid.uuid4().hex[:8]}"
            await app.start_session(sid, title)
            print(f"  → created {sid}")
            return sid
        # Try numeric
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(sessions):
                return sessions[idx]["session_id"]
        except ValueError:
            pass
        # Try direct session id
        if choice in {s["session_id"] for s in sessions}:
            return choice
        print("  (invalid, try again)")


async def chat_loop(app: CodecompassApp, session_id: str) -> None:
    """Main read-eval-print loop for one session."""
    sess = await app.session_store.get(session_id)
    title = sess.get("title", "(untitled)") if sess else "(unknown)"
    print(f"\n── Session: {session_id}  Title: {title} ──")
    if sess and sess.get("status") == "interrupted":
        print("[recovery] session was interrupted; replaying last checkpoint...")
        cp = sess.get("last_checkpoint")
        if cp:
            print(f"[recovery] last checkpoint: {cp.get('checkpoint')} turn={cp.get('turn_index')}")
    print(HELP_TEXT)

    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[bye] saving snapshot...")
            await app.session_store.snapshot(
                session_id, sess["state"] if sess else {},
                checkpoint="RoundEnd", turn_index=0,
            )
            return
        if not line:
            continue
        if line == "/help":
            print(HELP_TEXT)
            continue
        if line in ("/quit", "/q"):
            await app.session_store.snapshot(
                session_id, sess["state"] if sess else {},
                checkpoint="RoundEnd", turn_index=0,
            )
            return
        if line == "/sessions":
            await list_sessions_interactive(app)
            continue
        if line.startswith("/switch "):
            new_id = line.split(maxsplit=1)[1].strip()
            return await chat_loop(app, new_id)
        if line.startswith("/delete "):
            del_id = line.split(maxsplit=1)[1].strip()
            await app.delete_session(del_id)
            print(f"  → deleted {del_id}")
            continue
        if line == "/compact":
            result = await app.compactor.compact(session_id)
            print(f"  → compact: {result}")
            continue
        if line.startswith("/delegate "):
            task = line.split(maxsplit=1)[1]
            try:
                out = await app.delegate_to_subagent(session_id, task)
                print(f"  → subagent: {out}")
            except Exception as e:
                print(f"  → delegate error: {e}")
            continue
        if line.startswith("/peer "):
            parts = line.split(maxsplit=2)
            if len(parts) < 3:
                print("  usage: /peer <session_id> <message>")
                continue
            _, to_sid, content = parts
            try:
                out = await app.send_peer_message(session_id, to_sid, content)
                print(f"  → peer reply: {out}")
            except Exception as e:
                print(f"  → peer error: {e}")
            continue

        # Normal chat
        try:
            output = await app.chat(session_id, line)
            print(f"  << {output}")
        except Exception as e:
            print(f"  !! chat error: {e}")


async def main() -> int:
    parser = argparse.ArgumentParser(description="codecompass-fs CLI")
    parser.add_argument("--mode", default="mock", choices=["mock", "live"])
    parser.add_argument("--workdir", default=None)
    parser.add_argument("--session", default=None)
    parser.add_argument("--new", default=None, metavar="TITLE")
    parser.add_argument("--recover", default=None, metavar="SESSION_ID")
    args = parser.parse_args()

    workdir = Path(args.workdir) if args.workdir else None
    app = CodecompassApp(workdir=workdir, mode=args.mode)
    await app.start()
    try:
        if args.new:
            import uuid
            sid = f"sess-{uuid.uuid4().hex[:8]}"
            await app.start_session(sid, args.new)
            print(f"[cli] created new session: {sid}")
            await chat_loop(app, sid)
        elif args.session:
            await chat_loop(app, args.session)
        elif args.recover:
            print(f"[cli] recovering session: {args.recover}")
            await chat_loop(app, args.recover)
        else:
            chosen = await list_sessions_interactive(app)
            if chosen:
                await chat_loop(app, chosen)
    finally:
        await app.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
