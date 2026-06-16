"""A/B experiment framework for meta-agent parameter tuning.

In-memory only; persistence is out of scope for Phase 4 step 1.

Usage::

    from jobhunt.ab import Experiment, Variant, ExperimentRegistry

    exp = Experiment(
        name="discovery_threshold",
        target="discovery",
        variants=[
            Variant("control", {"min_relevance": 0.5}),
            Variant("lower",   {"min_relevance": 0.4}),
        ],
    )
    variant = exp.assign(user_id)
    # … run job search …
    exp.record(variant.name, success=found_any_jobs)

    if exp.winner():
        print("Promote", exp.winner().name)
    if exp.should_rollback():
        print("Roll back to control")
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any


# ----------------------------------------------------------------- Variant


@dataclass
class Variant:
    """A single arm of an A/B experiment."""

    name: str
    params: dict[str, Any]
    impressions: int = 0
    successes: int = 0

    @property
    def success_rate(self) -> float:
        """Fraction of impressions that counted as successes.

        Returns 0.0 when there are no impressions.
        """
        if self.impressions == 0:
            return 0.0
        return self.successes / self.impressions

    # Serialisation helpers ---------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "params": self.params,
            "impressions": self.impressions,
            "successes": self.successes,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Variant":
        return cls(
            name=d["name"],
            params=d["params"],
            impressions=d.get("impressions", 0),
            successes=d.get("successes", 0),
        )


# --------------------------------------------------------------- Experiment


@dataclass
class Experiment:
    """An A/B experiment with deterministic assignment and automatic rollback.

    The first variant in *variants* is always treated as the **control**.

    Parameters
    ----------
    name:
        Unique experiment identifier.
    target:
        Which agent / parameter set this experiment affects.
    variants:
        At least two :class:`Variant` objects (index 0 = control).
    rollback_threshold:
        If a non-control variant's ``success_rate`` falls below this
        *fraction* of the control's ``success_rate`` (after
        ``min_impressions``), :meth:`should_rollback` returns ``True``.
    min_impressions:
        Minimum impressions required on each variant before significance
        or rollback can be declared.
    """

    name: str
    target: str
    variants: list[Variant]
    rollback_threshold: float = 0.5
    min_impressions: int = 10

    # ---------------------------------------------------------------- public

    def control(self) -> Variant:
        """First variant is always control."""
        return self.variants[0]

    def assign(self, key: str) -> Variant:
        """Deterministically assign *key* to a variant via hash.

        The same key always maps to the same variant.
        """
        digest = int(hashlib.md5(key.encode("utf-8")).hexdigest(), 16)  # noqa: S324
        return self.variants[digest % len(self.variants)]

    def record(self, variant_name: str, *, success: bool) -> None:
        """Increment counters for the named variant."""
        for v in self.variants:
            if v.name == variant_name:
                v.impressions += 1
                if success:
                    v.successes += 1
                return
        raise ValueError(f"Unknown variant {variant_name!r} in experiment {self.name!r}")

    def winner(self) -> Variant | None:
        """Return the best non-control variant if it meets significance criteria.

        Significance requires:
        - All variants have at least ``min_impressions`` impressions.
        - The best non-control variant's ``success_rate`` exceeds the
          control's by at least 0.10 (absolute).

        Returns ``None`` if no variant qualifies.
        """
        ctrl = self.control()
        if ctrl.impressions < self.min_impressions:
            return None
        best: Variant | None = None
        for v in self.variants[1:]:
            if v.impressions < self.min_impressions:
                return None  # wait until all variants have enough data
            if v.success_rate - ctrl.success_rate >= 0.10:
                if best is None or v.success_rate > best.success_rate:
                    best = v
        return best

    def should_rollback(self) -> bool:
        """Return ``True`` when a non-control variant is performing badly.

        A rollback is triggered when **any** non-control variant has at
        least ``min_impressions`` impressions AND its ``success_rate`` is
        less than ``rollback_threshold * control.success_rate``.

        The control's success_rate must also be > 0 for a meaningful ratio.
        """
        ctrl = self.control()
        if ctrl.impressions < self.min_impressions or ctrl.success_rate == 0.0:
            return False
        for v in self.variants[1:]:
            if v.impressions < self.min_impressions:
                continue
            if v.success_rate < self.rollback_threshold * ctrl.success_rate:
                return True
        return False

    # -------------------------------------------------------- serialisation

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "target": self.target,
            "variants": [v.to_dict() for v in self.variants],
            "rollback_threshold": self.rollback_threshold,
            "min_impressions": self.min_impressions,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Experiment":
        return cls(
            name=d["name"],
            target=d["target"],
            variants=[Variant.from_dict(v) for v in d["variants"]],
            rollback_threshold=d.get("rollback_threshold", 0.5),
            min_impressions=d.get("min_impressions", 10),
        )


# ------------------------------------------------------- ExperimentRegistry


class ExperimentRegistry:
    """In-memory store for experiments.

    Persistence (disk/DB) is out of scope for this phase.
    """

    def __init__(self) -> None:
        self._experiments: dict[str, Experiment] = {}

    def register(self, exp: Experiment) -> None:
        """Add or replace an experiment by name."""
        self._experiments[exp.name] = exp

    def get(self, name: str) -> Experiment | None:
        """Return the named experiment or ``None``."""
        return self._experiments.get(name)

    def all(self) -> list[Experiment]:
        """Return all registered experiments."""
        return list(self._experiments.values())

    # -------------------------------------------------------- serialisation

    def to_dict(self) -> dict[str, Any]:
        return {name: exp.to_dict() for name, exp in self._experiments.items()}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ExperimentRegistry":
        registry = cls()
        for exp_dict in d.values():
            registry.register(Experiment.from_dict(exp_dict))
        return registry
