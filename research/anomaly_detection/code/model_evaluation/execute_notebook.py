"""Execute only the new comparison notebook and extract PNGs for visual QA."""
import base64
from pathlib import Path
import sys

import nbformat
from nbclient import NotebookClient
from jupyter_client import KernelManager


def main():
    folder = Path(__file__).resolve().parent
    target = folder/"results.ipynb"
    notebook = nbformat.read(target,as_version=4)
    manager = KernelManager(kernel_name="python3")
    manager.kernel_spec.argv = [sys.executable,"-m","ipykernel_launcher","-f","{connection_file}"]
    client = NotebookClient(notebook,km=manager,timeout=600,resources={"metadata":{"path":str(folder)}},
                            on_cell_start=lambda cell,cell_index:print(f"Cell {cell_index:02d}: {cell.cell_type}",flush=True))
    client.execute(cleanup_kc=True)
    # Saving execution outputs is a notebook artifact operation, not a source rewrite.
    nbformat.write(notebook,target)
    images = folder.parents[1]/"reports/model_evaluation_v2/figures"
    images.mkdir(parents=True,exist_ok=True)
    for i,cell in enumerate(notebook.cells):
        for j,output in enumerate(cell.get("outputs",[])):
            encoded = output.get("data",{}).get("image/png")
            if encoded:
                if isinstance(encoded,list): encoded="".join(encoded)
                (images/f"cell_{i:02d}_{j:02d}.png").write_bytes(base64.b64decode(encoded))
    print(f"Executed {len(notebook.cells)} cells; saved result notebook and visual QA PNGs",flush=True)


if __name__ == "__main__": main()
