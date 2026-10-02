"""
Paper Trading Blueprint

Read-only view of the paper trading engine: marked positions, equity curve,
fills, strategies and worker heartbeat.
Endpoints:
    GET /api/paper/state    - Full paper portfolio state
"""

from flask import Blueprint, jsonify

from services.paper import engine
from utils.logging import get_logger
from utils.session import check_session_validity

logger = get_logger(__name__)

paper_bp = Blueprint("paper_bp", __name__, url_prefix="/api/paper")


@paper_bp.route("/state", methods=["GET"])
@check_session_validity
def paper_state():
    """Current paper portfolio, marked to market."""
    try:
        return jsonify({"status": "success", "data": engine.get_state()})
    except Exception:
        logger.exception("Failed to read paper trading state")
        return jsonify(
            {
                "status": "error",
                "message": "The paper trading ledger could not be read. Try again in a moment.",
            }
        ), 500
