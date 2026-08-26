"""Persistent signal outcome tracking and performance evaluation."""

# Install narrow actual-portfolio integrity guards without changing the
# baseline scoring/decision/lifecycle engines.
from modules.analytics.portfolio_integrity_overlay import install as _install_portfolio_integrity

_install_portfolio_integrity()
