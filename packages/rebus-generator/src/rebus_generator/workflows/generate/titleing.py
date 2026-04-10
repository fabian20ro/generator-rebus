from __future__ import annotations

from rebus_generator.workflows.retitle.titleing import generate_title_for_final_puzzle_result


def generate_publication_title(puzzle, *, client, runtime, multi_model: bool):
    return generate_title_for_final_puzzle_result(
        puzzle,
        client=client,
        rate_client=client,
        runtime=runtime,
        multi_model=multi_model,
    )
