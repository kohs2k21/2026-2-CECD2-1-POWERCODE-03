"""Execute report notebook with the actual analysis environment's kernel."""
import sys
from pathlib import Path
import nbformat
from nbclient import NotebookClient
from jupyter_client import KernelManager
from ..common import ROOT


def main():
    path=Path(__file__).with_name('result.ipynb');notebook=nbformat.read(path,as_version=4)
    manager=KernelManager(kernel_name='python3');manager.kernel_spec.argv[0]=sys.executable
    try:
        NotebookClient(notebook,km=manager,timeout=300,resources={'metadata':{'path':str(ROOT)}}).execute()
        nbformat.validate(notebook)
        assert not any(o.output_type=='error' for c in notebook.cells if c.cell_type=='code' for o in c.outputs)
        nbformat.write(notebook,path)
    finally:
        if manager.has_kernel:manager.shutdown_kernel(now=True)
    print('Executed strict interval notebook; cells',len(notebook.cells),'errors 0',flush=True)


if __name__=='__main__':main()
