"""Trusted legacy script-pickle compatibility, without changing frozen files.

Some v9/v10 research objects were authored by a script as __main__ classes.
Resolve only the explicitly known local classes while loading their VERIFIED
hashes. Re-exported objects use canonical importable module paths.
"""
from contextlib import contextmanager
import sys


@contextmanager
def legacy_classes():
    from model_research_v8 import bridge,refine
    main=sys.modules['__main__'];sentinel=object()
    aliases={'Bridge':bridge.Bridge,'Latent':bridge.Latent,'Family':refine.Family,
             'Detector':refine.Detector,'RefinedSettings':refine.RefinedSettings}
    saved={k:getattr(main,k,sentinel) for k in aliases}
    try:
        for k,v in aliases.items():setattr(main,k,v)
        yield
    finally:
        for k,v in saved.items():
            if v is sentinel:delattr(main,k)
            else:setattr(main,k,v)
