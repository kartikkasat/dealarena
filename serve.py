#!/usr/bin/env python3
"""Start DealArena: the compute API and the web interface on port 8007.

Usage:  python3 serve.py              -> http://localhost:8007  (API docs at /docs)
        python3 serve.py --port 9000
        python3 serve.py --local      -> bind to 127.0.0.1 only (no access from phones)
First time only:  python3 -m pip install -r backend/requirements.txt
"""
import argparse
import os
import socket
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # no packet is sent; picks the outbound interface
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def main():
    ap = argparse.ArgumentParser(description="Start the DealArena compute API and web interface")
    ap.add_argument("--port", type=int, default=8007)
    ap.add_argument("--local", action="store_true", help="bind to 127.0.0.1 only")
    args = ap.parse_args()
    if sys.version_info < (3, 10):
        sys.exit("DealArena needs Python 3.10 or later (found %d.%d)." % sys.version_info[:2])
    try:
        import uvicorn  # noqa: F401
        import fastapi  # noqa: F401
        import numpy  # noqa: F401
    except ImportError as exc:
        sys.exit(f"Missing package ({exc.name}). Install once with:\n  python3 -m pip install -r backend/requirements.txt")
    import uvicorn
    host = "127.0.0.1" if args.local else "0.0.0.0"
    print("DealArena is starting.")
    print(f"  This computer:  http://localhost:{args.port}     API docs: http://localhost:{args.port}/docs")
    ip = lan_ip()
    if not args.local and ip:
        print(f"  Same Wi-Fi:     http://{ip}:{args.port}   (open on a phone or tablet)")
    print("  Run logs:       logs/runs.jsonl and logs/runs/<run id>.json")
    print("Press Ctrl+C to stop.")
    uvicorn.run("backend.app:app", host=host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
