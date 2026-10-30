"""Execute the existing notebook using THIS Python environment, not a global one."""
import sys
from pathlib import Path
import nbformat
from nbclient import NotebookClient
from jupyter_client import KernelManager
from .common import ROOT


def main():
    path = Path(__file__).with_name("result.ipynb")
    nb = nbformat.read(path, as_version=4)
    # Mechanical cell-ID normalization; content is authored in the notebook.
    _, nb = nbformat.validator.normalize(nb)
    manager = KernelManager(kernel_name="python3")
    manager.kernel_spec.argv[0] = sys.executable
    try:
        client = NotebookClient(nb, km=manager, timeout=300,
                                resources={"metadata": {"path": str(ROOT)}})
        client.execute()
        nbformat.validate(nb)
        assert not any(output.output_type == "error" for cell in nb.cells if cell.cell_type == "code"
                       for output in cell.get("outputs", []))
        nbformat.write(nb, path)
    finally:
        if manager.has_kernel:
            manager.shutdown_kernel(now=True)
    executed = [c for c in nb.cells if c.cell_type == "code"]
    print("Notebook executed", len(executed), "code cells; errors=0; environment", sys.executable, flush=True)


if __name__ == "__main__":
    main()
