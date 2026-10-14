"""Fail explicitly until a service has a real long-running entry point."""
import os
import sys


def main() -> int:
    service = os.environ.get("POWERCODE_SERVICE", "")
    if service not in {"collector", "detector", "trainer", "llm"}:
        print("Worker service is not configured.", file=sys.stderr)
        return 2
    print(f"{service}: long-running runtime is not implemented.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
