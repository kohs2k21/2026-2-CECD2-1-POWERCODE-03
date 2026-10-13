"""Execute the NEW report notebook with the current Python; extract figures for QA.

Generated notebook outputs/PNG files are execution artifacts, not source edits.
No original data or pre-existing analysis notebooks are modified.
"""
import base64
import sys
from pathlib import Path
import nbformat
from nbclient import NotebookClient
from jupyter_client import KernelManager


def main():
    root = Path(__file__).resolve().parent
    path = root / "post_analysis.ipynb"
    nb = nbformat.read(path, as_version=4)
    manager = KernelManager(kernel_name="python3")
    manager.kernel_spec.argv = [sys.executable, "-m", "ipykernel_launcher", "-f", "{connection_file}"]
    client = NotebookClient(nb, km=manager, timeout=1800, resources={"metadata": {"path": str(root)}},
                            on_cell_start=lambda cell, cell_index: print(f"Cell {cell_index:02d}: {cell.cell_type}", flush=True))
    try:
        client.execute(cleanup_kc=True)
    finally:
        nbformat.write(nb, path)
        figures = root / "artifacts" / "figures" / "post_analysis_v2"
        figures.mkdir(parents=True, exist_ok=True)
        count = 0
        for i, cell in enumerate(nb.cells):
            for j, output in enumerate(cell.get("outputs", [])):
                if "image/png" in output.get("data", {}):
                    encoded = output.data["image/png"]
                    if isinstance(encoded, list):
                        encoded = "".join(encoded)
                    (figures / f"cell_{i:02d}_output_{j:02d}.png").write_bytes(base64.b64decode(encoded))
                    count += 1
        print(f"Saved notebook outputs; extracted {count} figures for visual QA", flush=True)
    print(f"Executed {path.name}: {len(nb.cells)} cells", flush=True)


if __name__ == "__main__":
    main()
