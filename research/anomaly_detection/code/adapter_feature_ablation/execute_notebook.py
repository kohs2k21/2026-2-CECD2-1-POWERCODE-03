"""Execute the report notebook with the active, verified research interpreter."""
from pathlib import Path
import json
import sys

import nbformat
from nbclient import NotebookClient
from jupyter_client import KernelManager


def main():
    folder = Path(__file__).resolve().parent
    path = folder/"result.ipynb"
    notebook = nbformat.read(path, as_version=4)
    manager = KernelManager(kernel_name="python3")
    manager.kernel_spec.argv = [sys.executable, "-m", "ipykernel_launcher", "-f", "{connection_file}"]
    client = NotebookClient(notebook, km=manager, timeout=180,
        resources={"metadata": {"path": str(folder)}},
        on_cell_start=lambda cell, cell_index: print(f"Cell {cell_index}: {cell.cell_type}", flush=True))
    client.execute(cleanup_kc=True)
    nbformat.write(notebook, path)
    audit_path = folder.parents[1]/"reports/adapter_feature_ablation/report_audit.json"
    record = json.loads(audit_path.read_text(encoding="utf-8"))
    record["notebook_executed"] = True
    record["executed_code_cells"] = sum(c.cell_type == "code" for c in notebook.cells)
    record["notebook_errors"] = len([o for c in notebook.cells if c.cell_type == "code"
                                    for o in c.outputs if o.output_type == "error"])
    audit_path.write_text(json.dumps(record, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(f"Executed {len(notebook.cells)} cells", flush=True)


if __name__ == "__main__":
    main()
