"""Explicit, additive Rule B provider. Importing this package never loads a GPU."""
from .bridge import build_service_request, convert_suggestion, evaluate_draft
from .contract import build_messages, validate_input, validate_prediction

__all__ = ["build_service_request", "convert_suggestion", "evaluate_draft",
           "build_messages", "validate_input", "validate_prediction"]
