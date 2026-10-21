"""Bounded subprocess execution, shared by probes and evidence collectors."""
import os
import signal
import subprocess
import threading
import time


def run(argv, *, env=None, cwd=None, timeout=15, max_bytes=1048576, stdin=None):
    if not 0 < timeout <= 120 or not 0 < max_bytes <= 8 * 1048576:
        raise ValueError("timeout must be 0–120 seconds; output limit 1–8388608 bytes")
    started = time.monotonic()
    output = {"stdout": bytearray(), "stderr": bytearray()}
    exceeded = threading.Event()
    lock = threading.Lock()
    total = 0
    try:
        proc = subprocess.Popen(
            argv, env=env, cwd=cwd, stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=os.name != "nt",
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )
    except OSError as exc:
        return {"status": "unavailable", "exit_code": None, "stdout": b"", "stderr": str(exc).encode(), "duration_ms": 0}

    def drain(name, pipe):
        nonlocal total
        try:
            while True:
                chunk = os.read(pipe.fileno(), 8192)
                if not chunk:
                    break
                with lock:
                    remaining = max(0, max_bytes - total)
                    output[name].extend(chunk[:remaining])
                    total += len(chunk)
                    if total > max_bytes:
                        exceeded.set()
        finally:
            pipe.close()

    threads = [threading.Thread(target=drain, args=(name, getattr(proc, name)), daemon=True) for name in output]
    for thread in threads:
        thread.start()

    def stop():
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
            else:
                os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, OSError, subprocess.TimeoutExpired):
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    status = "ok"
    original_term = None
    if threading.current_thread() is threading.main_thread():
        original_term = signal.getsignal(signal.SIGTERM)
        def terminate(_signum, _frame):
            raise KeyboardInterrupt
        signal.signal(signal.SIGTERM, terminate)
    try:
        if stdin is not None:
            # All supported request bodies are small; write in a thread so a
            # child refusing stdin cannot defeat the wall-clock deadline.
            def feed():
                try:
                    proc.stdin.write(stdin)
                    proc.stdin.close()
                except (BrokenPipeError, OSError):
                    pass
            threading.Thread(target=feed, daemon=True).start()
        while proc.poll() is None or any(t.is_alive() for t in threads):
            if exceeded.is_set():
                status = "output_limit"
                break
            if time.monotonic() - started >= timeout:
                status = "timeout"
                break
            time.sleep(0.01)
    except KeyboardInterrupt:
        status = "cancelled"
    finally:
        # Also reap descendants after a parent exits with inherited pipes open.
        stop()
        for thread in threads:
            thread.join(timeout=1)
        if original_term is not None:
            signal.signal(signal.SIGTERM, original_term)
    if exceeded.is_set() and status == "ok":
        status = "output_limit"
    if status == "ok" and proc.returncode:
        status = "failed"
    return {"status": status, "exit_code": proc.returncode,
            "stdout": bytes(output["stdout"]), "stderr": bytes(output["stderr"]),
            "duration_ms": round((time.monotonic() - started) * 1000)}
