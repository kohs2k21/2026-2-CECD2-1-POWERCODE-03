"""Run the research pilot; published pickle loading requires explicit trust."""
import argparse
from pathlib import Path

from .manifest import create_manifest, file_digest, load_config, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("config.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--trust-published-models", action="store_true")
    args = parser.parse_args()
    if not args.trust_published_models:
        parser.error("Explicit --trust-published-models required for repository-owned pickle artifacts")
    config = load_config(args.config)
    manifest = create_manifest(config)
    args.output.mkdir(parents=True, exist_ok=False)
    receipt = args.output / "manifest.json"
    write_json(receipt, manifest)
    try:
        from .models import inspect_published_bundle
        manifest["published_reference_smoke"] = inspect_published_bundle(trusted=True)
        from .evaluate import run_experiment
        result = run_experiment(config, args.output)
        if result is not None:
            write_json(args.output / "results.json", result)
        manifest["status"] = "completed"
        manifest["outputs"] = {str(path.relative_to(args.output)): file_digest(path)
            for path in sorted(args.output.rglob("*")) if path.is_file() and path != receipt}
    except Exception as error:
        manifest["status"] = "failed"
        manifest["error_type"] = type(error).__name__
        raise
    finally:
        write_json(receipt, manifest)


if __name__ == "__main__":
    main()
