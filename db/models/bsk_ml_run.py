from sqlalchemy import Column, Integer, String, DateTime, Text, Float
from sqlalchemy.sql import func
from db.database import Base


class BskMlRun(Base):
    __tablename__ = "bsk_ml_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ran_at = Column(DateTime, nullable=False, server_default=func.now())
    rounds_used = Column(Integer, nullable=False, default=0)
    maps_updated = Column(Integer, nullable=False, default=0)
    maps_skipped = Column(Integer, nullable=False, default=0)
    status = Column(String(32), nullable=False, default="ok")
    triggered_by = Column(String(32), nullable=False, default="scheduler")
    notes = Column(Text, nullable=True)

    # ML prediction accuracy
    predictions_total = Column(Integer, nullable=True)
    predictions_correct = Column(Integer, nullable=True)
    prediction_accuracy = Column(Float, nullable=True)  # 0.0–1.0

    # Honest breakdown of how each map's weights were produced this run.
    #   data_driven  — ≥MIN_ROUNDS_PER_MAP rounds + per-map correlation usable
    #   rf_prior     — global RF was trained, map had no local data → forest.predict
    #   heuristic    — global RF unavailable, fell back to weights_from_features
    # Sum equals number of maps written to bsk_map_pool this run.
    maps_data_driven = Column(Integer, nullable=True)
    maps_rf_prior    = Column(Integer, nullable=True)
    maps_heuristic   = Column(Integer, nullable=True)

    # Global model training state.
    global_model_trained = Column(Integer, nullable=True)   # 0/1
    global_model_samples = Column(Integer, nullable=True)   # X_rows fed to RF

    # Quality of the global model:
    #   oob_r2               — mean out-of-bag R² across the 4 component forests,
    #                          in (-∞, 1]; >0 means the forest beats predicting
    #                          the mean. None if not enough OOB samples.
    #   feature_importances  — JSON {"top": [{"name", "imp"}…]} with the top-N
    #                          features by mean SSE-reduction across forests.
    oob_r2 = Column(Float, nullable=True)
    feature_importances = Column(Text, nullable=True)
