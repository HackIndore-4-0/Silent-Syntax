"""The SDK's own version string, in its own module so both
agentguard/__init__.py and agentguard/decorator.py (which records it onto
every Run — see agentguard/versioning.py) can import it without a
circular-import ordering dependency on __init__.py's own import order."""

__version__ = "0.2.0"
