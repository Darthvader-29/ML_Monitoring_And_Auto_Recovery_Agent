"""Shared base for the model inference services (app factory + metrics).

NOT a sibling-service import: this is a shared base both services build on, so the
~290 lines that were copy-pasted between model_a and model_b live here once.
"""
