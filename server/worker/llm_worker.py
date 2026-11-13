"""One GPU Base shared by recommendation adapters and requested explanations."""
import argparse
import logging
import os
from pathlib import Path
import threading
from dotenv import load_dotenv


# 설명 전용 소비자가 예외로 끝났을 때 다시 시작하기까지 기다리는 시간.
EXPLANATION_RESTART_SECONDS = 5.0


def run(*, once=False):
    from trainer.runner import run as consume
    consume(once=once, role="llm")


def consume_explanations(stop: threading.Event) -> None:
    """설명 큐만 받는 두 번째 소비자. 예외는 기록하고 잠시 뒤 다시 시작한다.

    주 소비자(role llm)도 설명 큐를 교대로 받으므로 이 스레드가 멈춰도 설명 작업은 처리된다.
    """
    from trainer.runner import run as consume
    while not stop.is_set():
        try:
            consume(role="llm_explanation")
        except Exception:
            logging.getLogger(__name__).exception("explanation consumer failed; restarting")
        stop.wait(EXPLANATION_RESTART_SECONDS)


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
    # 두 소비자가 같은 모델을 쓰고 GPU 차례(GenerationGate)가 설명을 먼저 넣는다. 각자 DB 연결을 연다.
    stop = threading.Event()
    threading.Thread(target=consume_explanations, args=(stop,), name="llm-explanation", daemon=True).start()
    try:
        run()
    finally:
        stop.set()
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="consume via externally configured model; test/maintenance")
    args = parser.parse_args()
    run(once=True) if args.once else main()
