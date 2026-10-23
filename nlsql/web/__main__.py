"""Start the local website:  python -m nlsql.web   (or the shortcut command: nlsql-web)"""

import argparse
import webbrowser

import uvicorn


def main() -> None:
    p = argparse.ArgumentParser(prog="nlsql-web", description="Run the nlsql website on this computer.")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--no-browser", action="store_true", help="Do not open the browser automatically")
    args = p.parse_args()

    from .app import state

    state()  # connect to the database and read the schema now, so mistakes show up before the browser opens
    url = f"http://127.0.0.1:{args.port}"
    print(f"nlsql website running at {url}  (press Ctrl+C to stop)")
    if not args.no_browser:
        webbrowser.open(url)
    # 127.0.0.1 = only this computer can connect. Never change it to 0.0.0.0 without a login in front.
    uvicorn.run("nlsql.web.app:app", host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
