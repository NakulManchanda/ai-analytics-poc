from __future__ import annotations

import json
from pathlib import Path

from app.query_catalogue import lookup
from app.scenarios.models import ScenarioConfig

DEFAULT_SCENARIOS_DIR = Path("config/scenarios")


def load_scenario(
    scenario_path_or_name: str | Path, scenarios_dir: Path | None = None
) -> ScenarioConfig:
    """Load and validate a ScenarioConfig from a file path or scenario name."""
    path = Path(scenario_path_or_name)
    if not path.is_file():
        # Check in scenarios_dir or default directory
        search_dir = scenarios_dir or DEFAULT_SCENARIOS_DIR
        candidate = search_dir / f"{scenario_path_or_name}.json"
        if not candidate.is_file():
            candidate = search_dir / str(scenario_path_or_name)
        if not candidate.is_file():
            raise FileNotFoundError(
                f"Scenario not found: {scenario_path_or_name} (checked {path} and {candidate})"
            )
        path = candidate

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    return ScenarioConfig.model_validate(data)


def validate_catalogue_alignment(config: ScenarioConfig) -> list[str]:
    """Check whether scenario questions align with the query catalogue.

    Returns a list of warnings for questions that miss the catalogue.
    """
    warnings: list[str] = []
    for conv_idx, conv in enumerate(config.conversations):
        for turn_idx, turn in enumerate(conv.turns):
            hit = lookup(turn.question)
            if hit is None:
                warnings.append(
                    f"Conv {conv_idx + 1} Turn {turn_idx + 1} misses query catalogue: "
                    f"'{turn.question[:50]}...'"
                )
            elif turn.expected_tool and hit[0] != turn.expected_tool:
                warnings.append(
                    f"Conv {conv_idx + 1} Turn {turn_idx + 1} tool mismatch: "
                    f"expected '{turn.expected_tool}', catalogue has '{hit[0]}'"
                )
    return warnings
