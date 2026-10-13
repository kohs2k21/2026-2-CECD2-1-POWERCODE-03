"""Validate and optionally execute every cell, without exporting analysis data.

Run with the Python interpreter containing the notebook dependencies:
    python code/validate_analysis_notebooks.py --execute
Use --embed-outputs to retain the verified figures and interpretations in each
notebook. This does not add data-export cells or create result CSV files.
"""

import argparse
import ast
import base64
from pathlib import Path
import sys
import time

import nbformat


FILES = ["transaction_analysis.ipynb", "processes_analysis.ipynb", "merge_analysis.ipynb", "rule_and_model_strategy_analysis.ipynb"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--notebook", choices=FILES)
    parser.add_argument("--embed-outputs", action="store_true")
    parser.add_argument("--preview-dir", type=Path)
    parser.add_argument("--eda-only", action="store_true",
                        help="Refresh EDA figures only; preserve an already validated model's outputs")
    args = parser.parse_args()
    if (args.embed_outputs or args.preview_dir or args.eda_only) and not args.execute:
        parser.error("--embed-outputs / --preview-dir / --eda-only require --execute")
    folder = Path(__file__).resolve().parent
    for name in ([args.notebook] if args.notebook else FILES):
        notebook = nbformat.read(folder / name, as_version=4)
        nbformat.validate(notebook)
        code_cells = [c for c in notebook.cells if c.cell_type == "code"]
        for cell in code_cells:
            ast.parse(cell.source)
            if any(token in cell.source for token in [".to_csv(", ".to_parquet(", ".savefig(", "분석 결과 저장"]):
                raise AssertionError(f"Unwanted result export: {name}")
        print(f"STRUCTURE PASS: {name}, {len(notebook.cells)} cells / {len(code_cells)} code cells", flush=True)
        if not args.execute:
            continue
        from jupyter_client import KernelManager
        from jupyter_client.kernelspec import KernelSpec
        from nbclient import NotebookClient
        manager = KernelManager(kernel_name="python3")
        manager._kernel_spec = KernelSpec(
            argv=[sys.executable, "-m", "ipykernel_launcher", "-f", "{connection_file}"],
            display_name="Analysis validation", language="python",
        )
        execution_notebook = notebook
        if args.eda_only:
            model_index = next(i for i, c in enumerate(notebook.cells)
                               if c.cell_type == "code" and c.source.startswith("MODEL_NUMERIC ="))
            if not notebook.cells[model_index].get("outputs"):
                raise AssertionError("EDA-only refresh requires previously executed model outputs")
            execution_notebook = nbformat.from_dict({**notebook, "cells": notebook.cells[:model_index]})
        start = time.monotonic()
        def finished(cell, cell_index, **_):
            print(f"CELL PASS: {name} [{cell_index}] {cell.source.splitlines()[0][:100]}", flush=True)
        client = NotebookClient(execution_notebook, km=manager, timeout=1800,
                                resources={"metadata": {"path": str(folder)}},
                                on_cell_executed=finished)
        try:
            client.execute()
            if args.eda_only:
                notebook.cells[:model_index] = execution_notebook.cells
            errors = [output for cell in notebook.cells if cell.cell_type == "code" for output in cell.outputs if output.output_type == "error"]
            if errors:
                raise AssertionError(errors)
            images = []
            for cell_index, cell in enumerate(notebook.cells):
                if cell.cell_type != "code":
                    continue
                for output_index, output in enumerate(cell.outputs):
                    if "image/png" in output.get("data", {}):
                        images.append((cell_index, output_index, output.data["image/png"]))
            if not images:
                raise AssertionError(f"No visible figures were rendered: {name}")
            if args.preview_dir:
                args.preview_dir.mkdir(parents=True, exist_ok=True)
                for cell_index, output_index, encoded in images:
                    target = args.preview_dir / f"{Path(name).stem}-{cell_index:03d}-{output_index:02d}.png"
                    target.write_bytes(base64.b64decode(encoded))
            if args.embed_outputs:
                for cell in notebook.cells:
                    if cell.cell_type == "code":
                        cell.metadata["scrolled"] = False
                        cell.metadata["jupyter"] = {"source_hidden": False, "outputs_hidden": False}
                nbformat.write(notebook, folder / name)
                print(f"EMBEDDED OUTPUTS: {name}, {len(images)} figures", flush=True)
            # Echo selected numerical results for a reviewable execution report.
            for cell in notebook.cells:
                if cell.cell_type == "code" and cell.source.startswith(("df, source_audit =", "transactions, transaction_audit =", "MODEL_NUMERIC =")):
                    for output in cell.outputs:
                        if output.output_type == "stream":
                            print(output.text.strip(), flush=True)
                        elif output.output_type in {"execute_result", "display_data"} and "text/plain" in output.data:
                            print(output.data["text/plain"][:5000], flush=True)
            mode = "EDA REFRESH" if args.eda_only else "EXECUTION"
            print(f"{mode} PASS: {name}, {time.monotonic() - start:.1f}s", flush=True)
        finally:
            if manager.has_kernel:
                manager.shutdown_kernel(now=True)


if __name__ == "__main__":
    main()
