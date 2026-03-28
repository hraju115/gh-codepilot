"""PTY lifecycle management — spawns Claude Code in a pseudo-terminal and bridges I/O to SocketIO."""

import fcntl
import os
import pty
import shutil
import signal
import struct
import termios

import eventlet
from eventlet import tpool

# Active PTY sessions keyed by SocketIO sid
active_ptys = {}


def spawn_claude(sid, socketio, cwd, resume_session_id=None):
    """Spawn Claude Code in a PTY at the given CWD. Optionally resume a session."""
    pid, fd = pty.fork()

    if pid == 0:
        # Child process
        os.chdir(str(cwd))
        env = os.environ.copy()
        env["TERM"] = "xterm-256color"

        cmd = [shutil.which("claude") or "claude"]
        if resume_session_id:
            cmd.extend(["--resume", resume_session_id])

        os.execvpe(cmd[0], cmd, env)
    else:
        # Parent process
        active_ptys[sid] = {"fd": fd, "pid": pid}

        # Start reader greenlet
        greenlet = eventlet.spawn(_pty_reader, sid, fd, socketio)
        active_ptys[sid]["greenlet"] = greenlet


def _pty_reader(sid, fd, socketio):
    """Background greenlet that reads PTY output and emits to the client."""
    while True:
        eventlet.sleep(0.01)
        try:
            data = tpool.execute(os.read, fd, 4096)
            if not data:
                break
            socketio.emit("pty_output", {"data": data.decode("utf-8", errors="replace")}, to=sid)
        except (OSError, IOError):
            break

    socketio.emit("pty_exit", {}, to=sid)
    _cleanup(sid)


def write_to_pty(sid, data):
    """Write user input to the PTY."""
    session = active_ptys.get(sid)
    if session:
        try:
            os.write(session["fd"], data.encode("utf-8"))
        except (OSError, IOError):
            pass


def resize_pty(sid, rows, cols):
    """Resize the PTY window."""
    session = active_ptys.get(sid)
    if session:
        try:
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(session["fd"], termios.TIOCSWINSZ, winsize)
        except (OSError, IOError):
            pass


def kill_pty(sid):
    """Kill the PTY process. Reader greenlet handles cleanup on OSError."""
    session = active_ptys.get(sid)
    if not session:
        return

    try:
        os.kill(session["pid"], signal.SIGHUP)
    except (OSError, ProcessLookupError):
        _cleanup(sid)


def _cleanup(sid):
    """Close fd and remove from active sessions."""
    session = active_ptys.pop(sid, None)
    if session:
        try:
            os.close(session["fd"])
        except OSError:
            pass
        try:
            os.waitpid(session["pid"], os.WNOHANG)
        except (OSError, ChildProcessError):
            pass
