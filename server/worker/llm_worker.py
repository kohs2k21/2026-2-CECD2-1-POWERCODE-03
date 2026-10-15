"""One GPU Base shared by recommendation adapters and requested explanations."""
import argparse
import os
from pathlib import Path
import threading
from dotenv import load_dotenv


def run(*, once=False):
    from trainer.runner import run as consume
    consume(once=once, role="llm")


def main():
    from generation_service import DesignModel, serve
    import generation_client
    load_dotenv(Path(__file__).parent / ".env")
    if len(os.environ.get("DESIGN_MODEL_TOKEN","")) < 32:
        raise ValueError("design_service_token_too_short")
    model = DesignModel(Path(os.environ["RESEARCH_BUNDLE_ROOT"]), Path(os.environ["RULE_MODEL_BASE_PATH"]))
    generation_client.set_local_model(model)
    server = serve(model, os.environ["DESIGN_MODEL_TOKEN"], host=os.environ.get("DESIGN_MODEL_HOST", "127.0.0.1"))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        run()
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="consume via externally configured model; test/maintenance")
    args = parser.parse_args()
    run(once=True) if args.once else main()
