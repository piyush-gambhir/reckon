"""Internal probe runner; does not grant permission to execute a command."""
import sys
from reckonlib.process import run

if __name__ == "__main__":
    result = run(sys.argv[1:])
    sys.stdout.buffer.write(result["stdout"])
    sys.stderr.buffer.write(result["stderr"])
    if result["status"] != "ok":
        print("\nreckon: probe " + result["status"], file=sys.stderr)
    sys.exit(0 if result["status"] == "ok" else 1)
