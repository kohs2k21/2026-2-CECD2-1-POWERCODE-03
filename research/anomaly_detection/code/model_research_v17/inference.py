"""Final research candidates: workload-conditioned temporal context."""
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT/"code"))
from model_research_v14.inference import ProcessResearchMonitor as OriginalMonitor
from model_research_v17.features import augment
from model_research_v17.run import OUTPUT
import model_research_v15.inference as exporter


class ProcessResearchMonitor(OriginalMonitor):
    def features(self, native, completions): return augment(super().features(native, completions))


def export():
    # Reuse verified exporter, but pickle canonical v17 monitor and final output.
    exporter.OUTPUT, exporter.ProcessResearchMonitor = OUTPUT, ProcessResearchMonitor
    exporter.export()
    from model_research_v13.data import read, write
    path = OUTPUT/"handoff/manifest.json"; manifest = read(path)
    manifest["requires_module"] = "model_research_v17.inference"
    manifest["temporal_source"] = "Count-conditioned residuals; no clean-prefix assumption"
    write(path, manifest)


if __name__ == "__main__":
    from model_research_v17.inference import export
    export()
